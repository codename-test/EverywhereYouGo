#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""api/backup.py — 导出/备份/恢复/导入"""

import os
import io
import json
import shutil
import tempfile
import zipfile
import datetime as _dt

import db
import config_manager
import parser_loader
import channel_loader
import i18n
import log
import plugin_paths
from flask import Blueprint, request, jsonify, Response, send_file, current_app

backup_bp = Blueprint("backup", __name__)

PARSERS_DIR = plugin_paths.user_dir("parser")    # 兼容旧引用：只指用户目录
CHANNELS_DIR = plugin_paths.user_dir("channel")  # 用户通道插件目录
VERSION = "1.0.1"

# 恢复时解压总大小上限（防 ZIP 炸弹撑爆磁盘/volume）
MAX_RESTORE_SIZE = 10 * 1024 * 1024  # 10 MB

# 备份上传（HTTP body）大小上限（v1.3.2 review #11）。
# MAX_RESTORE_SIZE 限制的是**解压后**大小，但整个 ZIP 会先 file.read()
# 进内存 —— 所以还要限制**上传体积本身**。
# 第一层防护是全局 app.config["MAX_CONTENT_LENGTH"]（见 api/__init__.py），
# 这里做备份专用的友好报错。
MAX_BACKUP_UPLOAD_SIZE = 20 * 1024 * 1024  # 20 MB


def _safe_filename(fname):
    """校验解压/导入用的文件名，防路径穿越（#22）。

    合法备份内文件均为扁平结构（config/*.json、parsers/*.py），
    因此拒绝空名、以点开头、含路径分隔符或 '..' 的文件名。
    """
    if not fname:
        return False
    if fname.startswith("."):
        return False
    if "/" in fname or "\\" in fname or ".." in fname:
        return False
    return True


# ── Export helpers ──

# Export 脱敏的敏感字段关键字（密码/授权码/token/secret/webhook/device_key...）。
# 仅 Export（JSON 文件）脱敏；Backup zip 保留完整凭据以便恢复。
_SENSITIVE_KEYS = (
    "password", "auth_code", "token", "secret",
    "apikey", "api_key", "webhook", "device_key",
    "access_token", "appsecret", "client_secret",
)

def _mask_config(config):
    """Export 时对敏感字段脱敏（值隐藏为 ***），保留结构。"""
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except ValueError:
            return config
    if not isinstance(config, dict):
        return config
    return {
        k: "***" if any(p in k.lower() for p in _SENSITIVE_KEYS) else v
        for k, v in config.items()
    }

def _export_source(s):
    return {k: s[k] for k in ("id", "name", "port", "parser_id", "enabled", "created_at")}


def _export_parser(p):
    return {k: p[k] for k in ("id", "name", "filename", "description", "created_at")}


def _export_parser_with_code(p):
    data = _export_parser(p)
    fpath = plugin_paths.resolve("parser", p["filename"])   # 内置/用户都支持导出
    if fpath:
        with open(fpath, "r", encoding="utf-8") as f:
            data["code"] = f.read()
    else:
        data["code"] = ""
    return data


def _export_channel(c):
    return {
        "id": c["id"],
        "name": c["name"],
        "type": c["type"],
        "config": _mask_config(c["config"]),
        "enabled": bool(c["enabled"]),
        "created_at": c["created_at"],
    }


def _export_template(t):
    return {k: t[k] for k in ("id", "name", "engine", "title_tpl", "content_tpl", "created_at")}


@backup_bp.route("/api/export/all", methods=["GET"])
def api_export_all():
    config_items = db._conn().execute("SELECT * FROM system_config").fetchall()
    payload = {
        "version": VERSION,
        "exported_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "parsers": [_export_parser_with_code(p) for p in db.get_parsers()],
        "sources": [_export_source(s) for s in db.get_sources()],
        "channels": [_export_channel(c) for c in db.get_channels()],
        "templates": [_export_template(t) for t in db.get_templates()],
        "source_channels": db.get_all_source_channels(),
        "system_config": {r["key"]: r["value"] for r in config_items},
    }
    return jsonify(payload)


@backup_bp.route("/api/export/<item_type>/<int:item_id>", methods=["GET"])
def api_export_single(item_type, item_id):
    if item_type == "parser":
        item = db.get_parser(item_id)
        if not item:
            return jsonify({"error": i18n._("err.not_found")}), 404
        fpath = plugin_paths.resolve("parser", item["filename"])
        if not fpath:
            return jsonify({"error": i18n._("err.file_not_found_disk")}), 404
        return send_file(fpath, as_attachment=True, download_name=item["filename"])

    getter = {
        "source": (db.get_source, _export_source),
        "channel": (db.get_channel, _export_channel),
        "template": (db.get_template, _export_template),
    }
    pair = getter.get(item_type)
    if not pair:
        return jsonify({"error": i18n._("err.unknown_type").replace("{type}", item_type)}), 400
    fn_get, fn_export = pair
    item = fn_get(item_id)
    if not item:
        return jsonify({"error": i18n._("err.not_found")}), 404
    return jsonify(fn_export(item))


@backup_bp.route("/api/backup", methods=["GET"])
def api_backup():
    from config_manager import CONFIG_DIR, _CONFIG_FILES
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, fn in _CONFIG_FILES.items():
            p = os.path.join(CONFIG_DIR, fn)
            if os.path.isfile(p):
                zf.write(p, f"config/{fn}")
        # 只打包**用户**插件：内置随镜像发布，打进备份反而会在恢复时
        # 用旧副本遮蔽新版内置插件
        for kind, arc in (("parser", "parsers"), ("channel", "channels")):
            d = plugin_paths.user_dir(kind)
            if not os.path.isdir(d):
                continue
            for f in sorted(os.listdir(d)):
                if f.endswith(".py"):
                    zf.write(os.path.join(d, f), f"{arc}/{f}")
        zf.writestr("version.txt", VERSION)
    buf.seek(0)
    return Response(buf.getvalue(), mimetype="application/zip",
                    headers={"Content-Disposition": "attachment; filename=ego_backup.zip"})


# ── 恢复校验（v1.3.2 review #9 / #10）────────────────────────────


def _core_config_entries():
    """核心快照文件在 ZIP 里的路径（config/*.json），取自 config_manager。

    只要求"已知的这几个"存在，**不限制** ZIP 里有多余文件 ——
    将来若新增配置文件，旧 ZIP 仍可恢复（向前兼容）。
    """
    from config_manager import _CONFIG_FILES
    return ["config/%s" % fn for fn in _CONFIG_FILES.values()]


def _missing_core_files(zf_names):
    """返回 ZIP 里缺失的核心快照文件列表（空列表 = 完整）。

    缺文件**不拒绝**，只提示：备份里有什么就恢复什么，未包含的表保持原样
    （由 config_manager.import_from_json 保证"没文件就不动那张表"）。
    """
    present = set(zf_names)
    return [n for n in _core_config_entries() if n not in present]


def _stage_and_validate(zf, config_files, parser_files, channel_files, tmp_root):
    """把 ZIP 内容落到临时目录并逐项校验。**不写任何最终位置**。

    返回 (staged, errors, skipped_builtin, restored)。
    校验覆盖：
      - 文件名安全（防路径穿越，#22）
      - 配置 JSON 可解析 + **结构合法**（list 形状 / 必需字段）
      - 插件可加载（parser 语法 / channel 可实例化）
    """
    from config_manager import CONFIG_DIR, _CONFIG_FILES

    fname_to_key = {fn: key for key, fn in _CONFIG_FILES.items()}
    errors = []
    staged = []
    skipped_builtin = []
    restored = {"parser": [], "channel": []}

    # 配置 JSON：写临时 + 解析 + 结构校验
    for name in config_files:
        if not name.endswith(".json"):
            continue
        fname = os.path.basename(name[len("config/"):])
        if not _safe_filename(fname):
            errors.append(f"config/{fname}: 非法文件名")
            continue
        tpath = os.path.join(tmp_root, "config", fname)
        with open(tpath, "wb") as f:
            f.write(zf.read(name))
        try:
            with open(tpath, "r", encoding="utf-8") as f:
                data = json.loads(f.read())
        except Exception as e:
            errors.append(f"config/{fname}: JSON 解析失败 {str(e)[:100]}")
            continue
        # 结构校验（review #9）：原先只在导入时 log.warning —— 形状错了要等
        # 导入阶段 KeyError 才炸，而那时**插件文件已经替换完了**，会留半成品。
        key = fname_to_key.get(fname)
        if key:
            verrs = config_manager._validate_config(key, data)
            if verrs:
                errors.append(f"config/{fname}: " + "; ".join(verrs[:3]))
                continue
        staged.append((tpath, os.path.join(CONFIG_DIR, fname), fname, "config"))

    # 插件：写临时 + 校验可加载；与内置同名条目跳过（内置随镜像更新）
    plugin_paths.ensure_user_dirs()
    for names, kind, prefix in ((parser_files, "parser", "parsers/"),
                                (channel_files, "channel", "channels/")):
        kind_dir = kind + "s"
        udir = plugin_paths.user_dir(kind)
        for name in names:
            if not name.endswith(".py"):
                continue
            fname = os.path.basename(name[len(prefix):])
            if not _safe_filename(fname):
                errors.append(f"{kind}/{fname}: 非法文件名")
                continue
            if plugin_paths.is_builtin(kind, fname):
                skipped_builtin.append(fname)
                continue
            tpath = os.path.join(tmp_root, kind_dir, fname)
            with open(tpath, "wb") as f:
                f.write(zf.read(name))
            if kind == "parser":
                err = parser_loader.validate_parser(tpath)
            else:
                err = channel_loader.validate_channel(tpath)
            if err:
                errors.append(f"{kind}/{fname}: {err[:120]}")
                continue
            staged.append((tpath, os.path.join(udir, fname), fname, kind))
            restored[kind].append(fname)

    return staged, errors, skipped_builtin, restored


@backup_bp.route("/api/restore", methods=["POST"])
def api_restore():
    from config_manager import CONFIG_DIR
    if "file" not in request.files:
        return jsonify({"ok": False, "error": i18n._("err.upload_file_required")})

    file = request.files["file"]
    dry_run = request.args.get("dry_run") == "1"

    # 上传体积上限（v1.3.2 review #11）：必须在 file.read() 之前判断，
    # 否则超大文件已经整个进内存了。dry_run 同样受限。
    clen = request.content_length
    if clen is not None and clen > MAX_BACKUP_UPLOAD_SIZE:
        return jsonify({"ok": False,
                        "error": i18n._("err.upload_too_large").replace(
                            "{size}", str(MAX_BACKUP_UPLOAD_SIZE // (1024 * 1024)))})

    try:
        zf = zipfile.ZipFile(io.BytesIO(file.read()))
    except Exception as e:
        return jsonify({"ok": False, "error": f"{i18n._('err.zip_parse_fail')} {e}"})

    names = zf.namelist()
    config_files = [n for n in names if n.startswith("config/")]
    parser_files = [n for n in names if n.startswith("parsers/")]
    channel_files = [n for n in names if n.startswith("channels/")]

    result = {"ok": True, "dry_run": dry_run, "config": config_files,
              "parsers": parser_files, "channels": channel_files,
              "warnings": []}

    # dry-run 不再"读个文件名就返回"：它走**同一套**校验（体积 + 完整性 +
    # JSON 结构 + 插件可加载），只是不落最终位置 —— 这样 WebUI 的"预览"
    # 才能真正回答"这个备份能不能恢复"（v1.3.2 review #10）。
    errors = []

    # 1) 完整性**提示**（v1.3.2 review #9）：缺核心配置文件不拒绝。
    #    备份里有什么就恢复什么；未包含的表保持原样
    #    （"没文件就不动那张表"由 config_manager.import_from_json 保证）。
    missing = _missing_core_files(names)
    if missing:
        result["warnings"].append(
            i18n._("warn.restore_partial").replace("{files}", ", ".join(missing)))

    # 2) 防 ZIP 炸弹：累计未压缩大小，超过上限即拒绝（#32）
    total_size = 0
    for name in config_files + parser_files + channel_files:
        total_size += zf.getinfo(name).file_size
        if total_size > MAX_RESTORE_SIZE:
            zf.close()
            errors.append(i18n._("err.restore_too_large"))
            result.update({"ok": False, "errors": errors,
                           "error": i18n._("err.restore_too_large")})
            return jsonify(result)

    # ── 原子恢复（#9）：全量落临时目录 + 校验，全通过才统一替换；
    # 任一失败 → 原文件不动，无半成功 ──
    tmp_root = tempfile.mkdtemp(prefix="ego_restore_")
    for d in ("config", "parsers", "channels"):
        os.makedirs(os.path.join(tmp_root, d), exist_ok=True)
    try:
        staged, stage_errors, skipped_builtin, restored = _stage_and_validate(
            zf, config_files, parser_files, channel_files, tmp_root)
        zf.close()

        if skipped_builtin:
            result["skipped_builtin"] = skipped_builtin

        if errors or stage_errors:
            errors.extend(stage_errors)
            result.update({"ok": False, "errors": errors,
                           "error": i18n._("err.restore_validate_failed")
                           + " " + "; ".join(errors[:3])})
            log.logger.warning(f"[Restore] validation failed, nothing written: {errors[:5]}")
            return jsonify(result)

        if dry_run:
            # 校验通过 → 告诉前端"可以恢复"，并回报将要写入的文件
            # （warnings 已在 result 里，前端会展示"部分是部分恢复"的提示）
            result["staged"] = {
                "config": [f for _t, _fn, f, k in staged if k == "config"],
                "parser": restored["parser"],
                "channel": restored["channel"],
            }
            result["errors"] = []
            return jsonify(result)

        # 全通过 → 统一原子替换到最终位置。
        # 说明：校验阶段是"全原子"的；提交阶段是逐文件 os.replace()，
        # 若中途失败（磁盘错误等）会出现"部分已替换"。这里做 best-effort
        # rollback：替换前备份旧文件，失败时反向恢复（v1.3.2 review #10）。
        replaced = []          # [(final_path, backup_path_or_None)]
        try:
            for tpath, final, fname, kind in staged:
                backup = None
                if os.path.exists(final):
                    backup = final + ".restore.bak"
                    shutil.copyfile(final, backup)
                tmpfinal = final + ".restore.tmp"
                shutil.copyfile(tpath, tmpfinal)
                os.replace(tmpfinal, final)
                replaced.append((final, backup))
                log.logger.info(f"Restore: placed {kind}/{fname}")
        except Exception as e:
            log.logger.error(f"[Restore] commit failed, rolling back: {e}")
            rollback_errors = []
            for final, backup in reversed(replaced):
                try:
                    if backup:
                        os.replace(backup, final)   # 恢复旧文件
                    else:
                        os.unlink(final)            # 原不存在 → 删除新写入的
                except Exception as re:
                    rollback_errors.append(f"{os.path.basename(final)}: {str(re)[:100]}")
            for _, backup in replaced:              # 清理未使用的备份
                if backup and os.path.exists(backup):
                    try:
                        os.unlink(backup)
                    except OSError:
                        pass
            zf.close()
            return jsonify({"ok": False,
                            "error": i18n._("err.restore_commit_failed") + " " + str(e)[:200],
                            "rollback_errors": rollback_errors})
        # 提交成功 → 删除备份临时文件
        for _, backup in replaced:
            if backup and os.path.exists(backup):
                try:
                    os.unlink(backup)
                except OSError:
                    pass

        if skipped_builtin:
            log.logger.info(f"Restore: skipped built-in plugin(s): {skipped_builtin}")
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


    # ── 配置恢复语义（v1.3.2 review #8）：Restore 要求「JSON → DB」无条件覆盖。
    # 不能调 load_all()——它在 DB 非空时以 DB 为准反向刷回 JSON，
    # 会把刚恢复的配置覆盖掉，使"配置恢复"变成 no-op。 ──
    try:
        import config_manager
        # 只允许覆盖**备份里确实带了**的配置文件对应的表。
        # 不能按"磁盘上有没有"判断 —— config/ 目录里通常已经有上一轮导出的
        # 旧文件，会把陈旧内容当成备份内容导入（实测踩到过）。
        from config_manager import _CONFIG_FILES
        in_zip = {os.path.basename(n) for n in config_files}
        present_keys = [k for k, fn in _CONFIG_FILES.items() if fn in in_zip]
        counts, skipped_tables = config_manager.import_from_json(only=present_keys)
        result["config_imported"] = counts
        if skipped_tables:
            # 备份未包含这些配置 → 对应的表原样保留（已在 warnings 里提示）
            result["config_skipped"] = skipped_tables
        # 备份可能来自更旧版本，内置解析器随镜像新增；重跑幂等登记，
        # 避免恢复旧备份后 parsers 表丢了新内置解析器。
        try:
            added = db.sync_builtin_parsers()
            if added:
                result["builtin_parsers_added"] = added
        except Exception as e:
            log.logger.warning(f"[Restore] sync_builtin_parsers failed: {e}")
    except Exception as e:
        # import_from_json 是事务性的：失败时 DB 未被改动
        result["ok"] = False
        result["config_error"] = str(e)
        log.logger.error(f"[Restore] config import failed: {e}")
        return jsonify(result)

    # 配置已按备份重建 → 重启 source listener，使监听端口 / 解析器绑定
    # 与新的 DB 一致（否则仍绑在旧端口上）。
    try:
        sm = current_app.source_mgr
        if sm is not None:
            sm.stop_all()
            sm.start_all()
            result["listeners_restarted"] = True
    except Exception as e:
        result["listener_error"] = str(e)
        log.logger.warning(f"[Restore] restart listeners failed: {e}")

    # 重载恢复的插件代码，刷新运行中缓存 —— 避免"文件已恢复但运行中
    # _parser_cache / _channel_cache 仍是旧版"（v1.3.1 改进清单 #1）
    reload_errors = []
    for fname in restored["parser"]:
        try:
            parser_loader.reload_parser(fname)
            log.logger.info(f"Restore: reloaded parser {fname}")
        except Exception as e:
            reload_errors.append(f"parser {fname}: {str(e)[:200]}")
            log.logger.warning(f"[Restore] reload parser {fname} failed: {e}")
    for fname in restored["channel"]:
        try:
            channel_loader.reload_plugin(fname)
            log.logger.info(f"Restore: reloaded channel {fname}")
        except Exception as e:
            reload_errors.append(f"channel {fname}: {str(e)[:200]}")
            log.logger.warning(f"[Restore] reload channel {fname} failed: {e}")
    if reload_errors:
        result["reload_errors"] = reload_errors

    result["ok"] = True
    return jsonify(result)


# ── Import (legacy JSON) ──


def _import_preview(data, mode="insert"):
    existing = {
        "parsers": {p["id"]: p for p in db.get_parsers()},
        "sources": {s["id"]: s for s in db.get_sources()},
        "channels": {c["id"]: c for c in db.get_channels()},
        "templates": {t["id"]: t for t in db.get_templates()},
    }
    diff = {"parsers": [], "sources": [], "channels": [], "templates": [],
            "source_channels": [], "system_config": []}
    errors = []
    deps = {"missing": [], "auto_import": []}

    for p in data.get("parsers", []):
        pid = p.get("id")
        name = p.get("name", "?")
        exist = existing["parsers"].get(pid)
        if exist:
            if mode == "overwrite":
                diff["parsers"].append({"action": "update", "id": pid, "name": name})
            else:
                diff["parsers"].append({"action": "skip", "id": pid, "name": name,
                                        "reason": i18n._("import.already_exists")})
        else:
            diff["parsers"].append({"action": "insert", "id": pid, "name": name})

    for s in data.get("sources", []):
        sid = s.get("id")
        name = s.get("name", "?")
        exist = existing["sources"].get(sid)
        if exist:
            if mode == "overwrite":
                diff["sources"].append({"action": "update", "id": sid, "name": name})
            else:
                diff["sources"].append({"action": "skip", "id": sid, "name": name,
                                        "reason": i18n._("import.already_exists")})
        else:
            diff["sources"].append({"action": "insert", "id": sid, "name": name})

    for c in data.get("channels", []):
        cid = c.get("id")
        name = c.get("name", "?")
        exist = existing["channels"].get(cid)
        if exist:
            if mode == "overwrite":
                diff["channels"].append({"action": "update", "id": cid, "name": name})
            else:
                diff["channels"].append({"action": "skip", "id": cid, "name": name,
                                         "reason": i18n._("import.already_exists")})
        else:
            diff["channels"].append({"action": "insert", "id": cid, "name": name})

    for t in data.get("templates", []):
        tid = t.get("id")
        name = t.get("name", "?")
        exist = existing["templates"].get(tid)
        if exist:
            if mode == "overwrite":
                diff["templates"].append({"action": "update", "id": tid, "name": name})
            else:
                diff["templates"].append({"action": "skip", "id": tid, "name": name,
                                          "reason": i18n._("import.already_exists")})
        else:
            diff["templates"].append({"action": "insert", "id": tid, "name": name})

    for sc in data.get("source_channels", []):
        sid = sc.get("source_id")
        cid = sc.get("channel_id")
        tid = sc.get("template_id")
        sc_id = sc.get("id")
        missing_deps = []
        if sid not in existing["sources"]:
            missing_deps.append(f"source_id={sid}")
        if cid not in existing["channels"]:
            missing_deps.append(f"channel_id={cid}")
        if tid not in existing["templates"]:
            missing_deps.append(f"template_id={tid}")
        if missing_deps:
            diff["source_channels"].append({
                "action": "warn", "id": sc_id,
                "deps": missing_deps,
                "auto_resolve": i18n._("import.mark_invalid") if mode == "insert" else i18n._("import.auto_import_missing")
            })
            deps["missing"].append({"id": sc_id, "deps": missing_deps})
        else:
            diff["source_channels"].append({"action": "insert", "id": sc_id})

    for k in data.get("system_config", {}):
        diff["system_config"].append({"action": "upsert", "key": k})

    return {"diff": diff, "deps": deps, "errors": errors}


def _import_execute(data, mode="insert"):
    summary = {
        "parsers": {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0},
        "sources": {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0},
        "channels": {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0},
        "templates": {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0},
        "source_channels": {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0},
        "system_config": {"updated": 0, "errors": 0},
        "parser_files": {"written": 0, "errors": 0},
    }
    errors = []
    existing_ids = {}

    existing_ids["parsers"] = {p["filename"]: p for p in db.get_parsers()}
    for p in data.get("parsers", []):
        fn = os.path.basename(p.get("filename", ""))
        if not _safe_filename(fn):
            summary["parsers"]["errors"] += 1
            errors.append(i18n._("import.parser_error")
                          .replace("{name}", p.get("filename", "?"))
                          .replace("{error}", "unsafe filename"))
            continue
        exist = existing_ids["parsers"].get(fn)
        try:
            if exist and mode == "insert":
                summary["parsers"]["skipped"] += 1
                continue
            if p.get("code"):
                plugin_paths.ensure_user_dirs()
                fpath = os.path.join(plugin_paths.user_dir("parser"), fn)
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(p["code"])
                summary["parser_files"]["written"] += 1
            if exist:
                db.update_parser(exist["id"], name=p["name"], description=p.get("description", ""))
                summary["parsers"]["updated"] += 1
            else:
                pid = db.create_parser(p["name"], fn, p.get("description", ""))
                if pid:
                    if p.get("code"):
                        try:
                            parser_loader.load_parser(fn)
                        except Exception:
                            pass
                    summary["parsers"]["inserted"] += 1
                else:
                    summary["parsers"]["errors"] += 1
                    errors.append(i18n._("import.parser_exists").replace("{name}", fn))
        except Exception as e:
            summary["parsers"]["errors"] += 1
            errors.append(i18n._("import.parser_error").replace("{name}", fn).replace("{error}", str(e)[:200]))

    existing_ids["sources"] = {s["port"]: s for s in db.get_sources()}
    for s in data.get("sources", []):
        port = s.get("port")
        exist = existing_ids["sources"].get(port)
        try:
            if exist and mode == "insert":
                summary["sources"]["skipped"] += 1
                continue
            if exist:
                db.update_source(exist["id"], name=s["name"], parser_id=s.get("parser_id"),
                                 enabled=s.get("enabled", 1))
                summary["sources"]["updated"] += 1
            else:
                db.create_source(s["name"], port, s.get("parser_id"), s.get("enabled", 1))
                summary["sources"]["inserted"] += 1
        except Exception as e:
            summary["sources"]["errors"] += 1
            errors.append(i18n._("import.source_error").replace("{name}", s.get("name", "?")).replace("{error}", str(e)[:200]))

    existing_ids["channels"] = {c["name"]: c for c in db.get_channels()}
    for c in data.get("channels", []):
        name = c.get("name", "")
        exist = existing_ids["channels"].get(name)
        try:
            if exist and mode == "insert":
                summary["channels"]["skipped"] += 1
                continue
            if exist:
                db.update_channel(exist["id"], name=name, type=c.get("type"),
                                  config=c.get("config"), enabled=c.get("enabled", 1))
                summary["channels"]["updated"] += 1
            else:
                db.create_channel(name, c["type"], c.get("config", "{}"), c.get("enabled", 1))
                summary["channels"]["inserted"] += 1
        except Exception as e:
            summary["channels"]["errors"] += 1
            errors.append(i18n._("import.channel_error").replace("{name}", name).replace("{error}", str(e)[:200]))

    existing_ids["templates"] = {t["name"]: t for t in db.get_templates()}
    for t in data.get("templates", []):
        name = t.get("name", "")
        exist = existing_ids["templates"].get(name)
        try:
            if exist and mode == "insert":
                summary["templates"]["skipped"] += 1
                continue
            if exist:
                db.update_template(exist["id"], name=name, engine=t.get("engine", "jinja2"),
                                   title_tpl=t.get("title_tpl", ""), content_tpl=t.get("content_tpl", ""))
                summary["templates"]["updated"] += 1
            else:
                db.create_template(name, t.get("engine", "jinja2"),
                                   t.get("title_tpl", ""), t.get("content_tpl", ""))
                summary["templates"]["inserted"] += 1
        except Exception as e:
            summary["templates"]["errors"] += 1
            errors.append(i18n._("import.template_error").replace("{name}", name).replace("{error}", str(e)[:200]))

    for sc in data.get("source_channels", []):
        try:
            if mode == "insert":
                db.create_source_channel(
                    sc["source_id"], sc["channel_id"], sc["template_id"],
                    sc.get("condition_expr", ""), sc.get("priority", 0),
                    sc.get("enabled", 1), sc.get("urgent", 0),
                    sc.get("dedup_key_expr", ""), sc.get("dedup_window", 3600))
                summary["source_channels"]["inserted"] += 1
            else:
                existing_scs = db.get_source_channels(sc["source_id"])
                match = [x for x in existing_scs if x["channel_id"] == sc["channel_id"]]
                if match:
                    db.update_source_channel(match[0]["id"],
                        template_id=sc["template_id"],
                        condition_expr=sc.get("condition_expr", ""),
                        priority=sc.get("priority", 0),
                        enabled=sc.get("enabled", 1),
                        urgent=sc.get("urgent", 0),
                        dedup_key_expr=sc.get("dedup_key_expr", ""),
                        dedup_window=sc.get("dedup_window", 3600))
                    summary["source_channels"]["updated"] += 1
                else:
                    db.create_source_channel(
                        sc["source_id"], sc["channel_id"], sc["template_id"],
                        sc.get("condition_expr", ""), sc.get("priority", 0),
                        sc.get("enabled", 1), sc.get("urgent", 0),
                        sc.get("dedup_key_expr", ""), sc.get("dedup_window", 3600))
                    summary["source_channels"]["inserted"] += 1
        except Exception as e:
            summary["source_channels"]["errors"] += 1
            errors.append(i18n._("import.source_channel_error").replace("{name}", sc.get("id", "?")).replace("{error}", str(e)[:200]))

    for k, v in data.get("system_config", {}).items():
        try:
            db.set_config(k, v)
            summary["system_config"]["updated"] += 1
        except Exception as e:
            summary["system_config"]["errors"] += 1
            errors.append(i18n._("import.config_error").replace("{name}", k).replace("{error}", str(e)[:200]))

    return {"status": "ok" if not errors else "partial", "summary": summary, "errors": errors}


@backup_bp.route("/api/import", methods=["POST"])
def api_import():
    data = request.json
    if not data:
        return jsonify({"error": i18n._("err.invalid_json")}), 400

    mode = request.args.get("mode", "insert")
    dry_run = request.args.get("dry_run", "")

    if dry_run in ("1", "true", "yes"):
        result = _import_preview(data, mode)
        result["dry_run"] = True
        result["mode"] = mode
        return jsonify(result)

    preview = _import_preview(data, mode)
    result = _import_execute(data, mode)
    result["preview"] = preview
    return jsonify(result)

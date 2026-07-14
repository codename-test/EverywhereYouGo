#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
Web UI — Flask + Bootstrap 5, 模板外置到 templates/ 目录。
"""

import json
import os
import traceback

import log
import db
import parser_loader
import renderer
import source_manager as sm
import base64
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
source_mgr = None  # main.py 注入

VERSION = "1.0.0"
PARSERS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "parsers")


def _render(page: str, title: str, active_page: str = "", **kwargs):
    """统一渲染：模板名 + title + active_page 注入。"""
    return render_template(page, title=title, active_page=active_page, **kwargs)


# ═══════════════════════════════════════════════
#  Pages
# ═══════════════════════════════════════════════


@app.route("/")
def index():
    stats = db.get_stats()
    sources = db.get_sources()
    for s in sources:
        p = db.get_parser(s.get("parser_id"))
        s["parser_name"] = p["name"] if p else "-"
    return _render("dashboard.html", "仪表盘", "dashboard", stats=stats, sources=sources)


# ── Sources ──────────────────────────────────


@app.route("/sources")
def sources_page():
    sources = db.get_sources()
    parsers = db.get_parsers()
    for s in sources:
        p = db.get_parser(s.get("parser_id"))
        s["parser_name"] = p["name"] if p else "-"
    channels = db.get_channels()
    templates = db.get_templates()
    sc = db.get_all_source_channels()
    return _render("sources_page.html", "数据源", "sources",
                   sources=sources, parsers=parsers,
                   channels=channels, templates=templates, sc=sc)


@app.route("/api/sources", methods=["GET"])
def api_sources():
    sources = db.get_sources()
    for s in sources:
        p = db.get_parser(s.get("parser_id"))
        s["parser_name"] = p["name"] if p else "-"
        s["channels"] = db.get_source_channels(s["id"])
    return jsonify(sources)


@app.route("/api/sources", methods=["POST"])
def api_create_source():
    data = request.json
    sid = db.create_source(data["name"], data["port"], data.get("parser_id"),
                           data.get("enabled", 1))
    if sid is None:
        return jsonify({"error": "端口已被占用"}), 400
    if source_mgr:
        source_mgr.start_source(sid)
    return jsonify({"id": sid})


@app.route("/api/sources/<int:sid>", methods=["PUT"])
def api_update_source(sid):
    data = request.json
    old = db.get_source(sid)
    if not old:
        return jsonify({"error": "Not found"}), 404
    if source_mgr:
        source_mgr.stop_source(sid)
    db.update_source(sid, **{k: v for k, v in data.items()
                             if k in ("name", "port", "parser_id", "enabled")})
    if source_mgr and data.get("enabled", old["enabled"]):
        source_mgr.start_source(sid)
    return jsonify({"status": "ok"})


# ── Parsers ──────────────────────────────────


@app.route("/parsers")
def parsers_page():
    parsers = db.get_parsers()
    for p in parsers:
        p["exists"] = os.path.isfile(os.path.join(PARSERS_DIR, p["filename"]))
    return _render("parsers_page.html", "解析器", "parsers", parsers=parsers)


@app.route("/api/parsers", methods=["GET"])
def api_parsers():
    parsers = db.get_parsers()
    for p in parsers:
        p["exists"] = os.path.isfile(os.path.join(PARSERS_DIR, p["filename"]))
    return jsonify(parsers)


@app.route("/api/parsers", methods=["POST"])
def api_create_parser():
    if "name" not in request.form:
        return jsonify({"error": "Missing name"}), 400
    name = request.form["name"]
    desc = request.form.get("description", "")
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400
    f = request.files["file"]
    if not f.filename.endswith(".py"):
        return jsonify({"error": "Only .py files allowed"}), 400
    filename = f.filename
    filepath = os.path.join(PARSERS_DIR, filename)
    f.save(filepath)
    pid = db.create_parser(name, filename, desc)
    if pid is None:
        return jsonify({"error": "Parser filename already exists"}), 400
    return jsonify({"id": pid})


@app.route("/api/parsers/<int:pid>", methods=["DELETE"])
def api_delete_parser(pid):
    p = db.get_parser(pid)
    if not p:
        return jsonify({"error": "Not found"}), 404
    filepath = os.path.join(PARSERS_DIR, p["filename"])
    if os.path.isfile(filepath):
        os.remove(filepath)
    db.delete_parser(pid)
    return jsonify({"status": "ok"})


@app.route("/api/parsers/<int:pid>/content", methods=["GET"])
def api_get_parser_content(pid):
    """在线编辑：读取解析器 .py 文件内容。"""
    p = db.get_parser(pid)
    if not p:
        return jsonify({"error": "Not found"}), 404
    fpath = os.path.join(PARSERS_DIR, p["filename"])
    if not os.path.isfile(fpath):
        return jsonify({"error": "File not found"}), 404
    with open(fpath, "r", encoding="utf-8") as f:
        return jsonify({"filename": p["filename"], "content": f.read()})


@app.route("/api/parsers/<int:pid>/content", methods=["PUT"])
def api_update_parser_content(pid):
    """在线编辑：保存解析器 .py 文件内容。"""
    p = db.get_parser(pid)
    if not p:
        return jsonify({"error": "Not found"}), 404
    data = request.json
    if "content" not in data:
        return jsonify({"error": "Missing content"}), 400
    fpath = os.path.join(PARSERS_DIR, p["filename"])
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(data["content"])
    # 需要重新加载解析器模块
    try:
        parser_loader.reload_parser(p["filename"])
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": f"Syntax error: {str(e)}"}), 400


@app.route("/api/parsers/<int:pid>/variables", methods=["GET"])
def api_parser_variables(pid):
    """返回解析器 return dict 中定义的所有变量名。"""
    p = db.get_parser(pid)
    if not p:
        return jsonify({"ok": False, "error": "Not found"}), 404
    fpath = os.path.join(PARSERS_DIR, p["filename"])
    if not os.path.isfile(fpath):
        return jsonify({"ok": False, "error": "File not found"}), 404
    with open(fpath, "r", encoding="utf-8") as f:
        content = f.read()
    import re
    match = re.search(r"return\s*\{([\s\S]*?)\}", content)
    keys = []
    if match:
        keys = list(set(re.findall(r'"([^"]+)"', match.group(1))))
    return jsonify({"ok": True, "filename": p["filename"], "variables": [{"path": k, "type": "str", "sample": ""} for k in keys]})


# ── Channels ─────────────────────────────────


@app.route("/channels")
def channels_page():
    channels = db.get_channels()
    return _render("channels_page.html", "推送通道", "channels", channels=channels)


@app.route("/api/channels", methods=["GET"])
def api_channels():
    return jsonify(db.get_channels())


@app.route("/api/channels", methods=["POST"])
def api_create_channel():
    data = request.json
    cid = db.create_channel(data["name"], data["type"], data.get("config", {}))
    return jsonify({"id": cid})


@app.route("/api/channels/<int:cid>", methods=["PUT"])
def api_update_channel(cid):
    data = request.json
    db.update_channel(cid, **data)
    return jsonify({"status": "ok"})




# ── Templates ────────────────────────────────


@app.route("/templates")
def templates_page():
    templates = db.get_templates()
    parsers = db.get_parsers()
    return _render("templates_page.html", "推送模板", "templates",
                   templates=templates, parsers=parsers)


@app.route("/api/templates", methods=["GET"])
def api_templates():
    return jsonify(db.get_templates())


@app.route("/api/templates", methods=["POST"])
def api_create_template():
    data = request.json
    tid = db.create_template(
        data["name"], data.get("engine", "jinja2"),
        data.get("title_tpl", ""), data.get("content_tpl", ""))
    return jsonify({"id": tid})


@app.route("/api/templates/<int:tid>", methods=["PUT"])
def api_update_template(tid):
    data = request.json
    db.update_template(tid, **{k: v for k, v in data.items()
                                if k in ("name", "engine", "title_tpl",
                                         "content_tpl")})
    return jsonify({"status": "ok"})


@app.route("/api/templates/test-render", methods=["POST"])
def api_template_test_render():
    """测试模板渲染效果。"""
    data = request.json
    if not data:
        return jsonify({"ok": False, "error": "No data"}), 400
    engine = data.get("engine", "simple")
    title_tpl = data.get("title_tpl", "")
    content_tpl = data.get("content_tpl", "")
    msg = data.get("msg", {})
    try:
        result = renderer.render_template(engine, title_tpl, content_tpl, msg)
        return jsonify({"ok": True, "result": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ── Logs ─────────────────────────────────────


@app.route("/logs")
def logs_page():
    return _render("logs_page.html", "系统日志", "logs")


@app.route("/api/logs", methods=["GET"])
def api_logs():
    level = request.args.get("level")
    limit = request.args.get("limit", 200, type=int)
    return jsonify(db.get_logs(level=level, limit=limit))


@app.route("/api/logs", methods=["DELETE"])
def api_clear_logs():
    db.clear_logs()
    return jsonify({"status": "ok"})




# ── Messages (unified) ───────────────────────


@app.route("/messages")
def messages_page():
    sources = db.get_sources()
    return _render("messages.html", "消息记录", "messages", sources=sources)


@app.route("/api/messages", methods=["GET"])
def api_get_messages():
    limit   = request.args.get("limit", 100, type=int)
    offset  = request.args.get("offset", 0, type=int)
    sid     = request.args.get("source_id", type=int)
    status  = request.args.get("status")
    ch_type = request.args.get("channel_type")
    msgs   = db.get_messages(source_id=sid, status=status, channel_type=ch_type,
                             limit=limit, offset=offset)
    total  = db.get_message_count(source_id=sid, status=status)
    pending = db.get_message_count(status="PENDING")
    failed  = db.get_message_count(status="FAILED")
    return jsonify({
        "messages": msgs,
        "total": total,
        "pending": pending,
        "failed": failed,
    })


@app.route("/api/messages/<int:msg_id>", methods=["DELETE"])
def api_delete_message(msg_id):
    db.delete_message(msg_id)
    return jsonify({"status": "ok"})


@app.route("/api/messages/<int:msg_id>/retry", methods=["POST"])
def api_retry_message(msg_id):
    mode = request.args.get("mode", "original")
    ok, err = sm.retry_message(msg_id, mode)
    return jsonify({"ok": ok, "error": err} if err else {"ok": ok})


@app.route("/api/messages/<int:msg_id>/ignore", methods=["POST"])
def api_ignore_message(msg_id):
    db.mark_ignored(msg_id)
    return jsonify({"status": "ok"})


@app.route("/api/messages/batch", methods=["POST"])
def api_batch_messages():
    data = request.json
    action = data.get("action")  # "retry" or "ignore"
    ids = data.get("ids", [])
    if not ids:
        return jsonify({"ok": False, "error": "No ids provided"})

    results = {"ok": 0, "fail": 0, "errors": []}
    for mid in ids:
        if action == "retry":
            ok, err = sm.retry_message(mid)
            if ok:
                results["ok"] += 1
            else:
                results["fail"] += 1
                results["errors"].append({"id": mid, "error": err})
        elif action == "ignore":
            db.mark_ignored(mid)
            results["ok"] += 1
        else:
            return jsonify({"ok": False, "error": f"Unknown action: {action}"})

    return jsonify(results)


# ── Queue / Flush ────────────────────────────


@app.route("/api/queue/flush", methods=["POST"])
def api_flush_queue():
    """手动刷新全部待发送队列。"""
    total = 0
    for s in db.get_sources():
        if s["enabled"]:
            try:
                count = sm.flush_queue_for_source(s["id"])
                total += count
            except Exception as e:
                log.logger.error(f"[API] Flush source {s['name']} error: {e}")
    return jsonify({"count": total})


@app.route("/api/sources/<int:sid>/channels", methods=["POST"])
def api_save_source_channels(sid):
    """批量保存数据源的渠道绑定（全量替换）。"""
    data = request.json
    items = data if isinstance(data, list) else [data]
    existing = db.get_source_channels(sid)
    for sc in existing:
        db.delete_source_channel(sc["id"])
    for item in items:
        db.create_source_channel(
            sid,
            item["channel_id"],
            item["template_id"],
            condition_expr=item.get("condition_expr", ""),
            priority=item.get("priority", 0),
            enabled=item.get("enabled", 1),
            urgent=item.get("urgent", 0),
        )
    return jsonify({"status": "ok", "count": len(items)})


# ── 样本数据 / 测试解析 / 测试推送 ──────────────


@app.route("/api/sources/<int:sid>/samples", methods=["GET"])
def api_source_samples(sid):
    """获取数据源的最近样本数据。"""
    count = request.args.get("count", 10, type=int)
    samples = sm.get_samples(sid, count)
    return jsonify(samples)


@app.route("/api/sources/<int:sid>/test-parse", methods=["POST"])
def api_source_test_parse(sid):
    """测试解析：用选中的样本数据运行解析器，返回解析结果。"""
    data = request.json
    sample_body = data.get("body", "")
    sample_headers = data.get("headers", {})
    sample_query = data.get("query_params", {})

    src = db.get_source(sid)
    if not src:
        return jsonify({"ok": False, "error": "数据源不存在"}), 404
    if not src.get("parser_id"):
        return jsonify({"ok": False, "error": "此数据源未绑定解析器"})

    parser = db.get_parser(src["parser_id"])
    if not parser:
        return jsonify({"ok": False, "error": "解析器不存在"})

    try:
        raw_body = sample_body.encode("utf-8")
        result = parser_loader.run_parser(parser["filename"], raw_body, sample_headers, sample_query)
        return jsonify({"ok": True, "result": result})
    except Exception as e:
        log.logger.error(f"[test-parse] sid={sid}: {e}")
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/sources/<int:sid>/test-push", methods=["POST"])
def api_source_test_push(sid):
    """测试推送：用选中的样本数据走完整链路（解析 → 路由 → 发送）。"""
    data = request.json
    sample_body = data.get("body", "")
    sample_headers = data.get("headers", {})
    sample_query = data.get("query_params", {})

    src = db.get_source(sid)
    if not src:
        return jsonify({"ok": False, "error": "数据源不存在"}), 404
    if not src.get("parser_id"):
        return jsonify({"ok": False, "error": "此数据源未绑定解析器"})

    try:
        raw_body = sample_body.encode("utf-8")
        ok, msg = sm.process_message(sid, raw_body, sample_headers, sample_query)
        return jsonify({"ok": ok, "message": "推送成功" if ok else "推送失败，请查看日志"})
    except Exception as e:
        log.logger.error(f"[test-push] sid={sid}: {e}")
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/sources/<int:sid>", methods=["DELETE"])
def api_delete_source(sid):
    if source_mgr:
        source_mgr.stop_source(sid)
    db.delete_source(sid)
    return jsonify({"status": "ok"})


# ── Export (2.1 + 2.2) ──────────────────────

import base64 as _base64
import datetime as _dt

PARSERS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "parsers")


def _export_source(s):
    return {k: s[k] for k in ("id", "name", "port", "parser_id", "enabled", "created_at")}


def _export_parser(p):
    data = {k: p[k] for k in ("id", "name", "filename", "description", "created_at")}
    return data  # 不嵌入 code，单独 /api/export/parser/{id}/file 下载 .py


def _export_parser_with_code(p):
    """全量导出用 — 嵌入解析器代码纯文本（非 base64），支持导入恢复。"""
    data = _export_parser(p)
    fpath = os.path.join(PARSERS_DIR, p["filename"])
    if os.path.isfile(fpath):
        with open(fpath, "r", encoding="utf-8") as f:
            data["code"] = f.read()
    else:
        data["code"] = ""
    return data


def _export_channel(c):
    return {k: c[k] for k in ("id", "name", "type", "config", "enabled", "created_at")}


def _export_template(t):
    return {k: t[k] for k in ("id", "name", "engine", "title_tpl", "content_tpl", "created_at")}


@app.route("/api/export/all", methods=["GET"])
def api_export_all():
    """全量导出：所有配置 + 解析器代码 base64。"""
    config_items = db._conn().execute("SELECT * FROM system_config").fetchall()
    payload = {
        "version": VERSION,
        "exported_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "parsers":          [_export_parser_with_code(p) for p in db.get_parsers()],
        "sources":          [_export_source(s) for s in db.get_sources()],
        "channels":         [_export_channel(c) for c in db.get_channels()],
        "templates":        [_export_template(t) for t in db.get_templates()],
        "source_channels":  db.get_all_source_channels(),
        "system_config":    {r["key"]: r["value"] for r in config_items},
    }
    return jsonify(payload)


@app.route("/api/export/<item_type>/<int:item_id>", methods=["GET"])
def api_export_single(item_type, item_id):
    """单项导出。"""
    # parser 特殊处理：直接下载 .py 文件
    if item_type == "parser":
        item = db.get_parser(item_id)
        if not item:
            return jsonify({"error": "Not found"}), 404
        fpath = os.path.join(PARSERS_DIR, item["filename"])
        if not os.path.isfile(fpath):
            return jsonify({"error": "File not found on disk"}), 404
        from flask import send_file
        return send_file(fpath, as_attachment=True, download_name=item["filename"])

    getter = {
        "source":   (db.get_source,   _export_source),
        "channel":  (db.get_channel,  _export_channel),
        "template": (db.get_template, _export_template),
    }
    pair = getter.get(item_type)
    if not pair:
        return jsonify({"error": f"Unknown type: {item_type}"}), 400
    fn_get, fn_export = pair
    item = fn_get(item_id)
    if not item:
        return jsonify({"error": "Not found"}), 404
    return jsonify(fn_export(item))


# ── Import (2.3) ─────────────────────────────


def _import_preview(data, mode="insert"):
    """
    检查导入数据并返回差异预览。
    Returns: {"diff": {...}, "deps": {...}, "errors": [...]}
    """
    existing = {
        "parsers":  {p["id"]: p for p in db.get_parsers()},
        "sources":  {s["id"]: s for s in db.get_sources()},
        "channels": {c["id"]: c for c in db.get_channels()},
        "templates":{t["id"]: t for t in db.get_templates()},
    }
    diff = {"parsers": [], "sources": [], "channels": [], "templates": [],
            "source_channels": [], "system_config": []}
    errors = []
    deps = {"missing": [], "auto_import": []}

    # ── parsers ──
    for p in data.get("parsers", []):
        pid = p.get("id")
        name = p.get("name", "?")
        exist = existing["parsers"].get(pid)
        if exist:
            if mode == "overwrite":
                diff["parsers"].append({"action": "update", "id": pid, "name": name})
            else:
                diff["parsers"].append({"action": "skip", "id": pid, "name": name,
                                        "reason": "已存在"})
        else:
            diff["parsers"].append({"action": "insert", "id": pid, "name": name})

    # ── sources ──
    for s in data.get("sources", []):
        sid = s.get("id")
        name = s.get("name", "?")
        exist = existing["sources"].get(sid)
        if exist:
            if mode == "overwrite":
                diff["sources"].append({"action": "update", "id": sid, "name": name})
            else:
                diff["sources"].append({"action": "skip", "id": sid, "name": name,
                                        "reason": "已存在"})
        else:
            diff["sources"].append({"action": "insert", "id": sid, "name": name})

    # ── channels ──
    for c in data.get("channels", []):
        cid = c.get("id")
        name = c.get("name", "?")
        exist = existing["channels"].get(cid)
        if exist:
            if mode == "overwrite":
                diff["channels"].append({"action": "update", "id": cid, "name": name})
            else:
                diff["channels"].append({"action": "skip", "id": cid, "name": name,
                                        "reason": "已存在"})
        else:
            diff["channels"].append({"action": "insert", "id": cid, "name": name})

    # ── templates ──
    for t in data.get("templates", []):
        tid = t.get("id")
        name = t.get("name", "?")
        exist = existing["templates"].get(tid)
        if exist:
            if mode == "overwrite":
                diff["templates"].append({"action": "update", "id": tid, "name": name})
            else:
                diff["templates"].append({"action": "skip", "id": tid, "name": name,
                                        "reason": "已存在"})
        else:
            diff["templates"].append({"action": "insert", "id": tid, "name": name})

    # ── source_channels (依赖检查) ──
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
                "auto_resolve": "标记为失效" if mode == "insert" else "一并导入缺失项"
            })
            deps["missing"].append({"id": sc_id, "deps": missing_deps})
        else:
            diff["source_channels"].append({"action": "insert", "id": sc_id})

    # ── system_config ──
    for k in data.get("system_config", {}):
        diff["system_config"].append({"action": "upsert", "key": k})

    return {"diff": diff, "deps": deps, "errors": errors}


def _import_execute(data, mode="insert"):
    """
    执行导入。
    Returns: {"status": "ok", "summary": {...}, "errors": [...]}
    """
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

    # ── 1. Parsers ──
    existing_ids["parsers"] = {p["filename"]: p for p in db.get_parsers()}
    for p in data.get("parsers", []):
        fn = p.get("filename", "")
        exist = existing_ids["parsers"].get(fn)
        try:
            if exist and mode == "insert":
                summary["parsers"]["skipped"] += 1
                continue
            # Write .py file
            if p.get("code"):
                fpath = os.path.join(PARSERS_DIR, fn)
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(p["code"])
                summary["parser_files"]["written"] += 1
            if exist:
                db.update_parser(exist["id"], name=p["name"], description=p.get("description", ""))
                summary["parsers"]["updated"] += 1
            else:
                pid = db.create_parser(p["name"], fn, p.get("description", ""))
                if pid:
                    # If code was written, reload
                    if p.get("code"):
                        try:
                            parser_loader.load_parser(fn)
                        except Exception:
                            pass
                    summary["parsers"]["inserted"] += 1
                else:
                    summary["parsers"]["errors"] += 1
                    errors.append(f"Parser already exists: {fn}")
        except Exception as e:
            summary["parsers"]["errors"] += 1
            errors.append(f"Parser {fn}: {str(e)[:200]}")

    # ── 2. Sources ──
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
            errors.append(f"Source {s.get('name','?')}: {str(e)[:200]}")

    # ── 3. Channels ──
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
            errors.append(f"Channel {name}: {str(e)[:200]}")

    # ── 4. Templates ──
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
                                   title_tpl=t.get("title_tpl", ""),
                                   content_tpl=t.get("content_tpl", ""))
                summary["templates"]["updated"] += 1
            else:
                db.create_template(name, t.get("engine", "jinja2"),
                                   t.get("title_tpl", ""), t.get("content_tpl", ""))
                summary["templates"]["inserted"] += 1
        except Exception as e:
            summary["templates"]["errors"] += 1
            errors.append(f"Template {name}: {str(e)[:200]}")

    # ── 5. Source-Channels ──
    for sc in data.get("source_channels", []):
        try:
            if mode == "insert":
                db.create_source_channel(
                    sc["source_id"], sc["channel_id"], sc["template_id"],
                    sc.get("condition_expr", ""), sc.get("priority", 0),
                    sc.get("enabled", 1), sc.get("urgent", 0)
                )
                summary["source_channels"]["inserted"] += 1
            else:
                # overwrite: find existing by source_id+channel_id
                existing_scs = db.get_source_channels(sc["source_id"])
                match = [x for x in existing_scs if x["channel_id"] == sc["channel_id"]]
                if match:
                    db.update_source_channel(match[0]["id"],
                        template_id=sc["template_id"],
                        condition_expr=sc.get("condition_expr", ""),
                        priority=sc.get("priority", 0),
                        enabled=sc.get("enabled", 1),
                        urgent=sc.get("urgent", 0))
                    summary["source_channels"]["updated"] += 1
                else:
                    db.create_source_channel(
                        sc["source_id"], sc["channel_id"], sc["template_id"],
                        sc.get("condition_expr", ""), sc.get("priority", 0),
                        sc.get("enabled", 1), sc.get("urgent", 0))
                    summary["source_channels"]["inserted"] += 1
        except Exception as e:
            summary["source_channels"]["errors"] += 1
            errors.append(f"SourceChannel {sc.get('id','?')}: {str(e)[:200]}")

    # ── 6. System Config ──
    for k, v in data.get("system_config", {}).items():
        try:
            db.set_config(k, v)
            summary["system_config"]["updated"] += 1
        except Exception as e:
            summary["system_config"]["errors"] += 1
            errors.append(f"Config {k}: {str(e)[:200]}")

    return {"status": "ok" if not errors else "partial", "summary": summary, "errors": errors}


@app.route("/api/import", methods=["POST"])
def api_import():
    """导入配置。
    ?dry_run=1   → 仅预览
    ?mode=overwrite → 覆盖模式（默认 insert 模式跳过已有）
    """
    data = request.json
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

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


# ── Settings ─────────────────────────────────


@app.route("/settings")
def settings_page():
    config = {
        "log_level": db.get_log_level(),
        "dnd_enabled": db.get_config("dnd_enabled", "0"),
        "dnd_start": db.get_config("dnd_start", "23:00"),
        "dnd_end": db.get_config("dnd_end", "07:00"),
    }
    return _render("settings.html", "系统设置", "settings",
                   config=config, version=VERSION)


@app.route("/api/settings", methods=["POST"])
def api_update_settings():
    data = request.json
    for k, v in data.items():
        if k == "log_level":
            db.set_log_level(v)
        else:
            db.set_config(k, v)
    return jsonify({"status": "ok"})




# ── Entry Point ──────────────────────────────


def run_web_ui(port: int = 5000):
    """由 main.py 调用，启动 Flask 开发服务器。"""
    import socket as _sock
    _old_init = _sock.socket.__init__
    def _patched_init(self, *args, **kwargs):
        _old_init(self, *args, **kwargs)
        self.setsockopt(_sock.SOL_SOCKET, _sock.SO_REUSEADDR, 1)
    _sock.socket.__init__ = _patched_init
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)

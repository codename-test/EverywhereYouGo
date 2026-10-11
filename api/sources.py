#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""api/sources.py — 数据源 CRUD + 绑定 + 样本 + 测试"""

import os
import sqlite3
import log
import db
import parser_loader
import i18n
from flask import Blueprint, request, jsonify, current_app
from api.validation import (
    require_name, optional_str, optional_int, optional_port,
    optional_flag, optional_slug, ValidationError,
)

sources_bp = Blueprint("sources", __name__)


def _sub_route_error(parent_id, path, sid=None):
    """子路由（parent_id 有效）必须挂在"存在且顶层"的 Source 组下、且路径非空。

    路由是两级匹配（path_router：先按 slug 找组，再 (组id, path) 精确找子路由），
    且连接未开 PRAGMA foreign_keys（db/queries.py 注释确认），所以悬空父级/三级嵌套/
    自引用/空 path 都能写进 DB，却永远解析不到——等于造一个收不到消息的 Source。
    返回错误消息或 None。"""
    if not parent_id:
        return None
    parent = db.get_source(parent_id)
    if not parent:
        return "parent source does not exist"
    if parent.get("parent_id"):
        return "parent must be a top-level source group (not another sub-route)"
    if sid is not None and parent_id == sid:
        return "source cannot be its own parent"
    if not path:
        return "sub-route requires a non-empty path"
    if "/" in path:
        return "path must be a single segment (no '/')"
    return None


@sources_bp.route("/api/sources", methods=["GET"])
def api_sources():
    """Return top-level sources (groups + port-mode) with nested sub-routes."""
    groups = db.get_source_groups()
    for s in groups:
        p = db.get_parser(s.get("parser_id"))
        s["parser_name"] = p["name"] if p else "-"
        s["channels"] = db.get_source_channels(s["id"])
        # Attach sub-routes for path-mode groups
        if s.get("slug"):
            subs = db.get_sub_routes(s["id"])
            for sub in subs:
                sp = db.get_parser(sub.get("parser_id"))
                sub["parser_name"] = sp["name"] if sp else "-"
                sub["channels"] = db.get_source_channels(sub["id"])
            s["sub_routes"] = subs
    return jsonify(groups)


@sources_bp.route("/api/sources", methods=["POST"])
def api_create_source():
    data = request.json or {}
    port = optional_port(data)
    parent_id = optional_int(data, "parent_id", 1, default=None)
    path = optional_str(data, "path", max_len=200, default="") or ""
    err = _sub_route_error(parent_id, path)
    if err:
        return jsonify({"error": err}), 400
    # 子路由：同一组下 path（nginx 路径最后一段）不能重复
    if parent_id and path and db.get_sub_route_by_path(parent_id, path):
        return jsonify({"error": "path already exists under this source"}), 409
    sid = db.create_source(
        name=require_name(data),
        port=port,
        parser_id=optional_int(data, "parser_id", 1, default=None),
        enabled=optional_flag(data, "enabled", default=1),
        slug=optional_slug(data),
        parent_id=parent_id,
        path=path,
    )
    if sid is None:
        return jsonify({"error": i18n._("err.port_in_use")}), 400
    import config_manager
    # 端口模式顶层源启动监听；失败则回滚本次创建（保留的必须可用）
    sm = current_app.source_mgr
    if sm and port and not data.get("parent_id"):
        started, err = sm.start_source(sid)
        if not started and err:
            db.delete_source(sid)
            config_manager.sync_table("sources")
            log.logger.error(f"[sources] listener failed, rolled back source {sid}: {err}")
            return jsonify({"error": "listener failed to start: %s" % err}), 500
    config_manager.sync_table("sources")
    return jsonify({"id": sid})


@sources_bp.route("/api/sources/full", methods=["POST"])
def api_create_source_full():
    """一次性创建完整 Source：源本身 + 全部通道绑定。

    与 POST /api/sources 的区别：
    - 只 INSERT，绝不覆盖已有记录（不存在 upsert 语义）；
    - 源与绑定在同一个事务里写入，任一步失败整体回滚；
    - parser_id / channel_id / template_id 必须是库里已存在的组件。
    """
    data = request.json or {}
    name = require_name(data)
    port = optional_port(data)
    slug = optional_slug(data)
    parser_id = optional_int(data, "parser_id", 1, default=None)
    enabled = optional_flag(data, "enabled", default=1)
    parent_id = optional_int(data, "parent_id", 1, default=None)
    path = optional_str(data, "path", max_len=200, default="") or ""

    raw_bindings = data.get("bindings") or []
    if not isinstance(raw_bindings, list):
        raise ValidationError("bindings must be a list")

    # 只允许引用已存在的组件
    if parser_id is None:
        raise ValidationError("parser_id is required")
    if db.get_parser(parser_id) is None:
        raise ValidationError(f"parser_id {parser_id} does not exist")

    bindings = []
    for i, b in enumerate(raw_bindings):
        if not isinstance(b, dict):
            raise ValidationError(f"bindings[{i}] must be a JSON object")
        channel_id = optional_int(b, "channel_id", 1)
        template_id = optional_int(b, "template_id", 1)
        if db.get_channel(channel_id) is None:
            raise ValidationError(f"bindings[{i}]: channel_id {channel_id} does not exist")
        if db.get_template(template_id) is None:
            raise ValidationError(f"bindings[{i}]: template_id {template_id} does not exist")
        bindings.append({
            "channel_id": channel_id,
            "template_id": template_id,
            "condition_expr": optional_str(b, "condition_expr", max_len=500, default="") or "",
            "priority": optional_int(b, "priority", default=0),
            "dedup_key_expr": optional_str(b, "dedup_key_expr", max_len=200, default="") or "",
            "dedup_window": optional_int(b, "dedup_window", 0, default=3600),
            "enabled": optional_flag(b, "enabled", default=1),
            "urgent": optional_flag(b, "urgent", default=0),
        })

    # 冲突检查：slug / port 唯一索引（只 INSERT，不覆盖）
    if slug and db.get_source_by_slug(slug):
        return jsonify({"error": "slug already exists"}), 409
    if port and db.get_source_by_port(port):
        return jsonify({"error": i18n._("err.port_in_use")}), 409
    # 子路由：同一组下 path（nginx 路径 /<prefix>/<slug>/<path> 的最后一段）不能重复
    err = _sub_route_error(parent_id, path)
    if err:
        return jsonify({"error": err}), 400
    if parent_id and path and db.get_sub_route_by_path(parent_id, path):
        return jsonify({"error": "path already exists under this source"}), 409

    conn = db._conn()
    try:
        cur = conn.execute(
            """INSERT INTO sources (name, port, parser_id, enabled, slug, parent_id, path)
               VALUES (?,?,?,?,?,?,?)""",
            (name, port, parser_id, enabled, slug, parent_id, path)
        )
        sid = cur.lastrowid
        binding_ids = []
        for b in bindings:
            bcur = conn.execute(
                """INSERT INTO source_channels
                   (source_id, channel_id, template_id, condition_expr, priority,
                    enabled, urgent, dedup_key_expr, dedup_window)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (sid, b["channel_id"], b["template_id"], b["condition_expr"],
                 b["priority"], b["enabled"], b["urgent"],
                 b["dedup_key_expr"], b["dedup_window"])
            )
            binding_ids.append(bcur.lastrowid)
        conn.commit()
    except sqlite3.IntegrityError as e:
        # 并发下唯一索引仍可能冲突：此时尚未 commit，回滚即可，绝不覆盖既有行
        conn.rollback()
        log.logger.error(f"[sources/full] integrity error: {e}")
        return jsonify({"error": "slug or port already exists"}), 409
    except Exception as e:
        conn.rollback()
        log.logger.error(f"[sources/full] rollback: {e}")
        return jsonify({"error": str(e)}), 500

    import config_manager
    config_manager.sync_table("sources")
    config_manager.sync_table("bindings")

    # 仅端口模式的顶层 Source 需要启动监听；启动失败则回滚本次创建（保留的必须可用）
    sm = current_app.source_mgr
    if sm and port and not parent_id:
        started, err = sm.start_source(sid)
        if not started and err:
            db.delete_source(sid)   # 显式级联删绑定 + 子路由
            config_manager.sync_table("sources")
            config_manager.sync_table("bindings")
            log.logger.error(f"[sources/full] listener failed, rolled back source {sid}: {err}")
            return jsonify({"error": "listener failed to start: %s" % err}), 500

    return jsonify({"id": sid, "bindings": binding_ids, "status": "ok"}), 201


@sources_bp.route("/api/sources/<int:sid>", methods=["PUT"])
def api_update_source(sid):
    data = request.json or {}
    old = db.get_source(sid)
    if not old:
        return jsonify({"error": i18n._("err.not_found")}), 404

    # 只接受白名单字段，且逐字段校验（原实现把任意值直接塞进 DB）
    patch = {}
    if "name" in data:
        patch["name"] = require_name(data)
    if "port" in data:
        patch["port"] = optional_port(data)
    if "parser_id" in data:
        patch["parser_id"] = optional_int(data, "parser_id", 1, default=None)
    if "enabled" in data:
        patch["enabled"] = optional_flag(data, "enabled")
    if "slug" in data:
        patch["slug"] = optional_slug(data)
    if "parent_id" in data:
        patch["parent_id"] = optional_int(data, "parent_id", 1, default=None)
    if "path" in data:
        patch["path"] = optional_str(data, "path", max_len=200, default="") or ""

    # 接入模式字段约束：切换模式时清理不再适用的字段（避免"幽灵端口"占着唯一索引）
    #   子路由(parent 有效)→port=NULL；独立端口/顶层组→path=""；顶层组→port=NULL
    eff_parent = patch.get("parent_id", old.get("parent_id"))
    eff_port = patch.get("port", old.get("port"))
    if eff_parent:
        patch["port"] = None
    elif eff_port:
        patch["path"] = ""
    else:
        patch["port"] = None
        patch["path"] = ""

    # P1-3：改 parent_id/path 前查子路由 path 冲突（排除自身）→ 409
    new_parent = patch.get("parent_id", old.get("parent_id"))
    new_path = patch.get("path", old.get("path"))
    # 子路由必须挂在存在且顶层的组下、路径非空——否则写进去也收不到消息
    err = _sub_route_error(new_parent, new_path, sid)
    if err:
        return jsonify({"error": err}), 400
    if new_parent and new_path:
        clash = db.get_sub_route_by_path(new_parent, new_path)
        if clash and clash["id"] != sid:
            return jsonify({"error": "path already exists under this source"}), 409

    sm = current_app.source_mgr
    # 按"更新后的完整配置"定最终接入模式，比较新旧决定是否启停监听
    new_port = patch.get("port", old.get("port"))
    new_parent = patch.get("parent_id", old.get("parent_id"))
    new_enabled = patch.get("enabled", old.get("enabled"))
    old_port_mode = bool(old.get("port")) and not old.get("parent_id")
    new_port_mode = bool(new_port) and not new_parent
    # 只有影响监听的字段（模式/port/enabled）变化才启停；改名等不动监听
    listener_relevant = (old_port_mode != new_port_mode
                         or old.get("port") != new_port
                         or old.get("enabled") != new_enabled)

    # 更新：撞唯一索引（slug/port/(parent_id,path)）→ 409（DB 未改，监听原样保留）
    try:
        db.update_source(sid, **patch)
    except sqlite3.IntegrityError as e:
        log.logger.error(f"[sources PUT] integrity error on update {sid}: {e}")
        return jsonify({"error": "conflict: slug/port/sub-route path already exists"}), 409

    # 监听器启停：按新旧模式 + 影响监听的字段变化决定（留下的必须可用）
    if sm and listener_relevant:
        was_listening = old_port_mode and bool(old.get("enabled"))
        need_listening = new_port_mode and bool(new_enabled)
        if was_listening:
            sm.stop_source(sid)
        if need_listening:
            started, err = sm.start_source(sid)
            if not started and err:
                # 回滚旧配置
                old_patch = {k: old.get(k) for k in
                             ("name", "port", "parser_id", "enabled", "slug", "parent_id", "path")}
                db.update_source(sid, **old_patch)
                # 尝试恢复旧监听（旧配置是端口模式且启用才需要）
                old_restore_err = None
                if old_port_mode and bool(old.get("enabled")):
                    r_ok, r_err = sm.start_source(sid)
                    if not r_ok and r_err:
                        old_restore_err = r_err
                import config_manager
                config_manager.sync_table("sources")
                if old_restore_err:
                    log.logger.error(
                        f"[sources PUT] source {sid}: 新监听失败({err}) 且旧监听恢复也失败"
                        f"({old_restore_err})，该 Source 当前未监听")
                    return jsonify({"error": "listener failed to start: %s；旧监听恢复失败：%s；"
                                             "该 Source 当前未监听" % (err, old_restore_err)}), 500
                log.logger.error(f"[sources PUT] listener failed, restored old config for {sid}: {err}")
                return jsonify({"error": "listener failed to start: %s（已回滚为旧配置）" % err}), 500

    import config_manager
    config_manager.sync_table("sources")
    return jsonify({"status": "ok"})


@sources_bp.route("/api/sources/<int:sid>", methods=["DELETE"])
def api_delete_source(sid):
    old = db.get_source(sid)
    sm = current_app.source_mgr
    if sm and old and old.get("port") and not old.get("parent_id"):
        sm.stop_source(sid)
    db.delete_source(sid)
    import config_manager
    config_manager.sync_table("sources")
    # delete_source 会级联删除子路由及其通道绑定，bindings 也需同步
    config_manager.sync_table("bindings")
    return jsonify({"status": "ok"})


@sources_bp.route("/api/sources/bindings", methods=["GET"])
def api_all_source_channels():
    return jsonify(db.get_all_source_channels())


@sources_bp.route("/api/sources/<int:sid>/channels", methods=["POST"])
def api_save_source_channels(sid):
    data = request.json
    items = data if isinstance(data, list) else [data]
    existing = db.get_source_channels(sid)
    for sc in existing:
        db.delete_source_channel(sc["id"])
    for item in items:
        db.create_source_channel(
            sid, item["channel_id"], item["template_id"],
            condition_expr=item.get("condition_expr", ""),
            priority=item.get("priority", 0),
            enabled=item.get("enabled", 1),
            urgent=item.get("urgent", 0),
            dedup_key_expr=item.get("dedup_key_expr", ""),
            dedup_window=item.get("dedup_window", 3600),
        )
    import config_manager
    config_manager.sync_table("bindings")
    return jsonify({"status": "ok", "count": len(items)})


@sources_bp.route("/api/sources/<int:sid>/samples", methods=["GET"])
def api_source_samples(sid):
    import source_manager as sm
    count = request.args.get("count", 10, type=int)
    samples = sm.get_samples(sid, count)
    return jsonify(samples)


@sources_bp.route("/api/sources/<int:sid>/test-parse", methods=["POST"])
def api_source_test_parse(sid):
    data = request.json
    sample_body = data.get("body", "")
    sample_headers = data.get("headers", {})
    sample_query = data.get("query_params", {})
    src = db.get_source(sid)
    if not src:
        return jsonify({"ok": False, "error": i18n._("err.source_not_found")}), 404
    if not src.get("parser_id"):
        return jsonify({"ok": False, "error": i18n._("err.no_parser_bound")})
    parser = db.get_parser(src["parser_id"])
    if not parser:
        return jsonify({"ok": False, "error": i18n._("err.parser_not_found")})
    try:
        raw_body = sample_body.encode("utf-8")
        result = parser_loader.run_parser(parser["filename"], raw_body, sample_headers, sample_query)
        return jsonify({"ok": True, "result": result})
    except Exception as e:
        log.logger.error(f"[test-parse] sid={sid}: {e}")
        return jsonify({"ok": False, "error": str(e)})


@sources_bp.route("/api/sources/<int:sid>/test-push", methods=["POST"])
def api_source_test_push(sid):
    import source_manager as sm
    data = request.json
    sample_body = data.get("body", "")
    sample_headers = data.get("headers", {})
    sample_query = data.get("query_params", {})
    src = db.get_source(sid)
    if not src:
        return jsonify({"ok": False, "error": i18n._("err.source_not_found")}), 404
    if not src.get("parser_id"):
        return jsonify({"ok": False, "error": i18n._("err.no_parser_bound")})
    try:
        raw_body = sample_body.encode("utf-8")
        ok, msg = sm.process_message(sid, raw_body, sample_headers, sample_query)
        return jsonify({"ok": ok, "message": i18n._("src.push_ok") if ok else i18n._("src.push_fail")})
    except Exception as e:
        log.logger.error(f"[test-push] sid={sid}: {e}")
        return jsonify({"ok": False, "error": str(e)})

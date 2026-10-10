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
    sid = db.create_source(
        name=require_name(data),
        port=port,
        parser_id=optional_int(data, "parser_id", 1, default=None),
        enabled=optional_flag(data, "enabled", default=1),
        slug=optional_slug(data),
        parent_id=optional_int(data, "parent_id", 1, default=None),
        path=optional_str(data, "path", max_len=200, default="") or "",
    )
    if sid is None:
        return jsonify({"error": i18n._("err.port_in_use")}), 400
    # Only start listener for port-mode sources (no parent, has port)
    sm = current_app.source_mgr
    if sm and port and not data.get("parent_id"):
        sm.start_source(sid)
    import config_manager
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

    # 仅端口模式的顶层 Source 需要启动监听
    sm = current_app.source_mgr
    if sm and port and not parent_id:
        sm.start_source(sid)

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

    sm = current_app.source_mgr
    # Only stop/start listener for port-mode sources
    if sm and old.get("port") and not old.get("parent_id"):
        sm.stop_source(sid)
    db.update_source(sid, **patch)
    if sm and old.get("port") and not old.get("parent_id") and data.get("enabled", old["enabled"]):
        sm.start_source(sid)
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

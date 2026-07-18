#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""api/sources.py — 数据源 CRUD + 绑定 + 样本 + 测试"""

import os
import log
import db
import parser_loader
import i18n
from flask import Blueprint, request, jsonify, current_app

sources_bp = Blueprint("sources", __name__)

PARSERS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "parsers")


@sources_bp.route("/api/sources", methods=["GET"])
def api_sources():
    sources = db.get_sources()
    for s in sources:
        p = db.get_parser(s.get("parser_id"))
        s["parser_name"] = p["name"] if p else "-"
        s["channels"] = db.get_source_channels(s["id"])
    return jsonify(sources)


@sources_bp.route("/api/sources", methods=["POST"])
def api_create_source():
    data = request.json
    sid = db.create_source(data["name"], data["port"], data.get("parser_id"), data.get("enabled", 1))
    if sid is None:
        return jsonify({"error": i18n._("err.port_in_use")}), 400
    sm = current_app.source_mgr
    if sm:
        sm.start_source(sid)
    import config_manager
    config_manager.sync_table("sources")
    return jsonify({"id": sid})


@sources_bp.route("/api/sources/<int:sid>", methods=["PUT"])
def api_update_source(sid):
    data = request.json
    old = db.get_source(sid)
    if not old:
        return jsonify({"error": i18n._("err.not_found")}), 404
    sm = current_app.source_mgr
    if sm:
        sm.stop_source(sid)
    db.update_source(sid, **{k: v for k, v in data.items() if k in ("name", "port", "parser_id", "enabled")})
    if sm and data.get("enabled", old["enabled"]):
        sm.start_source(sid)
    import config_manager
    config_manager.sync_table("sources")
    return jsonify({"status": "ok"})


@sources_bp.route("/api/sources/<int:sid>", methods=["DELETE"])
def api_delete_source(sid):
    sm = current_app.source_mgr
    if sm:
        sm.stop_source(sid)
    db.delete_source(sid)
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

#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""api/messages.py — 消息查询/操作/批量/清理/队列刷新"""

import log
import db
import i18n
from flask import Blueprint, request, jsonify

messages_bp = Blueprint("messages", __name__)


@messages_bp.route("/api/messages", methods=["GET"])
def api_get_messages():
    limit = request.args.get("limit", 100, type=int)
    offset = request.args.get("offset", 0, type=int)
    sid = request.args.get("source_id", type=int)
    status = request.args.get("status")
    ch_type = request.args.get("channel_type")
    msgs = db.get_messages(source_id=sid, status=status, channel_type=ch_type,
                           limit=limit, offset=offset)
    total = db.get_message_count(source_id=sid, status=status)
    pending = db.get_message_count(status="PENDING")
    failed = db.get_message_count(status="FAILED")
    return jsonify({"messages": msgs, "total": total, "pending": pending, "failed": failed})


@messages_bp.route("/api/messages/<int:msg_id>", methods=["DELETE"])
def api_delete_message(msg_id):
    db.delete_message(msg_id)
    return jsonify({"status": "ok"})


@messages_bp.route("/api/messages/<int:msg_id>/retry", methods=["POST"])
def api_retry_message(msg_id):
    import source_manager as sm
    mode = request.args.get("mode", "original")
    ok, err = sm.retry_message(msg_id, mode)
    return jsonify({"ok": ok, "error": err} if err else {"ok": ok})


@messages_bp.route("/api/messages/<int:msg_id>/ignore", methods=["POST"])
def api_ignore_message(msg_id):
    db.mark_ignored(msg_id)
    return jsonify({"status": "ok"})


@messages_bp.route("/api/messages/batch", methods=["POST"])
def api_batch_messages():
    import source_manager as sm
    data = request.json
    action = data.get("action")
    ids = data.get("ids", [])
    mode = data.get("mode", "original")
    if not ids:
        return jsonify({"ok": False, "error": i18n._("err.no_ids")})

    results = {"ok": 0, "fail": 0, "errors": []}
    for mid in ids:
        if action == "retry":
            ok, err = sm.retry_message(mid, mode)
            if ok:
                results["ok"] += 1
            else:
                results["fail"] += 1
                results["errors"].append({"id": mid, "error": err})
        elif action == "ignore":
            db.mark_ignored(mid)
            results["ok"] += 1
        elif action == "delete":
            db.delete_message(mid)
            results["ok"] += 1
        else:
            return jsonify({"ok": False, "error": i18n._("err.unknown_action").replace("{action}", action)})

    return jsonify(results)


@messages_bp.route("/api/messages/cleanup", methods=["POST"])
def api_cleanup_messages():
    data = request.json or {}
    overrides = data.get("overrides")
    if overrides:
        overrides = {k: int(v) for k, v in overrides.items()}
    count = db.cleanup_old_messages(overrides)
    return jsonify({"deleted": count})


@messages_bp.route("/api/queue/flush", methods=["POST"])
def api_flush_queue():
    import source_manager as sm
    total = 0
    for s in db.get_sources():
        if s["enabled"]:
            try:
                count = sm.flush_queue_for_source(s["id"])
                total += count
            except Exception as e:
                log.logger.error(f"[API] Flush source {s['name']} error: {e}")
    return jsonify({"count": total})

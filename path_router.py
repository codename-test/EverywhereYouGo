#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
path_router.py — Flask path-based webhook routing.
Handles POST /<prefix>/<slug> and POST /<prefix>/<slug>/<sub_path>.
Registered on the main Flask app (same port as WebUI).
"""

import log
import db
from flask import request, jsonify


def _get_prefix():
    """Read the global path prefix from system_config."""
    conn = db._conn()
    r = conn.execute(
        "SELECT value FROM system_config WHERE key='path_prefix'"
    ).fetchone()
    return r[0].strip("/") if r and r[0].strip() else "in"


def _handle_webhook(slug, sub_path=""):
    """Core handler: resolve source group + sub-route, process message."""
    import source_manager

    # 1. Find source group by slug
    group = db.get_source_by_slug(slug)
    if not group:
        return jsonify({"error": "source not found"}), 404
    if not group.get("enabled", 1):
        return jsonify({"error": "source disabled"}), 403

    # 2. Find matching sub-route
    sub_route = db.get_sub_route_by_path(group["id"], sub_path)
    if not sub_route:
        return jsonify({"error": "no matching route"}), 404
    if not sub_route.get("enabled", 1):
        return jsonify({"error": "route disabled"}), 403

    # 3. Body size check (5MB)
    content_length = request.content_length or 0
    if content_length > 5 * 1024 * 1024:
        return jsonify({"error": "payload too large"}), 413

    # 4. Extract request data
    raw_body = request.get_data()
    headers = dict(request.headers)
    query_params = dict(request.args)

    # 5. Process through the pipeline (uses sub-route's id as source_id)
    try:
        ok, msg = source_manager.process_message(
            source_id=sub_route["id"],
            raw_body=raw_body,
            headers=headers,
            query_params=query_params,
            extra_fields={"sub_path": sub_path, "source_slug": slug},
        )
        if ok:
            return jsonify({"status": "ok", "trace_id": msg.get("_trace_id", "") if msg else ""}), 200
        else:
            return jsonify({"status": "accepted"}), 200
    except Exception as e:
        log.logger.error(f"Path router error [{slug}/{sub_path}]: {e}")
        return jsonify({"status": "accepted"}), 200


def register_routes(app):
    """Register path routing endpoints on the Flask app."""
    prefix = _get_prefix()

    # Store prefix on app for auth middleware whitelist
    app._path_prefix = prefix

    # Route without sub-path: POST /<prefix>/<slug>
    @app.route(f"/{prefix}/<slug>", methods=["POST"])
    def webhook_base(slug):
        return _handle_webhook(slug, "")

    # Route with sub-path: POST /<prefix>/<slug>/<path:sub_path>
    @app.route(f"/{prefix}/<slug>/<path:sub_path>", methods=["POST"])
    def webhook_sub(slug, sub_path):
        return _handle_webhook(slug, sub_path)

    # Add to auth whitelist (webhook receivers don't need WebUI auth)
    log.logger.info(f"Path router registered: POST /{prefix}/<slug>[/<sub_path>]")

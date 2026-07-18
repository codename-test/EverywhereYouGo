#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""api/logs.py — 日志查询/清理"""

import db
from flask import Blueprint, request, jsonify

logs_bp = Blueprint("logs", __name__)


@logs_bp.route("/api/logs", methods=["GET"])
def api_logs():
    level = request.args.get("level")
    limit = request.args.get("limit", 200, type=int)
    return jsonify(db.get_logs(level=level, limit=limit))


@logs_bp.route("/api/logs", methods=["DELETE"])
def api_clear_logs():
    db.clear_logs()
    return jsonify({"status": "ok"})

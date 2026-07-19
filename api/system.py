#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""api/system.py — 健康检查/设置/版本"""

import db
import version_checker
from flask import Blueprint, request, jsonify

system_bp = Blueprint("system", __name__)


@system_bp.route("/api/settings", methods=["POST"])
def api_update_settings():
    data = request.json
    for k, v in data.items():
        if k == "log_level":
            db.set_log_level(v)
        else:
            db.set_config(k, v)
    return jsonify({"status": "ok"})


@system_bp.route("/api/version/check", methods=["GET"])
def api_version_check():
    """获取缓存的版本信息。"""
    return jsonify(version_checker.get_cache())


@system_bp.route("/api/version/check", methods=["POST"])
def api_version_check_now():
    """立即检查 GitHub 最新版本。"""
    has_update, info = version_checker.check_now()
    return jsonify(info)

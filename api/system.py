#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""api/system.py — 健康检查/语言切换/设置"""

import db
import i18n
from flask import Blueprint, request, jsonify

system_bp = Blueprint("system", __name__)


@system_bp.route("/api/health")
def api_health():
    return jsonify({"status": "ok"})


@system_bp.route("/api/lang", methods=["GET", "POST"])
def api_lang():
    if request.method == "GET":
        return jsonify({"lang": i18n.get_lang(), "supported": i18n.SUPPORTED_LANGS})
    data = request.json or {}
    lang = data.get("lang", i18n.DEFAULT_LANG)
    if lang not in i18n.SUPPORTED_LANGS:
        return jsonify({"error": i18n._("err.unsupported_lang")}), 400
    i18n.set_lang(lang)
    resp = jsonify({"lang": lang})
    resp.set_cookie("lang", lang, max_age=365 * 24 * 3600, samesite="Lax")
    return resp


@system_bp.route("/api/settings", methods=["POST"])
def api_update_settings():
    data = request.json
    for k, v in data.items():
        if k == "log_level":
            db.set_log_level(v)
        else:
            db.set_config(k, v)
    return jsonify({"status": "ok"})

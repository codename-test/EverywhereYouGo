#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
api/__init__.py — Flask app 初始化 + 蓝图注册。
"""

import os
import secrets
import hmac
from datetime import timedelta

import log
import i18n
from flask import Flask, request, jsonify, session, redirect


def create_app(source_mgr=None):
    """创建并配置 Flask app。"""
    app = Flask(__name__,
                template_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates"))
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.jinja_env.globals["_"] = i18n._

    # ── Secret Key + Session 过期 ──
    secret = os.getenv("EGO_SECRET_KEY", "")
    if not secret:
        secret = secrets.token_hex(16)
        log.logger.warning("EGO_SECRET_KEY not set, using random key (sessions invalidated on restart). "
                           "Set EGO_SECRET_KEY env var for persistent sessions.")
    elif len(secret) < 16:
        log.logger.warning("EGO_SECRET_KEY is too short (< 16 chars). Consider using a stronger key.")

    app.secret_key = secret
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=24)

    # ── 认证 ──
    AUTH_TOKEN = os.getenv("EGO_AUTH_TOKEN", "")
    _AUTH_WHITELIST = {"/login", "/logout", "/api/health", "/api/lang"}

    def _is_authenticated():
        if not AUTH_TOKEN:
            return True
        if session.get("authenticated"):
            return True
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and hmac.compare_digest(auth[7:], AUTH_TOKEN):
            return True
        return False

    @app.before_request
    def _auth_middleware():
        if request.path in _AUTH_WHITELIST or request.path.startswith("/static"):
            return
        # Path routing endpoints bypass WebUI auth (webhook receivers)
        prefix = getattr(app, "_path_prefix", "in")
        if request.path.startswith(f"/{prefix}/") or request.path == f"/{prefix}":
            return
        if not _is_authenticated():
            if request.path.startswith("/api/"):
                return jsonify({"error": i18n._("err.unauthorized")}), 401
            return redirect("/login")

    # ── 注册蓝图 ──
    from api.auth import auth_bp
    from api.sources import sources_bp
    from api.parsers import parsers_bp
    from api.channels import channels_bp
    from api.templates import templates_bp
    from api.messages import messages_bp
    from api.logs import logs_bp
    from api.system import system_bp
    from api.backup import backup_bp
    from api.pages import pages_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(sources_bp)
    app.register_blueprint(parsers_bp)
    app.register_blueprint(channels_bp)
    app.register_blueprint(templates_bp)
    app.register_blueprint(messages_bp)
    app.register_blueprint(logs_bp)
    app.register_blueprint(system_bp)
    app.register_blueprint(backup_bp)
    app.register_blueprint(pages_bp)

    # ── 注入 source_mgr ──
    app.source_mgr = source_mgr
    app.auth_token = AUTH_TOKEN

    return app

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

    # ── CSRF 缓解（最小化，#24）──
    # 内网自管理场景无需引入完整 Flask-WTF CSRF：
    #   SameSite=Lax 阻断跨站顶层表单提交（主要 CSRF 向量），
    #   HttpOnly 禁止 JS 读取 Cookie。
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    # 仅在启用 HTTPS 时追加 Secure（禁止明文传 Cookie），
    # 证书探测与 web_ui.py 保持一致。
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _cert = os.getenv("EGO_SSL_CERT", os.path.join(_root, "certs", "ego.crt"))
    _key = os.getenv("EGO_SSL_KEY", os.path.join(_root, "certs", "ego.key"))
    _ssl_env = os.getenv("EGO_SSL_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")
    _ssl_enabled = _ssl_env and os.path.isfile(_cert) and os.path.isfile(_key)
    app.config["EGO_SSL_ENABLED"] = _ssl_enabled
    app.config["EGO_SSL_PORT"] = int(os.getenv("WEB_SSL_PORT", "5001"))
    if _ssl_enabled:
        app.config["SESSION_COOKIE_SECURE"] = True

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
    def _https_redirect():
        """管理页面强制 HTTPS：HTTP 访问 301 跳转到 HTTPS 端口。

        webhook 接收器（/in/...）与健康检查保持 HTTP，不跳转。
        仅对幂等的 GET/HEAD 跳转，避免改变 POST 语义。
        """
        if not app.config.get("EGO_SSL_ENABLED"):
            return
        if request.scheme == "https":
            return
        path = request.path
        if path.startswith("/static"):
            return
        prefix = getattr(app, "_path_prefix", "in")
        if path.startswith(f"/{prefix}/") or path == f"/{prefix}":
            return
        if path == "/api/health":
            return
        if request.method not in ("GET", "HEAD"):
            return
        host = request.host.split(":")[0]
        ssl_port = app.config.get("EGO_SSL_PORT", 5001)
        qs = f"?{request.query_string.decode()}" if request.query_string else ""
        return redirect(f"https://{host}:{ssl_port}{path}{qs}", code=301)

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

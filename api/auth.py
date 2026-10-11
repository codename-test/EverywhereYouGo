#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""api/auth.py — 登录/登出"""

import hmac
import i18n
from flask import Blueprint, request, session, redirect, render_template, current_app, jsonify

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET"])
def login_page():
    auth_token = current_app.auth_token
    if not auth_token or session.get("authenticated"):
        return redirect("/")
    return render_template("login.html", title=i18n._("login.title"), error=None, lang=i18n.get_lang())


@auth_bp.route("/login", methods=["POST"])
def login_action():
    # P0-2 边界：带 Bearer 头的是 API 调用方，禁止经 /login 换交互式 session，
    # 以免持集成 token 的程序登录后绕过 guard 的写限制。人工登录走表单，无此头。
    if request.headers.get("Authorization", "").startswith("Bearer "):
        return jsonify({"error": "API token cannot establish a web session"}), 403
    token = request.form.get("token", "")
    auth_token = current_app.auth_token
    if auth_token and hmac.compare_digest(token, auth_token):
        session.permanent = True  # 启用 PERMANENT_SESSION_LIFETIME（24h）
        session["authenticated"] = True
        return redirect(request.args.get("next", "/"))
    return render_template("login.html", title=i18n._("login.title"),
                           error=i18n._("login.token_invalid"), lang=i18n.get_lang()), 401


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

@auth_bp.route("/api/lang", methods=["POST"])
def api_set_lang():
    data = request.get_json(silent=True) or {}
    lang = data.get("lang", "zh")
    i18n.set_lang(lang)
    return {"status": "ok", "lang": lang}

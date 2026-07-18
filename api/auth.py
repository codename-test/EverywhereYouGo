#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""api/auth.py — 登录/登出"""

import hmac
import i18n
from flask import Blueprint, request, session, redirect, render_template, current_app

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET"])
def login_page():
    auth_token = current_app.auth_token
    if not auth_token or session.get("authenticated"):
        return redirect("/")
    return render_template("login.html", title=i18n._("login.title"), error=None, lang=i18n.get_lang())


@auth_bp.route("/login", methods=["POST"])
def login_action():
    token = request.form.get("token", "")
    auth_token = current_app.auth_token
    if auth_token and hmac.compare_digest(token, auth_token):
        session["authenticated"] = True
        return redirect(request.args.get("next", "/"))
    return render_template("login.html", title=i18n._("login.title"),
                           error=i18n._("login.token_invalid"), lang=i18n.get_lang()), 401


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

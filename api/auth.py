#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""api/auth.py — 登录/登出"""

import hmac
import time
import threading
import i18n
from flask import Blueprint, request, session, redirect, render_template, current_app, jsonify

auth_bp = Blueprint("auth", __name__)


# ── 登录暴力破解防护：per-IP 失败计数（review #2）──
_login_lock = threading.Lock()
_login_fails = {}                 # ip -> [失败时间戳]
_LOGIN_MAX_FAILS = 5
_LOGIN_WINDOW = 300               # 秒
_LOGIN_MAX_KEYS = 10000           # 防 dict 无限增长


def _login_throttled(ip):
    now = time.time()
    with _login_lock:
        fails = [t for t in _login_fails.get(ip, []) if now - t < _LOGIN_WINDOW]
        _login_fails[ip] = fails
        return len(fails) >= _LOGIN_MAX_FAILS


def _login_record_fail(ip):
    now = time.time()
    with _login_lock:
        if len(_login_fails) > _LOGIN_MAX_KEYS:
            cutoff = now - _LOGIN_WINDOW
            for k in [k for k, v in _login_fails.items() if not v or v[-1] < cutoff]:
                del _login_fails[k]
        _login_fails.setdefault(ip, []).append(now)


def _login_clear(ip):
    with _login_lock:
        _login_fails.pop(ip, None)


def _safe_next(nxt):
    """只允许站内相对跳转，防开放重定向（review #1）。"""
    if not nxt or not nxt.startswith("/"):
        return "/"
    if nxt.startswith("//") or nxt.startswith("/\\"):
        return "/"
    return nxt


def _client_ip():
    """取真实客户端 IP：应用挂在 nginx 反代后，remote_addr 会是代理 IP（全员共享，
    一人失败会锁全站）。与 _https_redirect 一致地信任 X-Forwarded-For 首跳。"""
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "?"


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
    ip = _client_ip()
    if _login_throttled(ip):
        return render_template("login.html", title=i18n._("login.title"),
                               error=i18n._("login.too_many_attempts"), lang=i18n.get_lang()), 429
    token = request.form.get("token", "")
    auth_token = current_app.auth_token
    if auth_token and hmac.compare_digest(token, auth_token):
        _login_clear(ip)
        session.permanent = True  # 启用 PERMANENT_SESSION_LIFETIME（24h）
        session["authenticated"] = True
        return redirect(_safe_next(request.args.get("next", "/")))
    _login_record_fail(ip)
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

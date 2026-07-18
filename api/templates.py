#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""api/templates.py — 模板 CRUD + 测试渲染"""

import db
import renderer
import i18n
from flask import Blueprint, request, jsonify

templates_bp = Blueprint("templates", __name__)


@templates_bp.route("/api/templates", methods=["GET"])
def api_templates():
    return jsonify(db.get_templates())


@templates_bp.route("/api/templates", methods=["POST"])
def api_create_template():
    data = request.json
    tid = db.create_template(
        data["name"], data.get("engine", "jinja2"),
        data.get("title_tpl", ""), data.get("content_tpl", ""))
    return jsonify({"id": tid})


@templates_bp.route("/api/templates/<int:tid>", methods=["PUT"])
def api_update_template(tid):
    data = request.json
    db.update_template(tid, **{k: v for k, v in data.items()
                             if k in ("name", "engine", "title_tpl", "content_tpl")})
    return jsonify({"status": "ok"})


@templates_bp.route("/api/templates/test-render", methods=["POST"])
def api_template_test_render():
    data = request.json
    if not data:
        return jsonify({"ok": False, "error": i18n._("err.no_data")}), 400
    engine = data.get("engine", "simple")
    title_tpl = data.get("title_tpl", "")
    content_tpl = data.get("content_tpl", "")
    msg = data.get("msg", {})
    try:
        result = renderer.render_template(engine, title_tpl, content_tpl, msg)
        return jsonify({"ok": True, "result": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

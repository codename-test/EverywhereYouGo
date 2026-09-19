#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""api/templates.py — 模板 CRUD + 测试渲染"""

import db
import renderer
import i18n
from flask import Blueprint, request, jsonify
from api.validation import (
    require_name, optional_str, optional_enum, TEMPLATE_ENGINES,
)

templates_bp = Blueprint("templates", __name__)


@templates_bp.route("/api/templates", methods=["GET"])
def api_templates():
    return jsonify(db.get_templates())


@templates_bp.route("/api/templates", methods=["POST"])
def api_create_template():
    data = request.json or {}
    tid = db.create_template(
        require_name(data),
        optional_enum(data, "engine", TEMPLATE_ENGINES, default="jinja2"),
        optional_str(data, "title_tpl", max_len=10000, default="") or "",
        optional_str(data, "content_tpl", max_len=50000, default="") or "",
    )
    return jsonify({"id": tid})


@templates_bp.route("/api/templates/<int:tid>", methods=["PUT"])
def api_update_template(tid):
    data = request.json or {}
    patch = {}
    if "name" in data:
        patch["name"] = require_name(data)
    if "engine" in data:
        patch["engine"] = optional_enum(data, "engine", TEMPLATE_ENGINES, default="jinja2")
    if "title_tpl" in data:
        patch["title_tpl"] = optional_str(data, "title_tpl", max_len=10000, default="") or ""
    if "content_tpl" in data:
        patch["content_tpl"] = optional_str(data, "content_tpl", max_len=50000, default="") or ""
    if patch:
        db.update_template(tid, **patch)
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

#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""api/parsers.py — 解析器 CRUD + 内容编辑 + 变量提取"""

import os
import re
import db
import parser_loader
import i18n
from flask import Blueprint, request, jsonify

parsers_bp = Blueprint("parsers", __name__)

PARSERS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "parsers")


@parsers_bp.route("/api/parsers", methods=["GET"])
def api_parsers():
    parsers = db.get_parsers()
    for p in parsers:
        p["exists"] = os.path.isfile(os.path.join(PARSERS_DIR, p["filename"]))
    return jsonify(parsers)


@parsers_bp.route("/api/parsers", methods=["POST"])
def api_create_parser():
    if "name" not in request.form:
        return jsonify({"error": i18n._("err.missing_name")}), 400
    name = request.form["name"]
    desc = request.form.get("description", "")
    if "file" not in request.files:
        return jsonify({"error": i18n._("err.no_file")}), 400
    f = request.files["file"]
    if not f.filename.endswith(".py"):
        return jsonify({"error": i18n._("err.py_only")}), 400
    filename = f.filename
    filepath = os.path.join(PARSERS_DIR, filename)
    f.save(filepath)
    pid = db.create_parser(name, filename, desc)
    if pid is None:
        return jsonify({"error": i18n._("err.parser_exists")}), 400
    return jsonify({"id": pid})


@parsers_bp.route("/api/parsers/<int:pid>", methods=["DELETE"])
def api_delete_parser(pid):
    p = db.get_parser(pid)
    if not p:
        return jsonify({"error": i18n._("err.not_found")}), 404
    filepath = os.path.join(PARSERS_DIR, p["filename"])
    if os.path.isfile(filepath):
        os.remove(filepath)
    db.delete_parser(pid)
    import config_manager
    config_manager.sync_table("parsers")
    return jsonify({"status": "ok"})


@parsers_bp.route("/api/parsers/<int:pid>/content", methods=["GET"])
def api_get_parser_content(pid):
    p = db.get_parser(pid)
    if not p:
        return jsonify({"error": i18n._("err.not_found")}), 404
    fpath = os.path.join(PARSERS_DIR, p["filename"])
    if not os.path.isfile(fpath):
        return jsonify({"error": i18n._("err.file_not_found")}), 404
    with open(fpath, "r", encoding="utf-8") as f:
        return jsonify({"filename": p["filename"], "content": f.read()})


@parsers_bp.route("/api/parsers/<int:pid>/content", methods=["PUT"])
def api_update_parser_content(pid):
    p = db.get_parser(pid)
    if not p:
        return jsonify({"error": i18n._("err.not_found")}), 404
    data = request.json
    if "content" not in data:
        return jsonify({"error": i18n._("err.missing_content")}), 400
    fpath = os.path.join(PARSERS_DIR, p["filename"])
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(data["content"])
    try:
        parser_loader.reload_parser(p["filename"])
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": i18n._("err.syntax_error").replace("{error}", str(e))}), 400


@parsers_bp.route("/api/parsers/<int:pid>/variables", methods=["GET"])
def api_parser_variables(pid):
    p = db.get_parser(pid)
    if not p:
        return jsonify({"ok": False, "error": i18n._("err.not_found")}), 404
    fpath = os.path.join(PARSERS_DIR, p["filename"])
    if not os.path.isfile(fpath):
        return jsonify({"ok": False, "error": i18n._("err.file_not_found")}), 404
    with open(fpath, "r", encoding="utf-8") as f:
        content = f.read()
    match = re.search(r"return\s*\{([\s\S]*?)\}", content)
    keys = []
    if match:
        keys = list(set(re.findall(r'"([^"]+)"', match.group(1))))
    return jsonify({"ok": True, "filename": p["filename"],
                    "variables": [{"path": k, "type": "str", "sample": ""} for k in keys]})

#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""api/parsers.py — 解析器 CRUD + 变量提取

插件目录已拆分（见 plugin_paths.py）：
  - 上传写入**用户目录** `parsers/`（Docker 里挂 Volume，容器重建不丢）
  - 内置解析器在 `parsers_builtin/`，随镜像更新 → **只读**，不可改/删
"""

import os
import re
import db
import i18n
import log
import parser_loader
import plugin_paths
from flask import Blueprint, request, jsonify

parsers_bp = Blueprint("parsers", __name__)


def _decorate(p):
    """给解析器记录补上文件状态、来源（内置 / 用户）与版本号。"""
    fn = p.get("filename")
    path = plugin_paths.resolve("parser", fn)
    p["exists"] = path is not None
    p["source"] = plugin_paths.source_of("parser", fn) or "missing"
    p["shadow_builtin"] = plugin_paths.shadows_builtin("parser", fn)
    p["version"] = ""
    if path:
        meta = plugin_paths.read_source_meta(path, "PARSER")
        p["version"] = meta.get("version", "")
        if meta.get("name"):
            # 源码里的名字更权威（内置解析器随镜像更新可能改名）
            p["name"] = meta["name"]
    return p


def _reject_builtin(filename):
    """内置插件只读，返回错误响应；不是内置则返回 None。"""
    if plugin_paths.is_builtin("parser", filename):
        return jsonify({"error": i18n._("err.plugin_builtin_readonly")
                        .replace("{name}", filename)}), 400
    return None


@parsers_bp.route("/api/parsers", methods=["GET"])
def api_parsers():
    return jsonify([_decorate(p) for p in db.get_parsers()])


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

    filename = os.path.basename(f.filename)
    reason = plugin_paths.conflict_reason("parser", filename)
    if reason:
        return jsonify({"error": reason}), 400

    # 同 filename 已存在则直接拒绝（避免覆盖已有用户解析器）
    if any(p["filename"] == filename for p in db.get_parsers()):
        return jsonify({"error": i18n._("err.parser_exists")}), 400

    err = plugin_paths.atomic_upload("parser", filename, f)
    if err:
        return jsonify({"error": err}), 400
    pid = db.create_parser(name, filename, desc)

    if pid is None:
        return jsonify({"error": i18n._("err.parser_exists")}), 400
    log.logger.info(f"Parser uploaded: {filename} (validated + atomic)")
    return jsonify({"id": pid})


@parsers_bp.route("/api/parsers/<int:pid>", methods=["DELETE"])
def api_delete_parser(pid):
    p = db.get_parser(pid)
    if not p:
        return jsonify({"error": i18n._("err.not_found")}), 404
    rejected = _reject_builtin(p["filename"])
    if rejected:
        return rejected

    filepath = plugin_paths.resolve("parser", p["filename"])
    if filepath and plugin_paths.source_of("parser", p["filename"]) == "user":
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
    fpath = plugin_paths.resolve("parser", p["filename"])
    if not fpath:
        return jsonify({"error": i18n._("err.file_not_found")}), 404
    with open(fpath, "r", encoding="utf-8") as f:
        return jsonify({"filename": p["filename"],
                        "source": plugin_paths.source_of("parser", p["filename"]),
                        "content": f.read()})


@parsers_bp.route("/api/parsers/<int:pid>/content", methods=["PUT"])
def api_update_parser_content(pid):
    p = db.get_parser(pid)
    if not p:
        return jsonify({"error": i18n._("err.not_found")}), 404
    rejected = _reject_builtin(p["filename"])
    if rejected:
        return rejected
    data = request.json or {}
    if "content" not in data:
        return jsonify({"error": i18n._("err.missing_content")}), 400

    # 原子写 + 验证：失败则旧内容保持不变，成功才替换
    err = plugin_paths.atomic_write_string("parser", p["filename"], data["content"])
    if err:
        return jsonify({"error": err}), 400
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
    fpath = plugin_paths.resolve("parser", p["filename"])
    if not fpath:
        return jsonify({"ok": False, "error": i18n._("err.file_not_found")}), 404
    with open(fpath, "r", encoding="utf-8") as f:
        content = f.read()
    match = re.search(r"return\s*\{([\s\S]*?)\}", content)
    keys = []
    if match:
        keys = list(set(re.findall(r'"([^"]+)"', match.group(1))))
    return jsonify({"ok": True, "filename": p["filename"],
                    "variables": [{"path": k, "type": "str", "sample": ""} for k in keys]})

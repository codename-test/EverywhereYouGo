#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""api/channels.py — 通道 CRUD + 插件管理"""

import os
import db
import channel_loader
import i18n
from flask import Blueprint, request, jsonify

channels_bp = Blueprint("channels", __name__)

CHANNELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "channels")


@channels_bp.route("/api/channels", methods=["GET"])
def api_channels():
    return jsonify(db.get_channels())


@channels_bp.route("/api/channels", methods=["POST"])
def api_create_channel():
    data = request.json
    cid = db.create_channel(data["name"], data["type"], data.get("config", {}))
    import config_manager
    config_manager.sync_table("channels")
    return jsonify({"id": cid})


@channels_bp.route("/api/channels/<int:cid>", methods=["PUT"])
def api_update_channel(cid):
    data = request.json
    db.update_channel(cid, **data)
    return jsonify({"status": "ok"})


# ── Channel Plugins ──


@channels_bp.route("/api/channel_plugins", methods=["GET"])
def api_channel_plugins():
    plugins = channel_loader.list_plugins()
    for p in plugins:
        try:
            mod = channel_loader.load_plugin(p["filename"])
            cls = mod.Channel
            p["channel_name"] = getattr(cls, "CHANNEL_NAME", p["name"])
            p["channel_type"] = getattr(cls, "CHANNEL_TYPE", p["name"])
            p["config_fields"] = getattr(cls, "CONFIG_FIELDS", [])
        except Exception as e:
            p["channel_name"] = p["name"]
            p["channel_type"] = p["name"]
            p["config_fields"] = []
            p["load_error"] = str(e)
    return jsonify(plugins)


@channels_bp.route("/api/channel_plugins", methods=["POST"])
def api_create_channel_plugin():
    if "file" not in request.files:
        return jsonify({"error": i18n._("err.no_file")}), 400
    f = request.files["file"]
    if not f.filename.endswith(".py"):
        return jsonify({"error": i18n._("err.py_only")}), 400
    filename = f.filename
    filepath = os.path.join(CHANNELS_DIR, filename)
    if os.path.isfile(filepath):
        return jsonify({"error": i18n._("err.parser_exists")}), 400
    f.save(filepath)
    try:
        channel_loader.load_plugin(filename)
    except Exception as e:
        os.remove(filepath)
        return jsonify({"error": str(e)}), 400
    return jsonify({"filename": filename})


@channels_bp.route("/api/channel_plugins/<filename>", methods=["GET"])
def api_get_channel_plugin_content(filename):
    filepath = os.path.join(CHANNELS_DIR, filename)
    if not os.path.isfile(filepath):
        return jsonify({"error": i18n._("err.file_not_found")}), 404
    with open(filepath, "r", encoding="utf-8") as fh:
        return jsonify({"content": fh.read()})


@channels_bp.route("/api/channel_plugins/<filename>", methods=["PUT"])
def api_update_channel_plugin_content(filename):
    data = request.json
    if "content" not in data:
        return jsonify({"error": i18n._("err.missing_content")}), 400
    filepath = os.path.join(CHANNELS_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as fh:
        fh.write(data["content"])
    try:
        channel_loader.reload_plugin(filename)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": i18n._("err.syntax_error").replace("{error}", str(e))}), 400


@channels_bp.route("/api/channel_plugins/<filename>", methods=["DELETE"])
def api_delete_channel_plugin(filename):
    filepath = os.path.join(CHANNELS_DIR, filename)
    if os.path.isfile(filepath):
        os.remove(filepath)
    with channel_loader._channel_cache_lock:
        if filename in channel_loader._channel_cache:
            del channel_loader._channel_cache[filename]
    return jsonify({"status": "ok"})


@channels_bp.route("/api/channel_plugins/<filename>/test", methods=["POST"])
def api_test_channel_plugin(filename):
    config = request.json or {}
    result = channel_loader.test_channel(filename, config)
    return jsonify(result)


@channels_bp.route("/api/channel_plugins/<filename>/fields", methods=["GET"])
def api_channel_plugin_fields(filename):
    try:
        mod = channel_loader.load_plugin(filename)
        cls = mod.Channel
        fields = getattr(cls, "CONFIG_FIELDS", [])
        return jsonify({"fields": fields,
                        "channel_name": getattr(cls, "CHANNEL_NAME", ""),
                        "channel_type": getattr(cls, "CHANNEL_TYPE", "")})
    except Exception as e:
        return jsonify({"error": str(e)}), 404

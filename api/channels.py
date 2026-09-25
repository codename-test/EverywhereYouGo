#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""api/channels.py — 通道 CRUD + 插件管理"""

import os
import json
import log
import db
import channel_loader
import plugin_paths
import i18n
from flask import Blueprint, request, jsonify
from api.validation import require_name, optional_flag, ValidationError

channels_bp = Blueprint("channels", __name__)

# 插件目录已拆分（内置 channels_builtin/ 只读、用户 channels/ 挂 Volume），见 plugin_paths.py
CHANNELS_DIR = None   # 已废弃：请用 plugin_paths.user_dir("channel")


@channels_bp.route("/api/channels", methods=["GET"])
def api_channels():
    chans = db.get_channels()
    for c in chans:
        # 通道实例引用的插件可能已被删除 → 前端要能一眼看出它发不出去
        fn = plugin_paths.channel_filename(c.get("type", ""))
        c["plugin_missing"] = plugin_paths.resolve("channel", fn) is None
    return jsonify(chans)


def _channel_config(data):
    """通道 config 必须是 JSON 对象（允许传对象或 JSON 字符串）。"""
    cfg = (data or {}).get("config", {})
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg or "{}")
        except Exception:
            raise ValidationError("config must be a JSON object")
    if not isinstance(cfg, dict):
        raise ValidationError("config must be a JSON object")
    return cfg


@channels_bp.route("/api/channels", methods=["POST"])
def api_create_channel():
    data = request.json or {}
    cid = db.create_channel(
        require_name(data),
        require_name(data, key="type", max_len=64),
        _channel_config(data),
    )
    import config_manager
    config_manager.sync_table("channels")
    return jsonify({"id": cid})


@channels_bp.route("/api/channels/<int:cid>", methods=["PUT"])
def api_update_channel(cid):
    data = request.json or {}
    patch = {}
    if "name" in data:
        patch["name"] = require_name(data)
    if "type" in data:
        patch["type"] = require_name(data, key="type", max_len=64)
    if "config" in data:
        patch["config"] = _channel_config(data)
    if "enabled" in data:
        patch["enabled"] = optional_flag(data, "enabled")
    if patch:
        db.update_channel(cid, **patch)
    return jsonify({"status": "ok"})


@channels_bp.route("/api/channels/<int:cid>", methods=["DELETE"])
def api_delete_channel(cid):
    """删除通道（级联清理绑定、限流、熔断、去重键；待发任务移入死信）。"""
    if not db.get_channel(cid):
        return jsonify({"error": i18n._("err.not_found")}), 404
    db.delete_channel(cid)
    import config_manager
    config_manager.sync_table("channels")
    return jsonify({"status": "ok"})


@channels_bp.route("/api/channels/<int:cid>/test", methods=["POST"])
def api_test_channel(cid):
    """测试通道配置是否可用。前端期望 {ok: bool, error?}。"""
    ch = db.get_channel(cid)
    if not ch:
        return jsonify({"ok": False, "error": i18n._("err.not_found")}), 404
    try:
        cfg = _channel_config({"config": ch["config"]})
        result = channel_loader.create_channel(ch["type"], cfg).test()
        ok, err = channel_loader._unpack_test_result(result)
        return jsonify({"ok": ok,
                        "error": None if ok else (err or i18n._("ch.test_fail"))})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:300]})


@channels_bp.route("/api/channels/<int:cid>/duplicate", methods=["POST"])
def api_duplicate_channel(cid):
    """复制通道（默认禁用，避免复制出来就开始推送）。"""
    src = db.get_channel(cid)
    if not src:
        return jsonify({"error": i18n._("err.not_found")}), 404

    new_id = db.create_channel(src["name"] + " (copy)", src["type"],
                               src["config"], enabled=0)

    # 出站限流存在独立表里，不显式复制就会静默丢失
    try:
        from rate_limiter import get_limiter
        rate = get_limiter().get_rate(cid)
        if rate > 0:
            get_limiter().set_rate(new_id, rate)
    except Exception as e:
        log.logger.warning(f"Duplicate channel {cid}: rate limit not copied: {e}")

    import config_manager
    config_manager.sync_table("channels")
    return jsonify({"id": new_id})


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
            p["channel_version"] = getattr(cls, "CHANNEL_VERSION", "")
        except Exception as e:
            p["channel_name"] = p["name"]
            p["channel_type"] = p["name"]
            p["config_fields"] = []
            p["channel_version"] = ""
            p["load_error"] = str(e)
    return jsonify(plugins)


@channels_bp.route("/api/channel_plugins", methods=["POST"])
def api_create_channel_plugin():
    if "file" not in request.files:
        return jsonify({"error": i18n._("err.no_file")}), 400
    f = request.files["file"]
    if not f.filename.endswith(".py"):
        return jsonify({"error": i18n._("err.py_only")}), 400
    filename = os.path.basename(f.filename)
    reason = plugin_paths.conflict_reason("channel", filename)
    if reason:
        return jsonify({"error": reason}), 400

    plugin_paths.ensure_user_dirs()
    filepath = os.path.join(plugin_paths.user_dir("channel"), filename)
    f.save(filepath)
    try:
        channel_loader.reload_plugin(filename)
    except Exception as e:
        os.remove(filepath)
        return jsonify({"error": str(e)}), 400
    return jsonify({"filename": filename})


@channels_bp.route("/api/channel_plugins/<filename>", methods=["GET"])
def api_get_channel_plugin_content(filename):
    filename = os.path.basename(filename)
    filepath = plugin_paths.resolve("channel", filename)
    if not filepath:
        return jsonify({"error": i18n._("err.file_not_found")}), 404
    with open(filepath, "r", encoding="utf-8") as fh:
        return jsonify({"content": fh.read(),
                        "source": plugin_paths.source_of("channel", filename)})


@channels_bp.route("/api/channel_plugins/<filename>", methods=["PUT"])
def api_update_channel_plugin_content(filename):
    filename = os.path.basename(filename)
    if plugin_paths.is_builtin("channel", filename):
        return jsonify({"error": i18n._("err.plugin_builtin_readonly")
                        .replace("{name}", filename)}), 400
    data = request.json or {}
    if "content" not in data:
        return jsonify({"error": i18n._("err.missing_content")}), 400
    plugin_paths.ensure_user_dirs()
    filepath = plugin_paths.resolve("channel", filename) \
        or os.path.join(plugin_paths.user_dir("channel"), filename)
    with open(filepath, "w", encoding="utf-8") as fh:
        fh.write(data["content"])
    try:
        channel_loader.reload_plugin(filename)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": i18n._("err.syntax_error").replace("{error}", str(e))}), 400


@channels_bp.route("/api/channel_plugins/<filename>", methods=["DELETE"])
def api_delete_channel_plugin(filename):
    filename = os.path.basename(filename)
    if plugin_paths.is_builtin("channel", filename):
        return jsonify({"error": i18n._("err.plugin_builtin_readonly")
                        .replace("{name}", filename)}), 400
    user_file = os.path.join(plugin_paths.user_dir("channel"), filename)
    if os.path.isfile(user_file):
        os.remove(user_file)
    with channel_loader._channel_cache_lock:
        if filename in channel_loader._channel_cache:
            del channel_loader._channel_cache[filename]
    return jsonify({"status": "ok"})


@channels_bp.route("/api/channel_plugins/<filename>/test", methods=["POST"])
def api_test_channel_plugin(filename):
    filename = os.path.basename(filename)
    config = request.json or {}
    result = channel_loader.test_channel(filename, config)
    return jsonify(result)


@channels_bp.route("/api/channel_plugins/<filename>/fields", methods=["GET"])
def api_channel_plugin_fields(filename):
    filename = os.path.basename(filename)
    try:
        mod = channel_loader.load_plugin(filename)
        cls = mod.Channel
        fields = getattr(cls, "CONFIG_FIELDS", [])
        return jsonify({"fields": fields,
                        "channel_name": getattr(cls, "CHANNEL_NAME", ""),
                        "channel_type": getattr(cls, "CHANNEL_TYPE", "")})
    except Exception as e:
        return jsonify({"error": str(e)}), 404

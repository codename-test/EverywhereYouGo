#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
Channel plugin loader.
动态加载通道插件（内置目录 + 用户目录，用户优先）。
目录布局见 plugin_paths.py；BaseChannel 已从 channels/ 独立到 channel_base.py，
因为 channels/ 现在是**用户卷**，基础设施不该放在会被卷遮蔽的位置。
"""

import importlib.util
import os
import sys
import traceback
import threading
import log
import plugin_paths
from channel_base import BaseChannel

CHANNELS_DIR = plugin_paths.user_dir("channel")   # 兼容旧引用：指向用户目录


def _t(key, fallback):
    try:
        import i18n
        return i18n._(key)
    except Exception:
        return fallback


def _module_name(filename: str) -> str:
    name = filename.replace(".py", "")
    return f"channel_{name.replace('-', '_').replace('.', '_')}"


_channel_cache: dict = {}
_channel_cache_lock = threading.Lock()


def list_plugins() -> list:
    """列出内置与用户通道插件；同名时用户版本优先，并带 source 标注。"""
    return plugin_paths.list_plugins("channel")


def load_plugin(filename: str):
    with _channel_cache_lock:
        if filename in _channel_cache:
            return _channel_cache[filename]

    filepath = plugin_paths.resolve("channel", filename)
    if not filepath:
        raise FileNotFoundError(
            f"Channel plugin not found: {filename} "
            f"(the plugin file may have been deleted; re-upload it or pick another type)")

    mod_name = _module_name(filename)
    spec = importlib.util.spec_from_file_location(mod_name, filepath)
    mod = importlib.util.module_from_spec(spec)
    # Inject BaseChannel so plugins can use it without relative imports
    mod.BaseChannel = BaseChannel
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)

    if not hasattr(mod, "Channel"):
        raise AttributeError(f"Channel plugin {filename} must define a Channel class")

    with _channel_cache_lock:
        _channel_cache[filename] = mod

    log.logger.info(f"Channel plugin loaded: {filename}")
    return mod


def reload_plugin(filename: str):
    with _channel_cache_lock:
        if filename in _channel_cache:
            del _channel_cache[filename]
        mod_name = _module_name(filename)
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    return load_plugin(filename)


def create_channel(channel_type: str, config: dict):
    filename = plugin_paths.channel_filename(channel_type)
    mod = load_plugin(filename)
    ChannelClass = mod.Channel
    return ChannelClass(config)


def test_channel(channel_type: str, config: dict) -> dict:
    """测试通道。插件可返回 bool，也可返回 (ok, error) 以便把真实原因带给用户。"""
    try:
        channel = create_channel(channel_type, config)
        if not hasattr(channel, "test"):
            return {"ok": False, "error": "Plugin has no test() method"}
        result = channel.test()
        ok, err = _unpack_test_result(result)
        return {"ok": ok, "error": "" if ok else (err or "Test failed")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _unpack_test_result(result):
    """test() 允许两种返回：bool，或 (ok, error)。"""
    if isinstance(result, tuple):
        ok = bool(result[0]) if result else False
        err = result[1] if len(result) > 1 else ""
        return ok, (err or "")
    return bool(result), ""


def dry_run_channel(filename: str, config: dict) -> dict:
    try:
        mod = load_plugin(filename)
        ChannelClass = mod.Channel
        channel_type = getattr(ChannelClass, "CHANNEL_TYPE", filename.replace(".py", ""))
        channel_name = getattr(ChannelClass, "CHANNEL_NAME", filename.replace(".py", ""))
        channel = ChannelClass(config)
        return {
            "ok": True,
            "error": None,
            "channel_name": channel_name,
            "channel_type": channel_type,
        }
    except Exception as e:
        return {
            "ok": False,
            "error": traceback.format_exc(),
            "channel_name": None,
            "channel_type": None,
        }

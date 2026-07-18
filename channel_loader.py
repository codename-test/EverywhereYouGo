#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
Channel plugin loader.
Dynamically loads .py files from channels/ directory.
"""

import importlib.util
import os
import sys
import traceback
import threading
import log
from channels import BaseChannel

CHANNELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "channels")


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
    plugins = []
    if not os.path.isdir(CHANNELS_DIR):
        return plugins
    for f in sorted(os.listdir(CHANNELS_DIR)):
        if f.endswith(".py") and f != "__init__.py" and not f.startswith("_"):
            filepath = os.path.join(CHANNELS_DIR, f)
            plugins.append({
                "filename": f,
                "name": f.replace(".py", ""),
                "exists": os.path.isfile(filepath),
            })
    return plugins


def load_plugin(filename: str):
    with _channel_cache_lock:
        if filename in _channel_cache:
            return _channel_cache[filename]

    filepath = os.path.join(CHANNELS_DIR, filename)
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"Channel plugin not found: {filepath}")

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
    filename = channel_type if channel_type.endswith(".py") else f"{channel_type}.py"
    mod = load_plugin(filename)
    ChannelClass = mod.Channel
    return ChannelClass(config)


def test_channel(channel_type: str, config: dict) -> dict:
    try:
        channel = create_channel(channel_type, config)
        if hasattr(channel, "test"):
            ok = channel.test()
            return {"ok": ok, "error": "" if ok else "Test failed"}
        else:
            return {"ok": False, "error": "Plugin has no test() method"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


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

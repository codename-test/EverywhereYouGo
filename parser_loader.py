#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
解析器插件加载器。
动态加载 parsers/ 目录下的 .py 文件，调用 parse() 函数。
"""

import importlib.util
import os
import sys
import traceback
import log

PARSERS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "parsers")


def _module_name(filename: str) -> str:
    """parser_emby / parser_xxx"""
    name = filename.replace(".py", "")
    return f"parser_{name.replace('-', '_').replace('.', '_')}"


def load_parser(filename: str):
    """
    加载 parsers/{filename}，返回 module 对象。
    缓存：同名文件不重复 import。
    """
    filepath = os.path.join(PARSERS_DIR, filename)
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"Parser not found: {filepath}")

    mod_name = _module_name(filename)

    # 如果已加载，先卸载再重载（支持覆盖上传）
    if mod_name in sys.modules:
        del sys.modules[mod_name]

    spec = importlib.util.spec_from_file_location(mod_name, filepath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)

    if not hasattr(mod, "parse"):
        raise AttributeError(f"Parser {filename} must define a parse() function")

    log.logger.info(f"Parser loaded: {filename}")
    return mod


def run_parser(filename: str, raw_body: bytes, headers: dict, query_params: dict) -> dict:
    """
    执行解析器，返回标准消息体 dict。
    {
        "title": str,
        "content": str,
        "summary": str,
        "url": str,
        "image_url": str,
        "route_tags": {...},
        "tags": {...}
    }
    异常时 raise，调用方负责捕获。
    """
    mod = load_parser(filename)
    result = mod.parse(raw_body, headers, query_params)

    if not isinstance(result, dict):
        raise TypeError(f"parse() must return dict, got {type(result).__name__}")

    # 保底字段
    result.setdefault("title", "无标题")
    result.setdefault("content", "")
    result.setdefault("summary", "")
    result.setdefault("url", "")
    result.setdefault("image_url", "")
    result.setdefault("route_tags", {})
    result.setdefault("tags", {})
    return result


def dry_run_parser(filename: str, raw_body: bytes, headers: dict, query_params: dict) -> dict:
    """
    试运行解析器（用于 WebUI 测试）。
    返回 {"ok": bool, "result": dict|None, "error": str|None, "variables": [...]}
    """
    try:
        result = run_parser(filename, raw_body, headers, query_params)
        variables = _extract_variable_paths(result, prefix="msg")
        return {"ok": True, "result": result, "error": None, "variables": variables}
    except Exception as e:
        return {"ok": False, "result": None, "error": traceback.format_exc(), "variables": []}


def _extract_variable_paths(obj, prefix="", depth=0) -> list:
    """递归提取对象的所有变量路径，用于模板编辑器展示可用变量。"""
    if depth > 4:
        return []
    paths = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            key_str = str(k)
            p = f"{prefix}.{key_str}" if prefix else key_str
            paths.append({"path": p, "type": type(v).__name__, "sample": _sample(v)})
            if isinstance(v, dict):
                paths.extend(_extract_variable_paths(v, p, depth + 1))
    return paths


def _sample(v, max_len=30):
    s = str(v)
    return s[:max_len] + "..." if len(s) > max_len else s


def reload_parser(filename: str):
    """重新加载解析器（在线编辑后调用），等同于 load_parser。"""
    return load_parser(filename)

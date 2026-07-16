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
import threading
import log

PARSERS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "parsers")


def _t(key, fallback):
    """翻译辅助：有 Flask 上下文时用 i18n，否则返回 fallback。"""
    try:
        import i18n
        return i18n._(key)
    except Exception:
        return fallback


def _module_name(filename: str) -> str:
    """parser_emby / parser_xxx"""
    name = filename.replace(".py", "")
    return f"parser_{name.replace('-', '_').replace('.', '_')}"


# 解析器缓存 {filename: module}
_parser_cache: dict = {}
_parser_cache_lock = threading.Lock()


def load_parser(filename: str):
    """
    加载 parsers/{filename}，返回 module 对象。
    缓存：同名文件只加载一次，调用 reload_parser 显式重载。
    """
    with _parser_cache_lock:
        if filename in _parser_cache:
            return _parser_cache[filename]

    filepath = os.path.join(PARSERS_DIR, filename)
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"Parser not found: {filepath}")

    mod_name = _module_name(filename)
    spec = importlib.util.spec_from_file_location(mod_name, filepath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)

    if not hasattr(mod, "parse"):
        raise AttributeError(f"Parser {filename} must define a parse() function")

    with _parser_cache_lock:
        _parser_cache[filename] = mod

    log.logger.info(f"Parser loaded: {filename}")
    return mod


def reload_parser(filename: str):
    """重新加载解析器（在线编辑后调用），清除缓存后重新加载。"""
    with _parser_cache_lock:
        if filename in _parser_cache:
            del _parser_cache[filename]
        mod_name = _module_name(filename)
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    return load_parser(filename)


def run_parser(filename: str, raw_body: bytes, headers: dict, query_params: dict) -> dict:
    """
    执行解析器，返回标准消息体 dict。
    解析器返回 title + 展平的顶层字段。
    异常时 raise，调用方负责捕获。
    """
    mod = load_parser(filename)
    result = mod.parse(raw_body, headers, query_params)

    if not isinstance(result, dict):
        raise TypeError(f"parse() must return dict, got {type(result).__name__}")

    # 保底：至少有一对 KV
    if not result:
        result = {"data": _t("parser.empty_msg", "空消息")}
    
    # 自动生成 title：找 Name/title/Subject/Event 等常见字段，否则用第一个值
    if "title" not in result:
        title_key = None
        for key in ["Name", "title", "Subject", "Event", "name", "subject", "event"]:
            for k in result:
                if k == key or k.lower().endswith(f".{key}".lower()):
                    title_key = k
                    break
            if title_key:
                break
        
        # 优先用找到的字段，否则用第一个非空的值，最后兜底"未命名"
        if title_key:
            result["title"] = result[title_key]
        else:
            first_value = next((v for v in result.values() if v), _t("parser.untitled", "未命名"))
            result["title"] = first_value
    
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

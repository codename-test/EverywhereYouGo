#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""通用 JSON 解析器

把任意 JSON 请求体展平成模板可用的扁平变量：

    {"Event": "library.new",
     "Item": {"Name": "星际穿越", "Type": "Movie"},
     "Tags": ["科幻", "太空"]}

→  event=library.new
    item.name=星际穿越
    item.type=Movie
    tags=科幻, 太空
    title=星际穿越        （按通用规则推断）

嵌套对象用**点号路径**（`item.name`），既能做模板变量，也能直接写进路由条件
（路由只认标量字段，所以数组会被合成字符串，而不是留成列表 —— 否则条件里取不到）。

上限：深度 6 层、最多 300 个字段、单值截断 2000 字符，防止畸形/超大 payload 拖垮渲染。
"""

import json

PARSER_NAME = "通用 JSON"
PARSER_DESC = "解析任意 JSON 请求体，嵌套字段展平为点号路径变量"
PARSER_VERSION = "1.0"

MAX_DEPTH = 6
MAX_KEYS = 300
MAX_VALUE_LEN = 2000


def _shorten(v):
    s = v if isinstance(v, str) else str(v)
    return s if len(s) <= MAX_VALUE_LEN else s[:MAX_VALUE_LEN] + "…"


def _flatten(node, prefix, out, depth=0):
    if len(out) >= MAX_KEYS or depth > MAX_DEPTH:
        return
    if isinstance(node, dict):
        for k, v in node.items():
            # 键统一小写：与项目既有解析器（emby.py 产出 event/name/media_type）一致，
            # 条件表达式里也不必纠结大小写
            k = str(k).lower()
            key = f"{prefix}.{k}" if prefix else k
            _flatten(v, key, out, depth + 1)
    elif isinstance(node, (list, tuple)):
        scalars = [x for x in node if isinstance(x, (str, int, float, bool))]
        if len(scalars) == len(node):
            # 全是标量 → 合成一个字符串，路由条件与模板都能直接用
            out[prefix] = _shorten(", ".join(str(x) for x in scalars))
        else:
            for i, v in enumerate(node):
                _flatten(v, f"{prefix}.{i}" if prefix else str(i), out, depth + 1)
    elif node is None:
        return
    elif isinstance(node, (str, int, float, bool)):
        # 数值/布尔保留原类型，便于路由做数值比较（如 size > 100）
        out[prefix] = node if isinstance(node, (int, float, bool)) else _shorten(node)


def _guess_title(flat, data):
    """按常见字段名推断标题，找不到就用第一个非空标量。"""
    for cand in ("name", "title", "subject", "event", "item.name", "item.title"):
        for k, v in flat.items():
            if k.lower() == cand or k.lower().endswith("." + cand):
                if v:
                    return _shorten(v)
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, (str, int, float)) and str(v).strip():
                return _shorten(v)
    for v in flat.values():
        if v:
            return v
    return ""


def _as_kv_markdown(flat):
    lines = [f"- **{k}**: {v}" for k, v in flat.items()]
    return "\n".join(lines)


def parse(raw_body: bytes, headers: dict, query_params: dict) -> dict:
    text = (raw_body or b"").decode("utf-8", "replace").strip()
    if not text:
        return {"title": "", "content": "", "data": ""}

    try:
        data = json.loads(text)
    except Exception:
        # 不是合法 JSON：退化成原文，至少不丢消息
        return {"title": text.splitlines()[0][:120], "content": text, "data": text}

    flat = {}
    _flatten(data, "", flat)

    # query 参数补进来（不覆盖 body 里的同名字段）
    for k, v in (query_params or {}).items():
        flat.setdefault(k, _shorten(v))

    title = _guess_title(flat, data)
    result = dict(flat)
    result["title"] = title
    result["content"] = _as_kv_markdown(flat)
    return result

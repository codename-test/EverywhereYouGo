#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""通用表单解析器

支持两种表单编码：

  - `application/x-www-form-urlencoded`
  - `multipart/form-data`（含文件上传）

字段展平成扁平变量；**同名字段合成逗号分隔的字符串**（而不是列表），
因为路由条件只认标量字段，列表会让条件取不到值。

文件字段不读取内容，只给 `上传字段名.filename` / `.size`，避免把二进制塞进模板。

Content-Type 缺失或不认识时，退化为按 urlencoded 解析整个 body。
"""

import re
from email.parser import BytesParser
from email.policy import default as _policy

PARSER_NAME = "通用表单"
PARSER_DESC = "解析 form-urlencoded / multipart 表单，字段展平为变量"
PARSER_VERSION = "1.0"

MAX_KEYS = 300
MAX_VALUE_LEN = 2000


def _shorten(v):
    s = v if isinstance(v, str) else str(v)
    return s if len(s) <= MAX_VALUE_LEN else s[:MAX_VALUE_LEN] + "…"


def _put(out, key, value):
    """同名字段合成逗号分隔（保证是标量）；数值保留原类型。

    合成时必然变成字符串 —— 这是刻意取舍：路由条件只认标量，
    留成列表会取不到值。
    """
    key = str(key).lower()
    if key in out and out[key] != "":
        out[key] = f"{out[key]}, {_shorten(value)}"
    elif isinstance(value, (int, float, bool)):
        out[key] = value
    else:
        out[key] = _shorten(value)


def _parse_urlencoded(raw: bytes, out):
    text = raw.decode("utf-8", "replace")
    for pair in re.split(r"[&\n]", text):
        if not pair.strip():
            continue
        if "=" in pair:
            k, v = pair.split("=", 1)
        else:
            k, v = pair, ""
        from urllib.parse import unquote_plus
        _put(out, unquote_plus(k), unquote_plus(v))


def _parse_multipart(raw: bytes, content_type: str, out):
    """用标准库 email 解析 multipart —— 不依赖已废弃的 cgi 模块。"""
    envelope = (b"Content-Type: " + content_type.encode("utf-8")
                + b"\r\nMIME-Version: 1.0\r\n\r\n" + (raw or b""))
    msg = BytesParser(policy=_policy).parsebytes(envelope)
    if not msg.is_multipart():
        return False
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename:
            _put(out, f"{name}.filename", filename)
            _put(out, f"{name}.size", len(payload))
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            _put(out, name, payload.decode(charset, "replace"))
        except Exception:
            _put(out, name, payload.decode("utf-8", "replace"))
    return True


def parse(raw_body: bytes, headers: dict, query_params: dict) -> dict:
    hdrs = {str(k).lower(): v for k, v in (headers or {}).items()}
    ctype = str(hdrs.get("content-type", "") or "")

    out = {}
    if ctype.lower().startswith("multipart/form-data"):
        try:
            if not _parse_multipart(raw_body, ctype, out):
                _parse_urlencoded(raw_body, out)
        except Exception:
            _parse_urlencoded(raw_body, out)
    else:
        # urlencoded，或其他编码（不认识的按 urlencoded 试一把，尽量不丢消息）
        _parse_urlencoded(raw_body, out)

    for k, v in (query_params or {}).items():
        out.setdefault(k, _shorten(v))

    if len(out) > MAX_KEYS:
        out = dict(list(out.items())[:MAX_KEYS])

    title = ""
    for cand in ("name", "title", "subject", "event"):
        for k, v in out.items():
            if k.lower() == cand and v:
                title = v
                break
        if title:
            break
    if not title:
        title = next((v for v in out.values() if v), "")

    result = dict(out)
    result["title"] = title
    result["content"] = "\n".join(f"- **{k}**: {v}" for k, v in out.items())
    return result

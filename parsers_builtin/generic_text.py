#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""通用文本解析器

适合纯文本来源（告警、脚本输出、日志片段、配置变更通知等）：

  - 全文放进 `content` 与 `text`
  - **首个非空行**作为 `title`
  - 形如 `KEY=VALUE`（或 `KEY: VALUE`）的行额外提取成变量，
    这样路由条件可以直接写 `level == 'ERROR'`

示例输入：

    [告警] 磁盘使用率过高
    level=ERROR
    host=nas-01
    usage=91%

→  title=[告警] 磁盘使用率过高
    level=ERROR, host=nas-01, usage=91%
    content=<全文>
"""

import re

PARSER_NAME = "通用文本"
PARSER_DESC = "纯文本来源：首行作标题，KEY=VALUE 行提取为变量"
PARSER_VERSION = "1.0"

MAX_TEXT_LEN = 20000
MAX_KEYS = 200
MAX_VALUE_LEN = 2000

# KEY=VALUE 或 KEY: VALUE（键只允许字母数字下划线连字符点）
_KV_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_.\-]{0,63})\s*[:=]\s*(.+?)\s*$")


def _shorten(v, limit=MAX_VALUE_LEN):
    s = v if isinstance(v, str) else str(v)
    return s if len(s) <= limit else s[:limit] + "…"


def parse(raw_body: bytes, headers: dict, query_params: dict) -> dict:
    text = (raw_body or b"").decode("utf-8", "replace")
    if len(text) > MAX_TEXT_LEN:
        text = text[:MAX_TEXT_LEN] + "\n…（已截断）"

    lines = text.splitlines()
    title = ""
    for line in lines:
        if line.strip():
            title = _shorten(line.strip(), 200)
            break

    out = {}
    for line in lines:
        if len(out) >= MAX_KEYS:
            break
        m = _KV_RE.match(line)
        if not m:
            continue
        key, value = m.group(1).lower(), m.group(2)
        value = _shorten(value)
        if key in out and out[key]:
            out[key] = f"{out[key]}, {value}"
        else:
            out[key] = value

    # query 参数补进来（不覆盖正文提取出的值）
    for k, v in (query_params or {}).items():
        out.setdefault(str(k), _shorten(v))

    result = dict(out)
    result["title"] = title
    result["text"] = text
    result["content"] = text
    result["line_count"] = len(lines)
    return result

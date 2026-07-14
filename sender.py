#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
模板渲染器 — 支持 simple（{var} 替换）和 Jinja2 两种引擎。
"""

import log

try:
    from jinja2 import Template, Environment, BaseLoader
    _HAS_JINJA2 = True
except ImportError:
    _HAS_JINJA2 = False


def render_template(engine, title_tpl, content_tpl, msg):
    """
    渲染模板。

    Args:
        engine:      "jinja2" 或 "simple"
        title_tpl:   标题模板字符串
        content_tpl: 正文模板字符串
        msg:         解析器返回的 dict（title/content + 展平的顶层字段）

    Returns:
        (title, content) 两个字符串
    """
    if engine == "jinja2":
        return _render_jinja2(title_tpl, content_tpl, msg)
    else:
        return _render_simple(title_tpl, content_tpl, msg)


def _render_simple(title_tpl, content_tpl, msg):
    """Simple 模式：{key} 替换，msg 所有顶层标量字段直接可用。"""
    ns = {k: v for k, v in msg.items() if isinstance(v, (str, int, float, bool))}

    try:
        title = title_tpl.format(**ns) if title_tpl else msg.get("title", "")
    except (KeyError, ValueError):
        title = title_tpl
    try:
        content = content_tpl.format(**ns) if content_tpl else "\n".join(f"- **{k}**: {v}" for k, v in ns.items() if k != "title")
    except (KeyError, ValueError):
        content = content_tpl
    return title, content


def _render_jinja2(title_tpl, content_tpl, msg):
    """Jinja2 模式：{{ msg.title }} 或 {{ title }}。"""
    env = Environment(loader=BaseLoader())
    ns = {"msg": msg}
    # 顶层标量字段也直接暴露
    for k, v in msg.items():
        if isinstance(v, (str, int, float, bool)):
            ns[k] = v

    try:
        title = env.from_string(title_tpl).render(**ns) if title_tpl else msg.get("title", "")
    except Exception as e:
        log.logger.error(f"Jinja2 title render error: {e}")
        title = title_tpl

    try:
        content = env.from_string(content_tpl).render(**ns) if content_tpl else "\n".join(f"- **{k}**: {v}" for k, v in ns.items() if k not in ("msg", "title"))
    except Exception as e:
        log.logger.error(f"Jinja2 content render error: {e}")
        content = "\n".join(f"- **{k}**: {v}" for k, v in ns.items() if k not in ("msg", "title"))

    return title, content

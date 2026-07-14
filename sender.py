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
        msg:         标准消息体 dict

    Returns:
        (title, content) 两个字符串
    """
    if engine == "jinja2":
        return _render_jinja2(title_tpl, content_tpl, msg)
    else:
        return _render_simple(title_tpl, content_tpl, msg)


def _render_simple(title_tpl, content_tpl, msg):
    """Simple 模式：{key} 替换，支持 {title} {content} {summary} 等。"""
    # 构建一个扁平化的命名空间
    ns = {
        "title":   msg.get("title", ""),
        "content": msg.get("content", ""),
        "summary": msg.get("summary", ""),
        "url":     msg.get("url", ""),
        "image_url": msg.get("image_url", ""),
    }
    # 展开 tags
    tags = msg.get("tags", {})
    if isinstance(tags, dict):
        for k, v in tags.items():
            if k not in ns:
                ns[k] = v

    try:
        title = title_tpl.format(**ns) if title_tpl else msg.get("title", "")
    except (KeyError, ValueError):
        title = title_tpl  # 原样返回
    try:
        content = content_tpl.format(**ns) if content_tpl else msg.get("content", "")
    except (KeyError, ValueError):
        content = content_tpl
    return title, content


def _render_jinja2(title_tpl, content_tpl, msg):
    """Jinja2 模式：{{ msg.title }}"""
    env = Environment(loader=BaseLoader())

    # 构建安全的命名空间
    ns = {"msg": msg}

    # 也展开 tags 作为顶级变量方便使用
    tags = msg.get("tags", {})
    if isinstance(tags, dict):
        for k, v in tags.items():
            if k not in ns:
                ns[k] = v

    try:
        title = env.from_string(title_tpl).render(**ns) if title_tpl else msg.get("title", "")
    except Exception as e:
        log.logger.error(f"Jinja2 title render error: {e}")
        title = title_tpl

    try:
        content = env.from_string(content_tpl).render(**ns) if content_tpl else msg.get("content", "")
    except Exception as e:
        log.logger.error(f"Jinja2 content render error: {e}")
        content = content_tpl

    return title, content

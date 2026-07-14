#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
模板渲染引擎。同时支持 simple（{var} 替换）和 Jinja2。
"""

import log
from jinja2 import Template as Jinja2Template, UndefinedError

# 尝试 import jinja2
try:
    import jinja2
    HAS_JINJA2 = True
except ImportError:
    HAS_JINJA2 = False
    log.logger.warning("Jinja2 not installed, falling back to simple engine only")


def render_template(engine: str, title_tpl: str, content_tpl: str, msg: dict) -> dict:
    """
    根据 engine 类型渲染模板。

    Args:
        engine:      "simple" | "jinja2"
        title_tpl:   标题模板字符串
        content_tpl: 内容模板字符串
        msg:         标准消息体 dict（title/content/summary/url/image_url/route_tags/tags）

    Returns:
        {"title": str, "content": str}
    """
    if engine == "jinja2":
        return _render_jinja2(title_tpl, content_tpl, msg)
    else:
        return _render_simple(title_tpl, content_tpl, msg)


def _render_simple(title_tpl: str, content_tpl: str, msg: dict) -> dict:
    """简单 {var} 替换。变量名 = msg dict 的顶层 key + tags 平铺。"""
    # 准备替换字典：msg 顶层 + tags 平铺 + route_tags 平铺
    vars_dict = {}
    for k, v in msg.items():
        if k in ("route_tags", "tags"):
            if isinstance(v, dict):
                vars_dict.update(v)
        else:
            vars_dict[k] = v

    try:
        title = title_tpl.format(**vars_dict) if title_tpl else str(msg.get("title", ""))
        content = content_tpl.format(**vars_dict) if content_tpl else str(msg.get("content", ""))
    except KeyError as e:
        log.logger.warning(f"Simple template missing variable: {e}")
        title = title_tpl
        content = content_tpl
    except Exception as e:
        log.logger.error(f"Simple template render error: {e}")
        title = str(msg.get("title", ""))
        content = str(msg.get("content", ""))

    return {"title": title, "content": content}


def _render_jinja2(title_tpl: str, content_tpl: str, msg: dict) -> dict:
    """Jinja2 模板渲染。msg 作为 msg 变量注入。"""
    if not HAS_JINJA2:
        log.logger.warning("Jinja2 not available, falling back to simple")
        return _render_simple(title_tpl, content_tpl, msg)

    try:
        title = Jinja2Template(title_tpl).render(msg=msg) if title_tpl else str(msg.get("title", ""))
        content = Jinja2Template(content_tpl).render(msg=msg) if content_tpl else str(msg.get("content", ""))
        return {"title": title, "content": content}
    except UndefinedError as e:
        log.logger.warning(f"Jinja2 undefined variable: {e}")
        return {"title": title_tpl, "content": content_tpl}
    except Exception as e:
        log.logger.error(f"Jinja2 render error: {e}")
        return {"title": str(msg.get("title", "")), "content": str(msg.get("content", ""))}

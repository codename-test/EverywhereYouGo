#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""api/pages.py — HTML 页面渲染"""

import os
import db
import i18n
from flask import Blueprint, render_template

pages_bp = Blueprint("pages", __name__)

PARSERS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "parsers")
VERSION = "1.3.0"


def _render(page, title, active_page="", **kwargs):
    lang = i18n.get_lang()
    js_i18n = i18n.TRANSLATIONS.get(lang, {})
    from flask import current_app
    return render_template(page, title=title, active_page=active_page,
                           auth_enabled=bool(current_app.auth_token),
                           lang=lang, js_i18n=js_i18n, version=VERSION,
                           **kwargs)


@pages_bp.route("/")
def index():
    stats = db.get_stats()
    sources = db.get_sources()
    for s in sources:
        p = db.get_parser(s.get("parser_id"))
        s["parser_name"] = p["name"] if p else "-"
    return _render("dashboard.html", i18n._("dash.title"), "dashboard", stats=stats, sources=sources)


@pages_bp.route("/sources")
def sources_page():
    sources = db.get_sources()
    parsers = db.get_parsers()
    for s in sources:
        p = db.get_parser(s.get("parser_id"))
        s["parser_name"] = p["name"] if p else "-"
    channels = db.get_channels()
    templates = db.get_templates()
    sc = db.get_all_source_channels()
    path_prefix = db.get_config("path_prefix", "in")
    return _render("sources_page.html", i18n._("src.title"), "sources",
                   sources=sources, parsers=parsers,
                   channels=channels, templates=templates, sc=sc,
                   path_prefix=path_prefix)


@pages_bp.route("/parsers")
def parsers_page():
    parsers = db.get_parsers()
    for p in parsers:
        p["exists"] = os.path.isfile(os.path.join(PARSERS_DIR, p["filename"]))
    return _render("parsers_page.html", i18n._("parser.title"), "parsers", parsers=parsers)


@pages_bp.route("/channels")
def channels_page():
    channels = db.get_channels()
    return _render("channels_page.html", i18n._("ch.title"), "channels", channels=channels)


@pages_bp.route("/channel_sdk")
def channel_sdk_page():
    from flask import redirect
    return redirect("/docs/channel")


@pages_bp.route("/docs")
def docs_page():
    return _render("docs_page.html", i18n._("docs.title"), "docs")


SDK_DOC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "doc", "sdk")


def _load_sdk_doc(doc_type):
    """Load the bilingual guide (rendered to HTML) and the AI prompt (raw text)."""
    import markdown
    lang = i18n.get_lang()
    guide_html = ""
    for candidate in (f"{doc_type}.{lang}.md", f"{doc_type}.en.md"):
        md_path = os.path.join(SDK_DOC_DIR, candidate)
        if os.path.isfile(md_path):
            md_text = open(md_path, encoding="utf-8").read()
            guide_html = markdown.markdown(
                md_text, extensions=["tables", "fenced_code", "toc"])
            break
    prompt_path = os.path.join(SDK_DOC_DIR, "prompts", f"{doc_type}.md")
    prompt_text = ""
    if os.path.isfile(prompt_path):
        prompt_text = open(prompt_path, encoding="utf-8").read()
    return guide_html, prompt_text


@pages_bp.route("/docs/<doc_type>")
def docs_detail_page(doc_type):
    from flask import redirect
    valid = {"parser": "docs.parser_title", "channel": "docs.channel_title", "template": "docs.template_title"}
    if doc_type not in valid:
        return redirect("/docs")
    guide_html, prompt_text = _load_sdk_doc(doc_type)
    return _render("docs_detail.html", i18n._(valid[doc_type]), "docs",
                   doc_type=doc_type, guide_html=guide_html, prompt_text=prompt_text)


@pages_bp.route("/templates")
def templates_page():
    templates = db.get_templates()
    parsers = db.get_parsers()
    return _render("templates_page.html", i18n._("tpl.title"), "templates",
                   templates=templates, parsers=parsers)


@pages_bp.route("/logs")
def logs_page():
    return _render("logs_page.html", i18n._("log.title"), "logs")


@pages_bp.route("/messages")
def messages_page():
    sources = db.get_sources()
    return _render("messages.html", i18n._("msg.title"), "messages", sources=sources)


@pages_bp.route("/settings")
def settings_page():
    import circuit_breaker
    config = {
        "log_level": db.get_log_level(),
        "dnd_enabled": db.get_config("dnd_enabled", "0"),
        "dnd_start": db.get_config("dnd_start", "23:00"),
        "dnd_end": db.get_config("dnd_end", "07:00"),
        "cleanup": db.get_cleanup_config(),
        "path_prefix": db.get_config("path_prefix", "in"),
    }
    # 熔断参数：库里没存则留空，页面上以"默认值"占位（留空即用默认）
    breaker = {}
    for short, key in circuit_breaker.CONFIG_KEYS.items():
        breaker[short] = db.get_config(key, "")

    def _fmt(v):
        """整数就按整数显示，避免占位符出现 60.0 / 30.0 这种别扭写法。"""
        try:
            return int(v) if float(v) == int(v) else v
        except (TypeError, ValueError):
            return v

    translated_statuses = {s: i18n._(f"status.{s}") for s in db.MESSAGE_STATUSES}
    return _render("settings.html", i18n._("set.title"), "settings",
                   config=config,
                   breaker=breaker,
                   breaker_defaults={
                       k: _fmt(v) for k, v in {
                           "window": circuit_breaker.WINDOW_SECONDS,
                           "min_samples": circuit_breaker.MIN_SAMPLES,
                           "failure_ratio": circuit_breaker.FAILURE_RATIO,
                           "consecutive": circuit_breaker.CONSECUTIVE_THRESHOLD,
                           "open_base": circuit_breaker.OPEN_BASE_SECONDS,
                           "open_max": circuit_breaker.OPEN_MAX_SECONDS,
                           "half_open_ok": circuit_breaker.HALF_OPEN_NEEDED,
                       }.items()
                   },
                   message_statuses=translated_statuses)

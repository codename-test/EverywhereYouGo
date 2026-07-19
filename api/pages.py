#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""api/pages.py — HTML 页面渲染"""

import os
import db
import i18n
from flask import Blueprint, render_template

pages_bp = Blueprint("pages", __name__)

PARSERS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "parsers")
VERSION = "1.0.1"


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
    return _render("sources_page.html", i18n._("src.title"), "sources",
                   sources=sources, parsers=parsers,
                   channels=channels, templates=templates, sc=sc)


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


@pages_bp.route("/docs/<doc_type>")
def docs_detail_page(doc_type):
    from flask import redirect
    valid = {"parser": "docs.parser_title", "channel": "docs.channel_title", "template": "docs.template_title"}
    if doc_type not in valid:
        return redirect("/docs")
    return _render("docs_detail.html", i18n._(valid[doc_type]), "docs", doc_type=doc_type)


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
    config = {
        "log_level": db.get_log_level(),
        "dnd_enabled": db.get_config("dnd_enabled", "0"),
        "dnd_start": db.get_config("dnd_start", "23:00"),
        "dnd_end": db.get_config("dnd_end", "07:00"),
        "cleanup": db.get_cleanup_config(),
    }
    translated_statuses = {s: i18n._(f"status.{s}") for s in db.MESSAGE_STATUSES}
    return _render("settings.html", i18n._("set.title"), "settings",
                   config=config,
                   message_statuses=translated_statuses)

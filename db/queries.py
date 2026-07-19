#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
数据库 CRUD 查询函数。
按实体组织：Parsers / Sources / Channels / Templates / Bindings / Messages / Config / Logs / Stats。
"""

import json
import sqlite3
from .connection import _conn


# ═══════════════════════════════════════════════
#  Parsers
# ═══════════════════════════════════════════════

def get_parsers():
    return [dict(r) for r in _conn().execute("SELECT * FROM parsers ORDER BY id").fetchall()]


def get_parser(parser_id):
    r = _conn().execute("SELECT * FROM parsers WHERE id=?", (parser_id,)).fetchone()
    return dict(r) if r else None


def create_parser(name, filename, description=""):
    try:
        c = _conn().execute(
            "INSERT INTO parsers (name, filename, description) VALUES (?,?,?)",
            (name, filename, description)
        )
        _conn().commit()
        return c.lastrowid
    except sqlite3.IntegrityError:
        return None


def update_parser(parser_id, **kwargs):
    if not kwargs:
        return
    allowed = {"name", "filename", "description"}
    sets = [f"{k}=?" for k in kwargs if k in allowed]
    vals = [kwargs[k] for k in kwargs if k in allowed]
    if not sets:
        return
    vals.append(parser_id)
    _conn().execute(f"UPDATE parsers SET {', '.join(sets)} WHERE id=?", vals)
    _conn().commit()


def delete_parser(parser_id):
    _conn().execute("DELETE FROM parsers WHERE id=?", (parser_id,))
    _conn().commit()


def upsert_parser(pid, name, filename, description=""):
    _conn().execute(
        "INSERT OR REPLACE INTO parsers (id, name, filename, description) VALUES (?,?,?,?)",
        (pid, name, filename, description)
    )
    _conn().commit()


# ═══════════════════════════════════════════════
#  Sources
# ═══════════════════════════════════════════════

def get_sources():
    return [dict(r) for r in _conn().execute("SELECT * FROM sources ORDER BY id").fetchall()]


def get_source(source_id):
    r = _conn().execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
    return dict(r) if r else None


def get_source_by_port(port):
    r = _conn().execute("SELECT * FROM sources WHERE port=?", (port,)).fetchone()
    return dict(r) if r else None


def create_source(name, port, parser_id=None, enabled=1):
    try:
        c = _conn().execute(
            "INSERT INTO sources (name, port, parser_id, enabled) VALUES (?,?,?,?)",
            (name, port, parser_id, enabled)
        )
        _conn().commit()
        return c.lastrowid
    except sqlite3.IntegrityError:
        return None


def update_source(source_id, **kwargs):
    if not kwargs:
        return
    allowed = {"name", "port", "parser_id", "enabled"}
    sets = [f"{k}=?" for k in kwargs if k in allowed]
    vals = [kwargs[k] for k in kwargs if k in allowed]
    if not sets:
        return
    vals.append(source_id)
    _conn().execute(f"UPDATE sources SET {', '.join(sets)} WHERE id=?", vals)
    _conn().commit()


def delete_source(source_id):
    _conn().execute("DELETE FROM sources WHERE id=?", (source_id,))
    _conn().commit()


def upsert_source(sid, name, port, parser_id=None, enabled=1):
    _conn().execute(
        "INSERT OR REPLACE INTO sources (id, name, port, parser_id, enabled) VALUES (?,?,?,?,?)",
        (sid, name, port, parser_id, enabled)
    )
    _conn().commit()


# ═══════════════════════════════════════════════
#  Channels
# ═══════════════════════════════════════════════

def get_channels():
    return [dict(r) for r in _conn().execute("SELECT * FROM channels ORDER BY id").fetchall()]


def get_channel(channel_id):
    r = _conn().execute("SELECT * FROM channels WHERE id=?", (channel_id,)).fetchone()
    return dict(r) if r else None


def create_channel(name, channel_type, config="{}", enabled=1):
    if isinstance(config, dict):
        config = json.dumps(config)
    c = _conn().execute(
        "INSERT INTO channels (name, type, config, enabled) VALUES (?,?,?,?)",
        (name, channel_type, config, enabled)
    )
    _conn().commit()
    return c.lastrowid


def update_channel(channel_id, **kwargs):
    if not kwargs:
        return
    allowed = {"name", "type", "config", "enabled"}
    sets = []
    vals = []
    for k in allowed:
        if k in kwargs:
            sets.append(f"{k}=?")
            v = kwargs[k]
            vals.append(json.dumps(v) if k == "config" and isinstance(v, dict) else v)
    if not sets:
        return
    vals.append(channel_id)
    _conn().execute(f"UPDATE channels SET {', '.join(sets)} WHERE id=?", vals)
    _conn().commit()


def delete_channel(channel_id):
    _conn().execute("DELETE FROM channels WHERE id=?", (channel_id,))
    _conn().commit()


def upsert_channel(cid, name, channel_type, config="{}", enabled=1):
    if isinstance(config, dict):
        config = json.dumps(config)
    _conn().execute(
        "INSERT OR REPLACE INTO channels (id, name, type, config, enabled) VALUES (?,?,?,?,?)",
        (cid, name, channel_type, config, enabled)
    )
    _conn().commit()


# ═══════════════════════════════════════════════
#  Templates
# ═══════════════════════════════════════════════

def get_templates():
    return [dict(r) for r in _conn().execute("SELECT * FROM templates ORDER BY id").fetchall()]


def get_template(template_id):
    r = _conn().execute("SELECT * FROM templates WHERE id=?", (template_id,)).fetchone()
    return dict(r) if r else None


def create_template(name, engine="jinja2", title_tpl="", content_tpl=""):
    c = _conn().execute(
        "INSERT INTO templates (name, engine, title_tpl, content_tpl) VALUES (?,?,?,?)",
        (name, engine, title_tpl, content_tpl)
    )
    _conn().commit()
    return c.lastrowid


def update_template(template_id, **kwargs):
    if not kwargs:
        return
    allowed = {"name", "engine", "title_tpl", "content_tpl"}
    sets = [f"{k}=?" for k in kwargs if k in allowed]
    vals = [kwargs[k] for k in kwargs if k in allowed]
    if not sets:
        return
    vals.append(template_id)
    _conn().execute(f"UPDATE templates SET {', '.join(sets)} WHERE id=?", vals)
    _conn().commit()


def delete_template(template_id):
    _conn().execute("DELETE FROM templates WHERE id=?", (template_id,))
    _conn().commit()


def upsert_template(tid, name, engine="jinja2", title_tpl="", content_tpl=""):
    _conn().execute(
        "INSERT OR REPLACE INTO templates (id, name, engine, title_tpl, content_tpl) VALUES (?,?,?,?,?)",
        (tid, name, engine, title_tpl, content_tpl)
    )
    _conn().commit()


# ═══════════════════════════════════════════════
#  Source-Channels bindings
# ═══════════════════════════════════════════════

def get_source_channels(source_id):
    return [dict(r) for r in _conn().execute(
        "SELECT * FROM source_channels WHERE source_id=? ORDER BY priority",
        (source_id,)
    ).fetchall()]


def get_all_source_channels():
    return [dict(r) for r in _conn().execute(
        "SELECT * FROM source_channels ORDER BY source_id, priority"
    ).fetchall()]


def create_source_channel(source_id, channel_id, template_id, condition_expr="", priority=0, enabled=1, urgent=0, dedup_key_expr="", dedup_window=3600):
    c = _conn().execute(
        """INSERT INTO source_channels
           (source_id, channel_id, template_id, condition_expr, priority, enabled, urgent, dedup_key_expr, dedup_window)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (source_id, channel_id, template_id, condition_expr, priority, enabled, urgent, dedup_key_expr, dedup_window)
    )
    _conn().commit()
    return c.lastrowid


def update_source_channel(sc_id, **kwargs):
    if not kwargs:
        return
    allowed = {"channel_id", "template_id", "condition_expr", "dedup_key_expr", "dedup_window", "priority", "enabled", "urgent"}
    sets = [f"{k}=?" for k in kwargs if k in allowed]
    vals = [kwargs[k] for k in kwargs if k in allowed]
    if not sets:
        return
    vals.append(sc_id)
    _conn().execute(f"UPDATE source_channels SET {', '.join(sets)} WHERE id=?", vals)
    _conn().commit()


def delete_source_channel(sc_id):
    _conn().execute("DELETE FROM source_channels WHERE id=?", (sc_id,))
    _conn().commit()


def upsert_source_channel(sc_id, source_id, channel_id, template_id,
                          condition_expr="", priority=0, enabled=1, urgent=0,
                          dedup_key_expr="", dedup_window=3600):
    _conn().execute(
        """INSERT OR REPLACE INTO source_channels
           (id, source_id, channel_id, template_id, condition_expr,
            dedup_key_expr, dedup_window, priority, enabled, urgent)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (sc_id, source_id, channel_id, template_id, condition_expr,
         dedup_key_expr, dedup_window, priority, enabled, urgent)
    )
    _conn().commit()


# ═══════════════════════════════════════════════
#  Messages
# ═══════════════════════════════════════════════

def create_message_log(trace_id, source_id, source_name="", raw_body="", status="RECEIVED"):
    c = _conn().execute(
        "INSERT INTO message_log (trace_id, source_id, source_name, raw_body, status) VALUES (?,?,?,?,?)",
        (trace_id, source_id, source_name, raw_body, status)
    )
    _conn().commit()
    return c.lastrowid


def update_message(trace_id, **kwargs):
    if not kwargs:
        return
    allowed = {"status", "msg_json", "error", "channel_results", "raw_body", "sent_at", "dedup_key"}
    sets = [f"{k}=?" for k in kwargs if k in allowed]
    if not sets:
        return
    vals = [kwargs[k] for k in kwargs if k in allowed]
    vals.append(trace_id)
    _conn().execute(f"UPDATE message_log SET {', '.join(sets)}, updated_at=CURRENT_TIMESTAMP WHERE trace_id=?", vals)
    _conn().commit()


def update_message_by_id(msg_id, **kwargs):
    if not kwargs:
        return
    allowed = {"status", "msg_json", "error", "channel_results", "raw_body", "sent_at", "dedup_key"}
    sets = [f"{k}=?" for k in kwargs if k in allowed]
    if not sets:
        return
    vals = [kwargs[k] for k in kwargs if k in allowed]
    vals.append(msg_id)
    _conn().execute(f"UPDATE message_log SET {', '.join(sets)}, updated_at=CURRENT_TIMESTAMP WHERE id=?", vals)
    _conn().commit()


def get_message(trace_id):
    r = _conn().execute("SELECT * FROM message_log WHERE trace_id=?", (trace_id,)).fetchone()
    return dict(r) if r else None


def get_message_by_id(msg_id):
    r = _conn().execute("SELECT * FROM message_log WHERE id=?", (msg_id,)).fetchone()
    return dict(r) if r else None


def get_pending_messages(source_id=None):
    if source_id:
        rows = _conn().execute(
            "SELECT * FROM message_log WHERE source_id=? AND status='PENDING' ORDER BY created_at",
            (source_id,),
        ).fetchall()
    else:
        rows = _conn().execute(
            "SELECT * FROM message_log WHERE status='PENDING' ORDER BY created_at"
        ).fetchall()
    return [dict(r) for r in rows]


def get_messages(source_id=None, status=None, channel_type=None, limit=100, offset=0):
    sql = "SELECT * FROM message_log WHERE 1=1"
    params = []
    if source_id:
        sql += " AND source_id=?"
        params.append(source_id)
    if status:
        sql += " AND status=?"
        params.append(status)
    if channel_type:
        sql += " AND channel_results LIKE ?"
        params.append(f'%"{channel_type}"%')
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    return [dict(r) for r in _conn().execute(sql, params).fetchall()]


def get_message_count(status=None, source_id=None, today_only=False):
    sql = "SELECT COUNT(*) FROM message_log WHERE 1=1"
    params = []
    if status:
        sql += " AND status=?"
        params.append(status)
    if source_id:
        sql += " AND source_id=?"
        params.append(source_id)
    if today_only:
        sql += " AND date(created_at)=date('now')"
    return _conn().execute(sql, params).fetchone()[0]


def delete_message(msg_id):
    _conn().execute("DELETE FROM message_log WHERE id=?", (msg_id,))
    _conn().commit()


# 所有消息状态类型及其说明
MESSAGE_STATUSES = {
    "RECEIVED":  "已接收，尚未被解析器处理",
    "PARSED":    "解析成功，等待路由匹配通道",
    "NO_MATCH":  "解析成功，但没有匹配的路由规则",
    "PENDING":   "处于免打扰时段，排队等待发送",
    "SENDING":   "正在发送中",
    "SUCCESS":   "所有通道推送成功",
    "FAILED":    "解析或推送失败",
    "DISCARDED": "去重命中，主动丢弃",
    "IGNORED":   "手动标记为已处理",
}


def get_cleanup_config():
    return {s: int(get_config(f"cleanup_{s}", "0")) for s in MESSAGE_STATUSES}


def cleanup_old_messages(overrides=None):
    cfg = get_cleanup_config()
    if overrides:
        cfg.update(overrides)
    conn = _conn()
    total = 0
    for status, hours in cfg.items():
        if hours <= 0:
            continue
        cursor = conn.execute(
            "DELETE FROM message_log WHERE status=? AND created_at < datetime('now','localtime',?||' hours')",
            (status, f"-{hours}")
        )
        total += cursor.rowcount
    conn.commit()
    return total


def mark_ignored(msg_id):
    _conn().execute("UPDATE message_log SET status='IGNORED' WHERE id=?", (msg_id,))
    _conn().commit()


def check_dedup(dedup_key, window_seconds):
    r = _conn().execute(
        "SELECT COUNT(*) FROM message_log WHERE dedup_key=? AND status='SUCCESS' AND sent_at > datetime('now','-'||?||' seconds')",
        (dedup_key, str(window_seconds))
    ).fetchone()
    return r[0] > 0


def get_queue_stats():
    total   = _conn().execute("SELECT COUNT(*) FROM message_log WHERE status IN ('PENDING','FAILED','SUCCESS')").fetchone()[0]
    pending = _conn().execute("SELECT COUNT(*) FROM message_log WHERE status='PENDING'").fetchone()[0]
    sent    = _conn().execute("SELECT COUNT(*) FROM message_log WHERE status='SUCCESS'").fetchone()[0]
    failed  = _conn().execute("SELECT COUNT(*) FROM message_log WHERE status='FAILED'").fetchone()[0]
    return {"total": total, "pending": pending, "sent": sent, "failed": failed}


# ═══════════════════════════════════════════════
#  System Config
# ═══════════════════════════════════════════════

def get_config(key, default=None):
    r = _conn().execute("SELECT value FROM system_config WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def set_config(key, value):
    _conn().execute(
        "INSERT OR REPLACE INTO system_config (key, value) VALUES (?,?)",
        (key, str(value))
    )
    _conn().commit()


def get_dnd():
    return {
        "enabled":    get_config("dnd_enabled", "0") == "1",
        "start_time": get_config("dnd_start", "23:00"),
        "end_time":   get_config("dnd_end", "07:00"),
    }


def get_log_level():
    return get_config("log_level", "WARNING")


# ═══════════════════════════════════════════════
#  Logs
# ═══════════════════════════════════════════════

def add_log(level, message, module="", trace_id=""):
    _conn().execute(
        "INSERT INTO logs (timestamp, level, message, module, trace_id) VALUES (datetime('now','localtime'),?,?,?,?)",
        (level, message, module, trace_id)
    )
    _conn().commit()
    _conn().execute(
        "DELETE FROM logs WHERE id NOT IN (SELECT id FROM logs ORDER BY id DESC LIMIT 10000)"
    )
    _conn().commit()


def get_logs(level=None, limit=200):
    level_order = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}
    sql = "SELECT * FROM logs WHERE 1=1"
    params = []
    if level and isinstance(level, str):
        min_level = level_order.get(level.upper(), 1)
        levels_to_show = [l for l, v in level_order.items() if v >= min_level]
        placeholders = ",".join(["?"] * len(levels_to_show))
        sql += f" AND level IN ({placeholders})"
        params.extend(levels_to_show)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in _conn().execute(sql, params).fetchall()]


def clear_logs():
    _conn().execute("DELETE FROM logs")
    _conn().commit()


def set_log_level(level):
    _conn().execute(
        "INSERT OR REPLACE INTO system_config (key, value) VALUES (?,?)",
        ("log_level", level)
    )
    _conn().commit()
    import logging
    import log
    log.logger.setLevel(getattr(logging, level, logging.INFO))
    for h in log.logger.handlers:
        h.setLevel(getattr(logging, level, logging.INFO))


# ═══════════════════════════════════════════════
#  Stats
# ═══════════════════════════════════════════════

def get_stats():
    qs = get_queue_stats()
    return {
        "sources":  _conn().execute("SELECT COUNT(*) FROM sources").fetchone()[0],
        "channels": _conn().execute("SELECT COUNT(*) FROM channels").fetchone()[0],
        "templates": _conn().execute("SELECT COUNT(*) FROM templates").fetchone()[0],
        "messages_today": get_message_count(today_only=True),
        "queue_total":   qs["total"],
        "queue_pending": qs["pending"],
        "queue_sent":    qs["sent"],
        "queue_failed":  qs["failed"],
        "messages_failed":  get_message_count(status="FAILED"),
        "messages_success": get_message_count(status="SUCCESS"),
    }

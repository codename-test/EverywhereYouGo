#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
数据库模块：建表 + 全部 CRUD。
v1.1 — 统一 message_log 表（合并旧 message_log + message_queue）
"""

import sqlite3
import json
import os
import uuid
import datetime as dt
import log

DB_PATH = os.getenv("DB_PATH", "ego.db")


def _conn():
    global _conn_singleton
    if '_conn_singleton' not in globals() or _conn_singleton is None:
        _conn_singleton = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
        _conn_singleton.row_factory = sqlite3.Row
        _conn_singleton.execute("PRAGMA busy_timeout=5000")
    return _conn_singleton


# ═══════════════════════════════════════════════
#  Init
# ═══════════════════════════════════════════════

def init_db():
    conn = _conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS parsers (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            filename    TEXT    NOT NULL UNIQUE,
            description TEXT    DEFAULT '',
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS sources (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            port        INTEGER UNIQUE NOT NULL,
            parser_id   INTEGER,
            enabled     INTEGER DEFAULT 1,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (parser_id) REFERENCES parsers(id)
        );

        CREATE TABLE IF NOT EXISTS channels (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            type        TEXT    NOT NULL,
            config      TEXT    NOT NULL DEFAULT '{}',
            enabled     INTEGER DEFAULT 1,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS templates (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            engine      TEXT    DEFAULT 'jinja2',
            title_tpl   TEXT    DEFAULT '',
            content_tpl TEXT    DEFAULT '',
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS source_channels (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id       INTEGER NOT NULL,
            channel_id      INTEGER NOT NULL,
            template_id     INTEGER NOT NULL,
            condition_expr  TEXT    DEFAULT '',
            dedup_key_expr  TEXT    DEFAULT '',
            dedup_window    INTEGER DEFAULT 3600,
            priority        INTEGER DEFAULT 0,
            enabled         INTEGER DEFAULT 1,
            urgent          INTEGER DEFAULT 0,
            FOREIGN KEY (source_id)   REFERENCES sources(id)   ON DELETE CASCADE,
            FOREIGN KEY (channel_id)  REFERENCES channels(id)  ON DELETE CASCADE,
            FOREIGN KEY (template_id) REFERENCES templates(id)
        );

        CREATE TABLE IF NOT EXISTS message_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id        TEXT    NOT NULL,
            source_id       INTEGER,
            source_name     TEXT,
            raw_body        TEXT,
            msg_json        TEXT,
            dedup_key       TEXT,
            status          TEXT    DEFAULT 'RECEIVED',
            channel_results TEXT,
            error           TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sent_at         TIMESTAMP,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS system_config (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS logs (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            level     TEXT    NOT NULL DEFAULT 'INFO',
            module    TEXT    DEFAULT '',
            message   TEXT    NOT NULL,
            trace_id  TEXT    DEFAULT ''
        );

        -- 内置 Emby 解析器
        INSERT OR IGNORE INTO parsers (id, name, filename, description)
        VALUES (1, 'Emby Webhook', 'emby.py', '解析 Emby/Jellyfin Webhook 数据');

        -- 默认模板
        INSERT OR IGNORE INTO templates (id, name, engine, title_tpl, content_tpl)
        VALUES (1, '默认模板', 'jinja2',
            '{{ msg.title }}',
            '{{ msg.content }}');

        -- DND / 日志等级默认值
        INSERT OR IGNORE INTO system_config (key, value) VALUES ('dnd_enabled', '0');
        INSERT OR IGNORE INTO system_config (key, value) VALUES ('dnd_start', '23:00');
        INSERT OR IGNORE INTO system_config (key, value) VALUES ('dnd_end', '07:00');
        INSERT OR IGNORE INTO system_config (key, value) VALUES ('log_level', 'INFO');
    """)


    # migrate old source_channels that lack dedup columns
    try:
        conn.execute("ALTER TABLE source_channels ADD COLUMN dedup_key_expr TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE source_channels ADD COLUMN dedup_window INTEGER DEFAULT 3600")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE source_channels ADD COLUMN urgent INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    # migrate old message_log that lack new columns
    for col in ['source_name', 'msg_json', 'dedup_key', 'channel_results', 'sent_at', 'updated_at']:
        try:
            if col == 'updated_at':
                conn.execute(f"ALTER TABLE message_log ADD COLUMN {col} TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
            else:
                conn.execute(f"ALTER TABLE message_log ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass

    # drop old message_queue if exists
    conn.execute("DROP TABLE IF EXISTS message_queue")
    conn.commit()


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
    """带显式 ID 的插入/覆盖（config_manager.load_all 用）。"""
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
    """带显式 ID 的插入/覆盖（config_manager.load_all 用）。"""
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
    """带显式 ID 的插入/覆盖（config_manager.load_all 用）。"""
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
    """带显式 ID 的插入/覆盖（config_manager.load_all 用）。"""
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


def create_source_channel(source_id, channel_id, template_id, condition_expr="", priority=0, enabled=1, urgent=0):
    c = _conn().execute(
        """INSERT INTO source_channels
           (source_id, channel_id, template_id, condition_expr, priority, enabled, urgent)
           VALUES (?,?,?,?,?,?,?)""",
        (source_id, channel_id, template_id, condition_expr, priority, enabled, urgent)
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
    """带显式 ID 的插入/覆盖（config_manager.load_all 用）。"""
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
#  Unified Message Log (merged old message_log + message_queue)
# ═══════════════════════════════════════════════

def create_message_log(trace_id, source_id, source_name="", raw_body="", status="RECEIVED"):
    """新建消息记录。返回自增 id。"""
    c = _conn().execute(
        "INSERT INTO message_log (trace_id, source_id, source_name, raw_body, status) VALUES (?,?,?,?,?)",
        (trace_id, source_id, source_name, raw_body, status)
    )
    _conn().commit()
    return c.lastrowid


def update_message(trace_id, **kwargs):
    """按 trace_id 更新消息记录。"""
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
    """按 id 更新消息记录。"""
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
    """按 trace_id 获取消息。"""
    r = _conn().execute("SELECT * FROM message_log WHERE trace_id=?", (trace_id,)).fetchone()
    return dict(r) if r else None


def get_message_by_id(msg_id):
    """按 id 获取消息。"""
    r = _conn().execute("SELECT * FROM message_log WHERE id=?", (msg_id,)).fetchone()
    return dict(r) if r else None


def get_pending_messages(source_id=None):
    """获取 PENDING 状态的队列消息。"""
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
    """分页查询消息，支持按 source_id/status/channel_type 筛选。"""
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
    """统计消息数量。"""
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
    """删除消息记录。"""
    _conn().execute("DELETE FROM message_log WHERE id=?", (msg_id,))
    _conn().commit()


def cleanup_old_messages():
    """清理旧消息：SUCCESS 7 天后删除，RECEIVED/PARSED 24h 后删除。"""
    _conn().execute(
        "DELETE FROM message_log WHERE status='SUCCESS' AND sent_at < datetime('now','-7 days')"
    )
    _conn().execute(
        "DELETE FROM message_log WHERE status IN ('RECEIVED','PARSED') AND created_at < datetime('now','-24 hours')"
    )
    _conn().commit()


def mark_ignored(msg_id):
    """标记消息为已忽略。"""
    _conn().execute("UPDATE message_log SET status='IGNORED' WHERE id=?", (msg_id,))
    _conn().commit()


def check_dedup(dedup_key, window_seconds):
    """检查是否命中去重：同一 dedup_key 在窗口时间内已 SUCCESS 发送过。"""
    r = _conn().execute(
        "SELECT COUNT(*) FROM message_log WHERE dedup_key=? AND status='SUCCESS' AND sent_at > datetime('now','-'||?||' seconds')",
        (dedup_key, str(window_seconds))
    ).fetchone()
    return r[0] > 0


def get_queue_stats():
    """队列统计（来自统一 message_log 表）。"""
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
    return get_config("log_level", "INFO")


# ═══════════════════════════════════════════════
#  Logs
# ═══════════════════════════════════════════════

def add_log(level, message, module="", trace_id=""):
    _conn().execute(
        "INSERT INTO logs (timestamp, level, message, module, trace_id) VALUES (datetime('now','localtime'),?,?,?,?)",
        (level, message, module, trace_id)
    )
    _conn().commit()
    # 保留最近 10000 条
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
        ("LOG_LEVEL", level)
    )
    _conn().commit()
    import logging
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

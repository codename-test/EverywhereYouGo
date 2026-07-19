#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
数据库 Schema 定义与迁移。
"""

import sqlite3
from .connection import _conn


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
            '');

        -- 迁移：旧版默认 content_tpl '{{ msg.content }}' → 空字符串（启用自动KV列表）
        UPDATE templates SET content_tpl = ''
            WHERE id = 1 AND content_tpl = '{{ msg.content }}';

        -- DND / 日志等级默认值
        INSERT OR IGNORE INTO system_config (key, value) VALUES ('dnd_enabled', '0');
        INSERT OR IGNORE INTO system_config (key, value) VALUES ('dnd_start', '23:00');
        INSERT OR IGNORE INTO system_config (key, value) VALUES ('dnd_end', '07:00');
        INSERT OR IGNORE INTO system_config (key, value) VALUES ('log_level', 'WARNING');
        INSERT OR IGNORE INTO system_config (key, value) VALUES ('cleanup_SUCCESS', '168');
        INSERT OR IGNORE INTO system_config (key, value) VALUES ('cleanup_FAILED', '720');
        INSERT OR IGNORE INTO system_config (key, value) VALUES ('cleanup_RECEIVED', '24');
        INSERT OR IGNORE INTO system_config (key, value) VALUES ('cleanup_PARSED', '24');
        INSERT OR IGNORE INTO system_config (key, value) VALUES ('cleanup_NO_MATCH', '168');
        INSERT OR IGNORE INTO system_config (key, value) VALUES ('cleanup_DISCARDED', '168');
        INSERT OR IGNORE INTO system_config (key, value) VALUES ('cleanup_IGNORED', '168');
        INSERT OR IGNORE INTO system_config (key, value) VALUES ('cleanup_PENDING', '0');
        INSERT OR IGNORE INTO system_config (key, value) VALUES ('cleanup_SENDING', '0');
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

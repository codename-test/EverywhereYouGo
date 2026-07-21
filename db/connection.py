#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
数据库连接管理。
"""

import sqlite3
import os

DB_PATH = os.getenv("DB_PATH", "ego.db")


def _conn():
    global _conn_singleton
    if '_conn_singleton' not in globals() or _conn_singleton is None:
        _conn_singleton = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
        _conn_singleton.row_factory = sqlite3.Row
        _conn_singleton.execute("PRAGMA busy_timeout=5000")
        _conn_singleton.execute("PRAGMA journal_mode=WAL")
    return _conn_singleton

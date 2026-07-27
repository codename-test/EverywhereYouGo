#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
数据库连接管理。

每个线程持有独立的 SQLite 连接（threading.local）。
旧实现是全局单例连接 + check_same_thread=False，那只是关掉了 Python 的
线程检查，并不能让单个连接在多线程下安全共用——并发时会出现
"recursive use of cursors"、事务串扰等问题。

说明：
- WAL 日志模式持久化在数据库文件中，首个连接开启后对其余连接生效，
  这里每个连接仍执行一次（幂等，无副作用）。
- busy_timeout 为连接级设置，必须每个连接单独设置。
- 连接只在其所属线程内使用，因此使用默认的 check_same_thread=True。
"""

import sqlite3
import os
import threading

DB_PATH = os.getenv("DB_PATH", "ego.db")

_local = threading.local()


def _conn():
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA journal_mode=WAL")
        _local.conn = conn
    return conn

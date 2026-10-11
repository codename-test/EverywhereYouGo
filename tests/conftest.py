# tests/conftest.py
"""统一测试 DB 隔离：每个测试模块一份独立 SQLite 库。

根因：db/connection.py:22 的 `DB_PATH = os.getenv("DB_PATH", "ego.db")` 是 import 期
模块常量，`_conn()` 每次读这个常量。各测试文件模块级 `os.environ["DB_PATH"]=...`
只有**第一个 import db 的模块**生效，其余全部共享同一个库（test_backup_hardening.py:77
注释自证，曾导致 test_event_chain 串扰）。

这里用 module 级 autouse fixture 直接把 `db.connection.DB_PATH` 指向独立临时库并
重置线程本地连接，取代那套失效的 env 把戏。测试内需要再换库的文件（backup /
plugin_dirs / parser_upload_race）自带 helper 会 save/restore DB_PATH，与本 fixture
叠加无冲突。
"""
import os
import sys
import shutil
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import db
import db.connection as _dbconn


def _reset_thread_conn():
    """关掉并清掉当前线程缓存的连接，强制下次 _conn() 连到新 DB_PATH。"""
    conn = getattr(_dbconn._local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        try:
            del _dbconn._local.conn
        except AttributeError:
            pass


@pytest.fixture(autouse=True, scope="module")
def _isolated_db():
    tmp = tempfile.mkdtemp(prefix="ego_test_db_")
    old_path = _dbconn.DB_PATH
    _reset_thread_conn()
    _dbconn.DB_PATH = os.path.join(tmp, "test.db")
    db.init_db()
    try:
        yield
    finally:
        _reset_thread_conn()
        _dbconn.DB_PATH = old_path
        shutil.rmtree(tmp, ignore_errors=True)

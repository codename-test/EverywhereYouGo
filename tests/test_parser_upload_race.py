# tests/test_parser_upload_race.py
"""v1.3.2 review #9：同名 Parser 并发上传的 TOCTOU 防护。

上传流程是「查 DB 同名 → 落盘 → 入库」三步，本身不是原子的。
两个请求同时上传同一文件名时，两边都会通过查重，最终可能出现
「磁盘上是 B 的内容、DB 里是 A 的行」——DB 的 UNIQUE 只保证入库不重复，
管不住"文件已经落盘"。

修复：plugin_paths.filename_lock(kind, filename) 把这段临界区串行化。
"""
import sys
import os
import io
import shutil
import tempfile
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test_upload_race.db")

import db
import plugin_paths
from api import create_app

NL = chr(10)


def _use_temp_db(tmp_dir):
    """把当前线程的 DB 切到独立库（避免污染共享测试库），返回原 DB_PATH。"""
    import db as _db
    import db.connection as _dbconn

    old_path = _dbconn.DB_PATH
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
    _dbconn.DB_PATH = os.path.join(tmp_dir, "isolated.db")
    _db.init_db()
    return old_path


def _restore_db(old_path):
    import db.connection as _dbconn
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
    _dbconn.DB_PATH = old_path


def _parser_code(tag):
    return ("PARSER_NAME = '%s'" % tag) + NL + \
           "def parse(b, h, q):" + NL + "    return {'v': '%s'}" % tag + NL


class TestParserUploadRace:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.old_parsers_user = plugin_paths.PARSERS_USER
        plugin_paths.PARSERS_USER = os.path.join(self.tmp, "parsers")
        os.makedirs(plugin_paths.PARSERS_USER, exist_ok=True)
        self._old_db = _use_temp_db(self.tmp)
        self.app = create_app(source_mgr=None)
        self.app.config["TESTING"] = True

    def teardown_method(self):
        plugin_paths.PARSERS_USER = self.old_parsers_user
        _restore_db(self._old_db)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_filename_lock_shared_per_key(self):
        """同一 (kind, filename) 返回同一把锁；不同 key 互不影响。"""
        a1 = plugin_paths.filename_lock("parser", "x.py")
        a2 = plugin_paths.filename_lock("parser", "x.py")
        b = plugin_paths.filename_lock("parser", "y.py")
        c = plugin_paths.filename_lock("channel", "x.py")
        assert a1 is a2
        assert a1 is not b
        assert a1 is not c

    def test_upload_blocked_while_lock_held(self):
        """持锁期间上传必须阻塞 —— 证明「查重 → 落盘 → 入库」真被串行化。"""
        filename = "same.py"
        lock = plugin_paths.filename_lock("parser", filename)
        lock.acquire()
        events = []

        def _upload():
            client = self.app.test_client()
            events.append("start")
            client.post("/api/parsers",
                        data={"name": "A",
                              "file": (io.BytesIO(_parser_code("A").encode("utf-8")),
                                       filename)},
                        content_type="multipart/form-data")
            events.append("done")

        t = threading.Thread(target=_upload)
        t.start()
        t.join(timeout=1.0)
        assert events == ["start"], \
            "上传未被锁阻塞，临界区未串行化: %s" % events

        lock.release()
        t.join(timeout=10)
        assert events == ["start", "done"], events

    def test_concurrent_same_name_upload_keeps_file_db_consistent(self):
        """并发上传同名文件：只有一个成功，且磁盘内容与 DB 记录一致。"""
        filename = "same.py"
        results = []
        barrier = threading.Barrier(2)

        def _upload(tag):
            client = self.app.test_client()
            barrier.wait()
            r = client.post("/api/parsers",
                            data={"name": tag,
                                  "file": (io.BytesIO(_parser_code(tag).encode("utf-8")),
                                           filename)},
                            content_type="multipart/form-data")
            results.append(r.status_code)

        ts = [threading.Thread(target=_upload, args=(t,)) for t in ("A", "B")]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=10)

        assert sorted(results) == [200, 400], results

        rows = [p for p in db.get_parsers() if p["filename"] == filename]
        assert len(rows) == 1, rows
        meta = plugin_paths.read_source_meta(
            os.path.join(plugin_paths.PARSERS_USER, filename), "PARSER")
        assert meta["name"] == rows[0]["name"], (
            "文件内容(%s) 与 DB 记录(%s) 不一致 —— TOCTOU"
            % (meta["name"], rows[0]["name"]))

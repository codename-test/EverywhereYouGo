# tests/test_source_update.py
"""PUT /api/sources/<id> 更新路径加固测试（P1-1 / P1-3）+ start_source 失败清理（P1-2）。"""
import os
import sys
import json
import shutil
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_test_db_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_test_db_dir, "test_source_update.db")
os.environ["EGO_SSL_ENABLED"] = "0"

import db


def _reset_db():
    conn = db._conn()
    for t in ("source_channels", "channels", "sources", "templates", "parsers"):
        conn.execute("DELETE FROM %s" % t)
    conn.execute("INSERT INTO parsers (id,name,filename) VALUES (1,'p1','p1.py')")
    conn.commit()


class _FakeSM:
    """假监听管理器：端口在 bad 集合里则 start_source 失败；记录 stop/start 调用。"""
    def __init__(self, bad_ports=()):
        self.bad = set(bad_ports)
        self.stopped = []
        self.started = []          # 记录每次 start 时 DB 里的端口

    def stop_source(self, sid):
        self.stopped.append(sid)

    def start_source(self, sid):
        src = db.get_source(sid)
        port = src.get("port") if src else None
        self.started.append(port)
        if port in self.bad:
            return (False, "Address already in use")
        return (True, None)


class TestSourceUpdate:
    @classmethod
    def setup_class(cls):
        db.init_db()

    def setup_method(self):
        _reset_db()

    def _client(self, sm):
        from api import create_app
        return create_app(source_mgr=sm).test_client()

    # ── P1-1：改端口撞占用 → 500 + 旧配置/旧监听恢复 ──

    def test_port_change_to_bad_rolls_back_and_500(self):
        conn = db._conn()
        conn.execute("INSERT INTO sources (id,name,slug,port,parser_id,enabled) "
                     "VALUES (10,'s','s10',9100,1,1)")
        conn.commit()
        sm = _FakeSM(bad_ports={9101})
        client = self._client(sm)
        r = client.put("/api/sources/10", data=json.dumps({"port": 9101}),
                       content_type="application/json")
        assert r.status_code == 500, r.data[:200]
        assert "listener failed to start" in json.loads(r.data).get("error", ""), r.data
        assert db.get_source(10)["port"] == 9100, "旧端口应已恢复"
        assert sm.started and sm.started[-1] == 9100, "旧监听应被重启: %s" % sm.started

    # ── P1-3：改 path 撞同组重复 → 409，且不改动 ──

    def test_path_change_conflict_409(self):
        conn = db._conn()
        conn.execute("INSERT INTO sources (id,name,slug,parser_id,enabled) VALUES (77,'g','grp',1,1)")
        conn.execute("INSERT INTO sources (id,name,slug,parent_id,path,parser_id,enabled) "
                     "VALUES (78,'a','a78',77,'movies',1,1)")
        conn.execute("INSERT INTO sources (id,name,slug,parent_id,path,parser_id,enabled) "
                     "VALUES (79,'b','b79',77,'shows',1,1)")
        conn.commit()
        client = self._client(_FakeSM())
        r = client.put("/api/sources/79", data=json.dumps({"path": "movies"}),
                       content_type="application/json")
        assert r.status_code == 409, r.data[:200]
        assert "path already exists" in json.loads(r.data).get("error", ""), r.data
        assert db.get_source(79)["path"] == "shows", "冲突不应改动"

    # ── P1-2：真实 ListenerManager，bind 失败后无 _servers/_threads 残留 ──

    def test_start_source_bind_failure_leaves_no_residue(self):
        import socket
        from source_listener import ListenerManager
        blocker = socket.socket()
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        port = blocker.getsockname()[1]
        try:
            conn = db._conn()
            conn.execute("INSERT INTO sources (id,name,slug,port,parser_id,enabled) "
                         "VALUES (?,?,?,?,?,?)", (55, 's', 's55', port, 1, 1))
            conn.commit()
            mgr = ListenerManager()
            started, err = mgr.start_source(55)
            assert started is False and err, (started, err)
            assert 55 not in mgr._servers, "失败后 _servers 不应残留"
            assert 55 not in mgr._threads, "失败后 _threads 不应残留"
        finally:
            blocker.close()

    @classmethod
    def teardown_class(cls):
        shutil.rmtree(_test_db_dir, ignore_errors=True)

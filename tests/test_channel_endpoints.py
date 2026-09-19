# tests/test_channel_endpoints.py
"""通道页按钮对应的后端接口（此前缺失，点了直接 404/405）。

覆盖：测试通道、复制通道、删除通道（含级联清理）。
"""
import sys
import os
import json
import sqlite3
import tempfile
import shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_test_db_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_test_db_dir, "test_ch_endpoints.db")
os.environ["EGO_SSL_ENABLED"] = "0"

import db
import channel_loader
from queue_backend import get_backend


def _reset():
    conn = db._conn()
    for t in ("message_queue", "message_log", "dead_letter_queue", "dedup_keys",
              "source_channels", "channels", "sources", "channel_breaker",
              "channel_rate_limit"):
        try:
            conn.execute("DELETE FROM %s" % t)
        except sqlite3.OperationalError:
            # 表可能尚未创建（例如只回滚到删除接口修复那一版），忽略即可
            pass
    conn.execute("INSERT INTO sources (id,name,parser_id,enabled) VALUES (1,'s',1,1)")
    conn.commit()


def _mk_channel(cid=7, name="c7"):
    conn = db._conn()
    conn.execute(
        "INSERT INTO channels (id,name,type,config,enabled) VALUES (?,?,?,?,1)",
        (cid, name, "wechat_work_bot", json.dumps({"webhook_url": "http://127.0.0.1:9/x"})))
    conn.execute(
        "INSERT INTO source_channels (source_id,channel_id,template_id,condition_expr,enabled) "
        "VALUES (1,?,1,'',1)", (cid,))
    conn.commit()


class TestChannelEndpoints:
    @classmethod
    def setup_class(cls):
        db.init_db()
        from api import create_app
        cls.client = create_app().test_client()

    def setup_method(self):
        _reset()

    # ── 三个此前缺失的路由 ──

    def test_test_channel_route_exists(self):
        _mk_channel()
        r = self.client.post("/api/channels/7/test")
        assert r.status_code == 200, r.status_code
        d = json.loads(r.data)
        assert "ok" in d, "前端期望 {ok: bool}"
        assert d["ok"] is False             # 死地址，测不通

    def test_test_channel_not_found(self):
        r = self.client.post("/api/channels/999/test")
        assert r.status_code == 404

    def test_duplicate_channel_route_exists(self):
        _mk_channel()
        r = self.client.post("/api/channels/7/duplicate")
        assert r.status_code == 200, r.status_code
        new_id = json.loads(r.data)["id"]
        dup = db.get_channel(new_id)
        assert dup is not None
        assert dup["enabled"] == 0, "复制出来的通道应默认禁用"
        assert dup["type"] == "wechat_work_bot"
        assert "(copy)" in dup["name"]

    def test_duplicate_copies_rate_limit(self):
        _mk_channel()
        from rate_limiter import get_limiter
        get_limiter().set_rate(7, 42)
        new_id = json.loads(self.client.post("/api/channels/7/duplicate").data)["id"]
        assert get_limiter().get_rate(new_id) == 42, "复制通道应连带复制出站限流"

    def test_delete_channel_route_exists(self):
        _mk_channel()
        r = self.client.delete("/api/channels/7")
        assert r.status_code == 200, r.status_code
        assert db.get_channel(7) is None

    def test_delete_channel_not_found(self):
        assert self.client.delete("/api/channels/999").status_code == 404

    # ── 删除的级联清理（foreign_keys 未开启，必须手工做）──

    def test_delete_cascades_bindings_and_resilience_rows(self):
        _mk_channel()
        conn = db._conn()
        conn.execute("INSERT INTO dedup_keys (channel_id, dedup_key) VALUES (7,'k')")
        conn.execute("INSERT INTO channel_breaker (channel_id, state) VALUES (7,'open')")
        conn.execute("INSERT INTO channel_rate_limit (channel_id, per_minute) VALUES (7,9)")
        conn.commit()

        self.client.delete("/api/channels/7")

        for table, col in (("source_channels", "channel_id"), ("dedup_keys", "channel_id"),
                           ("channel_breaker", "channel_id"), ("channel_rate_limit", "channel_id")):
            left = conn.execute(
                "SELECT COUNT(*) FROM %s WHERE %s=?" % (table, col), (7,)).fetchone()[0]
            assert left == 0, "%s 里还留着通道 7 的孤儿记录" % table

    def test_delete_moves_queued_items_to_dlq(self):
        _mk_channel()
        q = get_backend()
        q.enqueue(trace_id="q1", source_id=1, msg_json='{"title":"T"}',
                  channel_id=7, template_id=1)
        q.enqueue(trace_id="q2", source_id=1, msg_json='{"title":"T"}',
                  channel_id=7, template_id=1)

        self.client.delete("/api/channels/7")

        conn = db._conn()
        assert conn.execute("SELECT COUNT(*) FROM message_queue").fetchone()[0] == 0
        dlq = conn.execute(
            "SELECT trace_id, error FROM dead_letter_queue ORDER BY id").fetchall()
        assert len(dlq) == 2, "待发任务应整批移入死信（不是 N 倍重复，也不是丢弃）"
        assert {r["trace_id"] for r in dlq} == {"q1", "q2"}
        assert all("channel deleted" in r["error"] for r in dlq)

    def test_delete_does_not_touch_other_channels(self):
        _mk_channel(7, "c7")
        _mk_channel(8, "c8")
        conn = db._conn()
        conn.execute("INSERT INTO dedup_keys (channel_id, dedup_key) VALUES (8,'keep')")
        conn.commit()

        self.client.delete("/api/channels/7")

        assert db.get_channel(8) is not None
        assert conn.execute(
            "SELECT COUNT(*) FROM source_channels WHERE channel_id=8").fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM dedup_keys WHERE channel_id=8").fetchone()[0] == 1

    @classmethod
    def teardown_class(cls):
        shutil.rmtree(_test_db_dir, ignore_errors=True)

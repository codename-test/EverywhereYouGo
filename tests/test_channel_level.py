# tests/test_channel_level.py
"""P1 测试：渠道级去重、渠道级重发、多 worker 结果回写一致性。"""
import sys
import os
import json
import threading
import tempfile
import shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_test_db_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_test_db_dir, "test_channel_level.db")

import db
import sender_engine
import source_manager


def _clear():
    conn = db._conn()
    for t in ("message_queue", "message_log", "dedup_keys",
              "source_channels", "channels", "templates", "sources"):
        conn.execute("DELETE FROM %s" % t)
    conn.commit()


class TestChannelLevelDedup:
    @classmethod
    def setup_class(cls):
        db.init_db()

    def setup_method(self):
        _clear()

    def _matched(self, *specs):
        """specs: (channel_id, dedup_expr, window)"""
        return [
            {"channel_id": cid, "template_id": 1,
             "dedup_key_expr": expr, "dedup_window": win}
            for cid, expr, win in specs
        ]

    def _queue_ids(self):
        return [r[0] for r in db._conn().execute(
            "SELECT channel_id FROM message_queue ORDER BY id")]

    def test_no_dedup_enqueues_all(self):
        matched = self._matched((1, "", 3600), (2, "", 3600))
        db.create_message_log("t1", 1, "s", "{}", "RECEIVED")
        sender_engine._on_message_routed(None, trace_id="t1", source_id=1,
                                         msg={"event": "e1"}, matched_channels=matched)
        assert self._queue_ids() == [1, 2]

    def test_per_channel_dedup_only_skips_hit_channel(self):
        """渠道 1 命中去重，渠道 2 应照常入队（旧实现会整条丢弃）。"""
        matched = self._matched((1, "event", 3600), (2, "event", 3600))
        db.dedup_record(1, "e1")            # 只有渠道 1 发过
        db.create_message_log("t2", 1, "s", "{}", "RECEIVED")

        sender_engine._on_message_routed(None, trace_id="t2", source_id=1,
                                         msg={"event": "e1"}, matched_channels=matched)

        assert self._queue_ids() == [2], "渠道 2 不应被渠道 1 的去重命中牵连"
        rec = db.get_message("t2")
        assert rec["status"] == "SENDING", "还有渠道要发，不应整体 DISCARDED"

    def test_all_channels_deduped_marks_discarded(self):
        matched = self._matched((1, "event", 3600), (2, "event", 3600))
        db.dedup_record(1, "e1")
        db.dedup_record(2, "e1")
        db.create_message_log("t3", 1, "s", "{}", "RECEIVED")

        sender_engine._on_message_routed(None, trace_id="t3", source_id=1,
                                         msg={"event": "e1"}, matched_channels=matched)

        assert self._queue_ids() == []
        assert db.get_message("t3")["status"] == "DISCARDED"

    def test_different_bindings_use_own_keys(self):
        """两个绑定各自的去重表达式都应生效（旧实现只取第一个就 break）。"""
        matched = self._matched((1, "event", 3600), (2, "title", 3600))
        db.dedup_record(1, "e1")            # 渠道1 的键 event=e1 命中
        db.create_message_log("t4", 1, "s", "{}", "RECEIVED")

        sender_engine._on_message_routed(
            None, trace_id="t4", source_id=1,
            msg={"event": "e1", "title": "T"}, matched_channels=matched)

        # 渠道 2 的键是 title=T，从未发过 → 应入队
        assert self._queue_ids() == [2]

    def test_dedup_key_recorded_per_channel(self):
        db.dedup_record(7, "k")
        assert db.dedup_hit(7, "k", 3600) is True
        assert db.dedup_hit(8, "k", 3600) is False, "去重必须按渠道隔离"

    def test_window_expiry(self):
        db.dedup_record(9, "k")
        assert db.dedup_hit(9, "k", 0) is False, "窗口为 0 视为已过期"


class TestChannelLevelRetry:
    @classmethod
    def setup_class(cls):
        db.init_db()

    def setup_method(self):
        _clear()

    def _mkmsg(self, trace, results):
        db.create_message_log(trace, 1, "s", "{}", "FAILED")
        db.update_message(trace, channel_results=json.dumps(results),
                          msg_json=json.dumps({"event": "e", "title": "T"}))
        return db.get_message(trace)

    def test_failed_channel_ids_extracted(self):
        rec = self._mkmsg("r1", [
            {"channel_id": 1, "ok": True, "ch_name": "a"},
            {"channel_id": 2, "ok": False, "ch_name": "b"},
        ])
        assert source_manager._failed_channel_ids(rec) == {2}

    def test_legacy_results_without_channel_id_return_none(self):
        rec = self._mkmsg("r2", [{"ch_name": "a", "ok": False}])
        assert source_manager._failed_channel_ids(rec) is None, "旧记录应回退整条重发"

    def test_empty_results_return_none(self):
        rec = self._mkmsg("r3", [])
        assert source_manager._failed_channel_ids(rec) is None

    def test_all_succeeded_returns_empty_set(self):
        rec = self._mkmsg("r4", [{"channel_id": 1, "ok": True}])
        assert source_manager._failed_channel_ids(rec) == set()

    def test_retry_scope_failed_only_targets_failed(self, monkeypatch):
        """scope=failed 时只把失败渠道交给 send_to_channels。"""
        captured = {}

        def fake_match(source_id, msg):
            return [{"channel_id": 1, "template_id": 1},
                    {"channel_id": 2, "template_id": 1}]

        def fake_send(trace_id, source_id, msg, matched):
            captured["ids"] = [sc["channel_id"] for sc in matched]
            return True, msg

        monkeypatch.setattr(source_manager.router_engine, "match_for_source", fake_match)
        monkeypatch.setattr(source_manager.sender_engine, "send_to_channels", fake_send)

        self._mkmsg("r5", [
            {"channel_id": 1, "ok": True},
            {"channel_id": 2, "ok": False},
        ])
        mid = db.get_message("r5")["id"]
        ok, err = source_manager.retry_message(mid)
        assert ok is True, err
        assert captured["ids"] == [2], "只应重发失败的渠道 2"

    def test_retry_scope_all_targets_everyone(self, monkeypatch):
        captured = {}

        def fake_match(source_id, msg):
            return [{"channel_id": 1, "template_id": 1},
                    {"channel_id": 2, "template_id": 1}]

        def fake_send(trace_id, source_id, msg, matched):
            captured["ids"] = [sc["channel_id"] for sc in matched]
            return True, msg

        monkeypatch.setattr(source_manager.router_engine, "match_for_source", fake_match)
        monkeypatch.setattr(source_manager.sender_engine, "send_to_channels", fake_send)

        self._mkmsg("r6", [{"channel_id": 1, "ok": True}, {"channel_id": 2, "ok": False}])
        mid = db.get_message("r6")["id"]
        ok, err = source_manager.retry_message(mid, scope="all")
        assert ok is True, err
        assert captured["ids"] == [1, 2]

    def test_retry_all_succeeded_refuses(self, monkeypatch):
        monkeypatch.setattr(source_manager.router_engine, "match_for_source",
                            lambda s, m: [{"channel_id": 1, "template_id": 1}])
        self._mkmsg("r7", [{"channel_id": 1, "ok": True}])
        mid = db.get_message("r7")["id"]
        ok, err = source_manager.retry_message(mid)
        assert ok is False and "无需重发" in err


class TestMultiWorkerResultConsistency:
    """多 worker 并发回写 channel_results 不应丢更新。"""

    @classmethod
    def setup_class(cls):
        db.init_class_db() if hasattr(db, "init_class_db") else db.init_db()

    def setup_method(self):
        _clear()

    def test_concurrent_appends_do_not_lose_updates(self):
        trace = "c1"
        db.create_message_log(trace, 1, "s", "{}", "SENDING")
        db.update_message(trace, channel_results="[]")

        n = 40
        threads = [
            threading.Thread(
                target=sender_engine.update_message_results, args=(trace,),
                kwargs={"channel_result": {"channel_id": i, "ch_name": "ch%d" % i,
                                           "ok": True, "error": None}},
            )
            for i in range(n)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        stored = json.loads(db.get_message(trace)["channel_results"])
        ids = sorted(r["channel_id"] for r in stored)
        assert len(stored) == n, f"应保留全部 {n} 条结果，实际 {len(stored)}"
        assert ids == list(range(n)), "结果不应丢失或错乱"

    @classmethod
    def teardown_class(cls):
        shutil.rmtree(_test_db_dir, ignore_errors=True)

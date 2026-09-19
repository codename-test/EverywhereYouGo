# tests/test_event_chain.py
"""事件链回归测试：一条消息**只应被投递一次**。

背景（2026-07-28 发现）：source_manager.process_message 除事件链外，
自己又重复 emit 了 message.parsed 与 message.routed，导致同一消息被投递 3 次：

    process_message
      ├─ emit(message_received) → parser_engine
      │                            └─ emit(message.parsed) → router_engine
      │                                                       └─ emit(message.routed) → ①
      ├─ emit(message.parsed)  → router_engine
      │                            └─ emit(message.routed)                            → ②
      └─ emit(message.routed)                                                          → ③

该 bug 自 v1.1.0（commit 47ac7f9a）起存在，一直到 v1.2.4。
"""
import sys
import os
import tempfile
import shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_test_db_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_test_db_dir, "test_event_chain.db")

import db
import bus
import parser_loader
# 三个引擎都必须 import，事件处理器才会注册到总线上
# （source_manager 只 import router_engine/sender_engine，不含 parser_engine）
import parser_engine
import router_engine
import sender_engine
import source_manager


def _seed():
    conn = db._conn()
    for t in ("message_queue", "message_log", "dead_letter_queue", "dedup_keys",
              "source_channels", "channels", "sources"):
        conn.execute("DELETE FROM %s" % t)
    # parsers(id=1, emby.py) 与 templates(id=1, 默认模板) 由 init_db() 预置，直接复用
    conn.execute("INSERT INTO sources (id,name,parser_id,enabled) VALUES (1,'s',1,1)")
    conn.execute("INSERT INTO channels (id,name,type,config,enabled) "
                 "VALUES (1,'c','wechat_work_bot','{}',1)")
    conn.execute("INSERT INTO source_channels "
                 "(source_id,channel_id,template_id,condition_expr,enabled) "
                 "VALUES (1,1,1,'',1)")
    conn.commit()


class _OkChannel:
    CHANNEL_TYPE = "fake"
    def send(self, title, content):
        return True, ""
    def test(self):
        return True


class _FailChannel:
    CHANNEL_TYPE = "fake"
    def send(self, title, content):
        return False, "HTTP 500 boom"
    def test(self):
        return False


class TestLifecycleEvents:
    """P2-3：message.sending / message.sent / message.failed 必须真的被触发。"""

    @classmethod
    def setup_class(cls):
        db.init_db()

    def setup_method(self):
        _seed()
        self._orig_run = parser_loader.run_parser
        parser_loader.run_parser = lambda *a, **k: {"title": "T", "event": "e"}
        self._orig_create = sender_engine.create_channel
        self.emitted = []
        self._orig_emit = bus.emit

    def teardown_method(self):
        parser_loader.run_parser = self._orig_run
        sender_engine.create_channel = self._orig_create
        bus.emit = self._orig_emit

    def _spy_emit(self):
        """记录**真实触发顺序**。

        不能用订阅者回调来记录顺序：同步嵌套下，外层事件的订阅者在
        整条内层链跑完之后才被调用，记录出来是「由内向外」的。
        """
        def _emit(signal, **kw):
            self.emitted.append(signal.name)
            return self._orig_emit(signal, **kw)
        bus.emit = _emit

    def _drain_queue(self):
        """取出队列任务并处理，模拟 worker。"""
        q = sender_engine.get_backend()
        while True:
            item = q.dequeue()
            if not item:
                break
            ok, result = sender_engine.process_queue_item(item)
            if result and not result.get("deferred"):
                result.setdefault("channel_id", item.get("channel_id"))
                q.ack(item["id"])
                sender_engine.update_message_results(item["trace_id"], result)

    def test_success_lifecycle_order(self):
        sender_engine.create_channel = lambda t, c: _OkChannel()
        self._spy_emit()

        source_manager.process_message(1, b'{"x":1}', {}, {})
        self._drain_queue()

        assert self.emitted == ["message.received", "message.parsed",
                                "message.routed", "message.sending",
                                "message.sent"], \
            "成功路径的事件顺序不对：%s" % self.emitted

    def test_failure_lifecycle_ends_with_failed(self):
        sender_engine.create_channel = lambda t, c: _FailChannel()
        self._spy_emit()

        source_manager.process_message(1, b'{"x":1}', {}, {})
        self._drain_queue()

        assert "message.failed" in self.emitted, "失败路径应触发 message.failed"
        assert self.emitted[-1] == "message.failed", "最后应是 failed：%s" % self.emitted
        assert "message_sent" not in self.emitted, "失败路径不应触发 sent"

    def test_parse_failure_emits_failed_with_stage(self):
        parser_loader.run_parser = lambda *a, **k: (_ for _ in ()).throw(ValueError("bad"))
        stages = []

        def _rec(sender, **kw):
            stages.append(kw.get("stage"))
            return None

        bus.on(bus.message_failed, _rec)
        try:
            source_manager.process_message(1, b'x', {}, {})
        finally:
            bus.off(bus.message_failed, _rec)

        assert stages == ["parse"], "解析失败应带 stage=parse，实际 %s" % stages

    def test_sending_not_emitted_when_all_deduped(self):
        """全渠道被去重拦下时不应发出 message.sending（根本没进入发送）。"""
        conn = db._conn()
        conn.execute("UPDATE source_channels SET dedup_key_expr='event' WHERE source_id=1")
        conn.commit()
        db.dedup_record(1, "e")

        self._spy_emit()
        source_manager.process_message(1, b'{"x":1}', {}, {})

        assert "message.sending" not in self.emitted, "全去重不应进入发送阶段"
        assert self.emitted == ["message.received", "message.parsed", "message.routed"], \
            "全去重应止步于 routed：%s" % self.emitted

    @classmethod
    def teardown_class(cls):
        shutil.rmtree(_test_db_dir, ignore_errors=True)


class TestSingleDelivery:
    @classmethod
    def setup_class(cls):
        db.init_db()

    def setup_method(self):
        _seed()
        # 用假的解析器输出，避免依赖真实 parsers/*.py 文件
        self._orig_run = parser_loader.run_parser
        parser_loader.run_parser = lambda *a, **k: {"title": "T", "event": "e"}

    def teardown_method(self):
        parser_loader.run_parser = self._orig_run

    def _queue_count(self):
        return db._conn().execute(
            "SELECT COUNT(*) FROM message_queue WHERE channel_id=1"
        ).fetchone()[0]

    def test_one_message_enqueues_exactly_once(self):
        ok, msg = source_manager.process_message(1, b'{"x":1}', {}, {})
        assert ok is True
        assert self._queue_count() == 1, (
            "一条消息应对每个渠道只入队 1 次，实际 %d 次" % self._queue_count()
        )

    def test_routed_handler_invoked_exactly_once(self):
        calls = []

        def _counter(sender, **kw):
            calls.append(kw.get("trace_id"))
            return None

        bus.on(bus.message_routed, _counter)
        try:
            source_manager.process_message(1, b'{"x":1}', {}, {})
        finally:
            bus.off(bus.message_routed, _counter)

        assert len(calls) == 1, (
            "message.routed 处理器应被触发 1 次，实际 %d 次（重复投递 bug 回归）" % len(calls)
        )

    def test_parsed_handler_invoked_exactly_once(self):
        calls = []

        def _counter(sender, **kw):
            calls.append(kw.get("trace_id"))
            return None

        bus.on(bus.message_parsed, _counter)
        try:
            source_manager.process_message(1, b'{"x":1}', {}, {})
        finally:
            bus.off(bus.message_parsed, _counter)

        assert len(calls) == 1, "message.parsed 应只触发 1 次，实际 %d 次" % len(calls)

    def test_extra_fields_merged_before_routing(self):
        """extra_fields（如 sub_path）必须在路由之前进入 msg。"""
        seen = {}

        def _spy(sender, **kw):
            seen["msg"] = dict(kw.get("msg") or {})
            seen["matched"] = kw.get("matched_channels")
            return True, kw.get("msg")

        bus.on(bus.message_routed, _spy)
        try:
            source_manager.process_message(
                1, b'{"x":1}', {}, {}, extra_fields={"sub_path": "movie"})
        finally:
            bus.off(bus.message_routed, _spy)

        assert seen["msg"].get("sub_path") == "movie", "sub_path 应已合并进 msg"
        assert seen["matched"], "应匹配到渠道"

    def test_parse_failure_returns_false(self):
        def _boom(*a, **k):
            raise ValueError("bad payload")

        parser_loader.run_parser = _boom
        ok, msg = source_manager.process_message(1, b'garbage', {}, {})
        assert ok is False
        assert self._queue_count() == 0, "解析失败不应入队"

    @classmethod
    def teardown_class(cls):
        shutil.rmtree(_test_db_dir, ignore_errors=True)

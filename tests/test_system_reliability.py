# tests/test_system_reliability.py
"""P2-1 系统级测试：故障注入、熔断/限流联动、重试→死信、崩溃恢复、并发消费。

与单元测试的区别：这里走的是「队列 → worker 取任务 → 发送 → 结果回写」的真实链路，
用可编程的假通道注入故障，验证系统在异常下的行为，而不只是函数返回值。
"""
import sys
import os
import json
import threading
import tempfile
import shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_test_db_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_test_db_dir, "test_reliability.db")

import db
import bus
import parser_engine      # noqa: F401  注册事件处理器
import router_engine      # noqa: F401
import sender_engine
import circuit_breaker
import rate_limiter
from queue_backend import get_backend, SQLiteQueueBackend


# ── 可编程假通道 ──────────────────────────────

class ScriptedChannel:
    """按脚本响应的假通道。

    script 为 (ok, err) 列表；用完后重复最后一项。
    调用次数记录在 calls 里，便于断言「有没有真的发」。
    """

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def send(self, title, content):
        i = min(self.calls, len(self.script) - 1)
        self.calls += 1
        return self.script[i]

    def test(self):
        return True


def _reset_db():
    conn = db._conn()
    for t in ("message_queue", "message_log", "dead_letter_queue", "dedup_keys",
              "source_channels", "channels", "sources", "channel_breaker",
              "channel_rate_limit"):
        conn.execute("DELETE FROM %s" % t)
    conn.execute("INSERT INTO sources (id,name,parser_id,enabled) VALUES (1,'s',1,1)")
    conn.execute("INSERT INTO channels (id,name,type,config,enabled) "
                 "VALUES (1,'c','fake','{}',1)")
    conn.execute("INSERT INTO source_channels "
                 "(source_id,channel_id,template_id,condition_expr,enabled) "
                 "VALUES (1,1,1,'',1)")
    conn.commit()


def _enqueue(trace, max_retries=3):
    get_backend().enqueue(trace_id=trace, source_id=1, msg_json='{"title":"T"}',
                          channel_id=1, template_id=1, dedup_key="",
                          max_retries=max_retries)


def _run_one(scripted_channel):
    """跑一轮 worker：取任务 → 处理 → ack/nack/defer → 回写结果。

    返回 (ok, result) 或 None（队列空）。
    """
    q = get_backend()
    item = q.dequeue()
    if item is None:
        return None

    sender_engine.create_channel = lambda t, c: scripted_channel
    ok, result = sender_engine.process_queue_item(item)

    if result and result.get("deferred"):
        q.defer(item["id"], result.get("defer_seconds", 5))
        return "deferred", result

    if ok:
        q.ack(item["id"])
    else:
        q.nack(item["id"], (result or {}).get("error", "?"))
    if result:
        result.setdefault("channel_id", item.get("channel_id"))
        sender_engine.update_message_results(item["trace_id"], result)
    return ok, result


class _Base:
    @classmethod
    def setup_class(cls):
        db.init_db()

    def setup_method(self):
        _reset_db()
        # 每个用例都用全新的熔断器/限流器，避免相互污染
        self.breaker = circuit_breaker.CircuitBreaker()
        self.breaker._loaded = True
        self.limiter = rate_limiter.RateLimiter()
        self.limiter._loaded = True
        circuit_breaker.get_breaker = lambda: self.breaker
        rate_limiter.get_limiter = lambda: self.limiter

    def _mkmsg(self, trace, status="SENDING"):
        db.create_message_log(trace, 1, "s", "{}", status)


class TestCircuitBreakerIntegration(_Base):
    """故障注入：第三方持续故障时的系统行为。"""

    def test_5xx_trips_breaker_then_defers(self, monkeypatch):
        monkeypatch.setattr(circuit_breaker, "CONSECUTIVE_THRESHOLD", 3)
        ch = ScriptedChannel([(False, "HTTP 500")])

        # 3 条消息各失败一次 → 连续失败达阈值 → 熔断
        for i in range(3):
            t = "t%d" % i
            self._mkmsg(t)
            _enqueue(t)
            _run_one(ch)

        assert self.breaker.should_allow(1)[0] is False, "连续 5xx 应触发熔断"

        # 熔断后的消息：延迟重排，**不消耗重试次数**
        self._mkmsg("after")
        _enqueue("after")
        state, result = _run_one(ch)
        assert state == "deferred"
        assert result["deferred"] is True

        row = db._conn().execute(
            "SELECT retry_count, defer_count, status FROM message_queue WHERE trace_id=?",
            ("after",)).fetchone()
        assert row is not None, "延迟重排的任务应还在队列里"
        assert row["retry_count"] == 0, "熔断期间不应消耗重试次数"
        assert row["defer_count"] == 1
        assert row["status"] == "pending", "应留在队列等待恢复，而不是进死信"

    def test_4xx_does_not_trip_breaker(self, monkeypatch):
        monkeypatch.setattr(circuit_breaker, "CONSECUTIVE_THRESHOLD", 3)
        ch = ScriptedChannel([(False, "HTTP 400 Bad Request")])

        for i in range(6):
            t = "b%d" % i
            self._mkmsg(t)
            _enqueue(t, max_retries=99)
            _run_one(ch)

        allowed, reason = self.breaker.should_allow(1)
        assert allowed, "4xx 是业务侧拒绝，不应触发熔断（reason=%s）" % reason

    def test_timeout_counts_as_failure(self, monkeypatch):
        monkeypatch.setattr(circuit_breaker, "CONSECUTIVE_THRESHOLD", 3)
        ch = ScriptedChannel([(False, "Read timed out.")])
        for i in range(3):
            t = "to%d" % i
            self._mkmsg(t)
            _enqueue(t)
            _run_one(ch)
        assert self.breaker.should_allow(1)[0] is False, "超时应计入失败"

    def test_breaker_recovers_after_probes(self, monkeypatch):
        monkeypatch.setattr(circuit_breaker, "CONSECUTIVE_THRESHOLD", 3)
        monkeypatch.setattr(circuit_breaker, "OPEN_BASE_SECONDS", 0.05)
        monkeypatch.setattr(circuit_breaker, "OPEN_MAX_SECONDS", 0.1)
        monkeypatch.setattr(circuit_breaker, "HALF_OPEN_NEEDED", 2)

        bad = ScriptedChannel([(False, "HTTP 503")])
        for i in range(3):
            t = "r%d" % i
            self._mkmsg(t)
            _enqueue(t)
            _run_one(bad)
        assert self.breaker.should_allow(1)[0] is False

        import time
        time.sleep(0.08)

        # 第三方恢复 → 探测成功若干次后应回到 CLOSED
        good = ScriptedChannel([(True, "")])
        for i in range(2):
            self.breaker.should_allow(1)          # 触发 HALF_OPEN 放行
            self.breaker.record(1, True, "")

        assert self.breaker.should_allow(1)[0] is True
        assert self.breaker._states[1].state == circuit_breaker.CLOSED


class TestRateLimitIntegration(_Base):
    def test_exhausted_tokens_defer_without_retry(self):
        self.limiter.set_rate(1, 1)     # 每分钟 1 条
        ch = ScriptedChannel([(True, "")])

        # 第 1 条：有令牌 → 发出去
        self._mkmsg("r1")
        _enqueue("r1")
        state, _ = _run_one(ch)
        assert state is True, "第 1 条应成功"

        # 第 2 条：令牌耗尽 → 延迟重排，不消耗重试
        self._mkmsg("r2")
        _enqueue("r2")
        state, result = _run_one(ch)
        assert state == "deferred"
        assert "Rate limited" in result["error"]

        row = db._conn().execute(
            "SELECT retry_count, defer_count FROM message_queue WHERE trace_id=?",
            ("r2",)).fetchone()
        assert row is not None, "被限流的任务应还在队列里"
        assert row["retry_count"] == 0
        assert row["defer_count"] == 1


class TestRetryAndDLQ(_Base):
    def test_retries_then_moves_to_dlq(self):
        q = get_backend()
        ch = ScriptedChannel([(False, "HTTP 500")])
        self._mkmsg("d1")
        _enqueue("d1", max_retries=3)

        # 3 次尝试（每次 nack 都会把 next_retry_at 推到未来，这里手工提前）
        for _ in range(3):
            db._conn().execute("UPDATE message_queue SET next_retry_at=datetime('now')")
            db._conn().commit()
            _run_one(ch)

        assert db._conn().execute(
            "SELECT COUNT(*) FROM message_queue").fetchone()[0] == 0, "重试耗尽应离开队列"
        dlq = q.get_dlq_items()
        assert len(dlq) == 1, "应进入死信队列"
        assert dlq[0]["trace_id"] == "d1"

    def test_dlq_retry_puts_item_back(self):
        q = get_backend()
        ch = ScriptedChannel([(False, "HTTP 502")])
        self._mkmsg("d2")
        _enqueue("d2", max_retries=1)
        _run_one(ch)                      # 一次即耗尽 → DLQ

        dlq = q.get_dlq_items()
        assert len(dlq) == 1
        assert q.retry_dlq(dlq[0]["id"]) is True

        assert db._conn().execute(
            "SELECT COUNT(*) FROM message_queue").fetchone()[0] == 1, "应从死信重新入队"
        assert q.get_dlq_items() == []


class TestCrashRecovery(_Base):
    def test_processing_items_recovered_after_restart(self):
        self._mkmsg("c1")
        _enqueue("c1")
        q = get_backend()
        item = q.dequeue()                 # → processing
        assert item is not None
        assert db._conn().execute(
            "SELECT status FROM message_queue").fetchone()["status"] == "processing"

        # 模拟进程崩溃后重启：新建 backend 并恢复
        fresh = SQLiteQueueBackend()
        fresh.recover_processing()

        row = db._conn().execute(
            "SELECT status, retry_count FROM message_queue").fetchone()
        assert row["status"] == "pending", "崩溃遗留的 processing 任务应被复原"
        assert row["retry_count"] == 0, "恢复不应消耗重试次数"


class TestUnifiedSendPath(_Base):
    """flush / retry 走 `_do_send_direct`，必须与 worker 路径受**同样的**熔断/限流约束。

    原先这条路径既不判熔断、不记录熔断结果、也不限流 —— 整层韧性保护等于被绕过。
    """

    def _direct(self, trace, ch):
        sender_engine.create_channel = lambda t, c: ch
        self._mkmsg(trace, "SENDING")
        matched = [{"channel_id": 1, "template_id": 1}]
        return sender_engine._do_send_direct(trace, 1, {"title": "T"}, matched)

    def test_direct_path_respects_breaker(self, monkeypatch):
        monkeypatch.setattr(circuit_breaker, "CONSECUTIVE_THRESHOLD", 2)
        for _ in range(2):
            self.breaker.record(1, False, "HTTP 500")
        assert self.breaker.should_allow(1)[0] is False

        ch = ScriptedChannel([(True, "")])
        ok, _ = self._direct("dr1", ch)

        assert ch.calls == 0, "熔断期间不应真的发起发送"
        assert ok is False
        assert db.get_message("dr1")["status"] == "SENDING", \
            "全部被拦下时不应改写消息终态（留给后续重试）"

    def test_direct_path_respects_rate_limit(self):
        self.limiter.set_rate(1, 1)      # 每分钟 1 条
        ch = ScriptedChannel([(True, "")])
        ok1, _ = self._direct("dr2", ch)
        assert ok1 is True and ch.calls == 1

        ok2, _ = self._direct("dr3", ch)
        assert ch.calls == 1, "限流期间不应再发"
        assert ok2 is False

    def test_direct_path_records_failures_into_breaker(self, monkeypatch):
        """直接路径的失败也要累计到熔断器（原先完全不记录）。"""
        monkeypatch.setattr(circuit_breaker, "CONSECUTIVE_THRESHOLD", 3)
        ch = ScriptedChannel([(False, "HTTP 503")])
        for i in range(3):
            self._direct("df%d" % i, ch)
        assert self.breaker.should_allow(1)[0] is False, \
            "直接路径的连续失败应触发熔断"

    def test_direct_path_success_still_works(self):
        ch = ScriptedChannel([(True, "")])
        ok, _ = self._direct("ds1", ch)
        assert ok is True and ch.calls == 1
        assert db.get_message("ds1")["status"] == "SUCCESS"


class TestSustainedLoad(_Base):
    """持续负载：消息守恒（不丢、不重），终态可对账。"""

    def _drain(self, ch, rounds=8):
        """反复跑 worker，并把待重试任务的时间提前，避免 test 等真实退避。"""
        for _ in range(rounds):
            db._conn().execute("UPDATE message_queue SET next_retry_at=datetime('now')")
            db._conn().commit()
            moved = False
            while True:
                r = _run_one(ch)
                if r is None:
                    break
                moved = True
            pending = db._conn().execute(
                "SELECT COUNT(*) FROM message_queue").fetchone()[0]
            if not moved and pending == 0:
                break

    def test_all_success_conservation(self):
        n = 40
        for i in range(n):
            t = "ok%d" % i
            self._mkmsg(t)
            _enqueue(t, max_retries=3)

        ch = ScriptedChannel([(True, "")])
        self._drain(ch)

        conn = db._conn()
        assert conn.execute("SELECT COUNT(*) FROM message_queue").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM dead_letter_queue").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM message_log WHERE status='SUCCESS'").fetchone()[0] == n

    def test_permanently_broken_channel_defers_instead_of_dlq(self):
        """通道持续故障时，**保护性行为**是熔断 + 消息留在队列，而不是全进死信。

        这正是 v1.3.0 熔断的意义：故障期内不把重试次数烧掉、不丢消息。
        """
        n = 25
        for i in range(n):
            t = "bad%d" % i
            self._mkmsg(t)
            _enqueue(t, max_retries=2)

        ch = ScriptedChannel([(False, "HTTP 503")])
        self._drain(ch)

        conn = db._conn()
        dlq = conn.execute("SELECT COUNT(*) FROM dead_letter_queue").fetchone()[0]
        left = conn.execute("SELECT COUNT(*) FROM message_queue").fetchone()[0]
        pending_ok = conn.execute(
            "SELECT COUNT(*) FROM message_queue WHERE retry_count=0").fetchone()[0]

        assert self.breaker.should_allow(1)[0] is False, "持续 5xx 应触发熔断"
        assert dlq == 0, "熔断后不应再把消息推进死信队列（实际 %d）" % dlq
        assert left == n, "消息应全部保留在队列里等待恢复，实际 %d/%d" % (left, n)
        # 只有触发熔断的那前 N 条（N=CONSECUTIVE_THRESHOLD）各消耗 1 次重试，
        # 之后被熔断拦下的都不再消耗重试次数
        assert pending_ok >= n - circuit_breaker.CONSECUTIVE_THRESHOLD, (
            "除触发熔断的头 %d 条外，其余消息不应消耗重试次数（retry_count=0 的仅 %d 条）"
            % (circuit_breaker.CONSECUTIVE_THRESHOLD, pending_ok))

    def test_all_failure_lands_in_dlq_when_breaker_disabled(self, monkeypatch):
        """关掉熔断后，重试耗尽应全部进死信队列（验证 DLQ 路径本身完好）。"""
        monkeypatch.setattr(circuit_breaker, "CONSECUTIVE_THRESHOLD", 10 ** 9)
        monkeypatch.setattr(circuit_breaker, "MIN_SAMPLES", 10 ** 9)

        n = 25
        for i in range(n):
            t = "dlq%d" % i
            self._mkmsg(t)
            _enqueue(t, max_retries=2)

        ch = ScriptedChannel([(False, "HTTP 503")])
        self._drain(ch)

        conn = db._conn()
        left = conn.execute("SELECT COUNT(*) FROM message_queue").fetchone()[0]
        dlq = conn.execute("SELECT COUNT(*) FROM dead_letter_queue").fetchone()[0]
        failed = conn.execute(
            "SELECT COUNT(*) FROM message_log WHERE status='FAILED'").fetchone()[0]

        assert left == 0, "重试耗尽后队列应清空"
        assert dlq == n, "失败消息应全部进死信队列，实际 %d/%d" % (dlq, n)
        assert failed == n, "所有消息都应标记 FAILED"

    def test_no_message_lost_across_mixed_outcomes(self):
        """守恒校验：成功 + 死信 + 队列剩余 == 投入总数。"""
        n = 30
        for i in range(n):
            t = "mix%d" % i
            self._mkmsg(t)
            _enqueue(t, max_retries=1)

        ch = ScriptedChannel([(True, "")])   # 全部成功
        self._drain(ch)

        conn = db._conn()
        success = conn.execute(
            "SELECT COUNT(*) FROM message_log WHERE status='SUCCESS'").fetchone()[0]
        dlq = conn.execute("SELECT COUNT(*) FROM dead_letter_queue").fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM message_queue").fetchone()[0]
        assert success + dlq + pending == n, (
            "消息守恒被破坏：success=%d dlq=%d pending=%d 总数=%d"
            % (success, dlq, pending, n)
        )


class TestConcurrentConsumption(_Base):
    def test_many_workers_no_duplicate_or_loss(self):
        """4 个 worker 并发消费 40 条任务：每条恰好成功一次。"""
        q = get_backend()
        n = 40
        for i in range(n):
            self._mkmsg("k%d" % i)
            _enqueue("k%d" % i)

        ok_channel = ScriptedChannel([(True, "")])
        sent = []
        lock = threading.Lock()

        def worker():
            while True:
                item = q.dequeue()
                if item is None:
                    return
                ok, result = sender_engine.process_queue_item(item)
                if ok:
                    q.ack(item["id"])
                    with lock:
                        sent.append(item["trace_id"])
                    result.setdefault("channel_id", item["channel_id"])
                    sender_engine.update_message_results(item["trace_id"], result)
                else:
                    q.nack(item["id"], "?")

        # 把假通道装给所有线程
        sender_engine.create_channel = lambda t, c: ok_channel

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(sent) == n, "应恰好发送 %d 条，实际 %d（有重复或丢失）" % (n, len(sent))
        assert len(set(sent)) == n, "不应有重复发送"
        assert db._conn().execute(
            "SELECT COUNT(*) FROM message_queue").fetchone()[0] == 0, "队列应清空"

    @classmethod
    def teardown_class(cls):
        shutil.rmtree(_test_db_dir, ignore_errors=True)

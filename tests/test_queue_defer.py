# tests/test_queue_defer.py
"""延迟重排语义：熔断/限流期间放回队列，不消耗重试次数；超上限才走重试/DLQ。"""
import sys
import os
import tempfile
import shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_test_db_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_test_db_dir, "test_defer.db")

import db
from queue_backend import SQLiteQueueBackend


class TestQueueDefer:
    @classmethod
    def setup_class(cls):
        db.init_db()

    def setup_method(self):
        conn = db._conn()
        conn.execute("DELETE FROM message_queue")
        conn.execute("DELETE FROM dead_letter_queue")
        conn.commit()
        self.q = SQLiteQueueBackend()

    def _enqueue(self, trace="t1"):
        self.q.enqueue(trace_id=trace, source_id=1, msg_json="{}",
                       channel_id=1, template_id=1)
        conn = db._conn()
        return conn.execute(
            "SELECT id FROM message_queue ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]

    def test_defer_does_not_consume_retry(self):
        qid = self._enqueue()
        self.q.dequeue()
        self.q.defer(qid, delay_seconds=0)

        conn = db._conn()
        row = conn.execute(
            "SELECT status, retry_count, defer_count FROM message_queue WHERE id=?", (qid,)
        ).fetchone()
        assert row["status"] == "pending"
        assert row["retry_count"] == 0, "延迟重排不应消耗重试次数"
        assert row["defer_count"] == 1

    def test_deferred_item_can_be_picked_again(self):
        qid = self._enqueue()
        item = self.q.dequeue()
        self.q.defer(item["id"], delay_seconds=0)

        again = self.q.dequeue()
        assert again is not None and again["id"] == qid, "延迟后应能再次被取出"

    def test_exceeding_max_defers_goes_to_dlq_without_counting_a_retry(self):
        """超限后应**直进死信**，且不动重试次数。

        它从来没被真正发送过，不是"发送失败"——走 nack() 会把两者混为一谈
        （多计一次重试、错误信息也误导排查）。
        """
        qid = self._enqueue()
        for _ in range(3):                      # max_defers=2 → 第 3 次超限
            self.q.dequeue()
            self.q.defer(qid, delay_seconds=0, max_defers=2)

        conn = db._conn()
        assert conn.execute(
            "SELECT COUNT(*) FROM message_queue WHERE id=?", (qid,)).fetchone()[0] == 0, \
            "超限后应离开队列"
        dlq = conn.execute(
            "SELECT error, retry_count FROM dead_letter_queue WHERE trace_id='t1'"
        ).fetchone()
        assert dlq is not None, "应进入死信队列"
        assert "deferred" in dlq["error"]
        assert "without ever being sent" in dlq["error"]
        assert "failed" not in dlq["error"].lower(), "不应被描述成发送失败"
        assert dlq["retry_count"] == 0, "从未发送过，不该消耗重试次数"

    def test_retry_due_time_uses_utc_not_local(self):
        """回归测试：next_retry_at 必须以 UTC 为基准。

        原先 nack() 用 Python 的 datetime.now()（本地时间）写入，
        而 dequeue() 比较的是 SQLite 的 datetime('now')（UTC），
        在 UTC+8 时区下会把重试推迟约 8 小时。
        """
        qid = self._enqueue()
        self.q.dequeue()
        # 延迟 0 秒 → 立即到期，应能被立刻取出
        self.q.defer(qid, delay_seconds=0)
        assert self.q.dequeue() is not None, "延迟 0 秒的任务应立即可取（时区基准错误会失败）"

        # nack 路径同理：退避 5 秒，不应变成 8 小时后
        conn = db._conn()
        conn.execute("UPDATE message_queue SET status='processing' WHERE id=?", (qid,))
        conn.commit()
        self.q.nack(qid, "boom")
        row = conn.execute(
            "SELECT next_retry_at, datetime('now') AS utc_now FROM message_queue WHERE id=?",
            (qid,)
        ).fetchone()
        assert row["next_retry_at"] > row["utc_now"], "退避时间应晚于当前 UTC 时间"
        assert row["next_retry_at"] < "2099", "退避时间不应是明显的异常未来值"

    def test_defer_unknown_id_is_noop(self):
        self.q.defer(999999, delay_seconds=1)   # 不应抛异常

    @classmethod
    def teardown_class(cls):
        shutil.rmtree(_test_db_dir, ignore_errors=True)

# tests/test_queue_backend.py
"""消息队列后端测试。"""
import sys
import os
import tempfile
import shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# 使用临时数据库
_test_db_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_test_db_dir, "test_ego.db")

import db
from queue_backend import SQLiteQueueBackend, get_backend


class TestQueueBackend:
    """SQLite 队列后端测试。"""

    @classmethod
    def setup_class(cls):
        """初始化测试数据库。"""
        db.init_db()

    @classmethod
    def teardown_class(cls):
        """清理临时目录。"""
        shutil.rmtree(_test_db_dir, ignore_errors=True)

    def setup_method(self):
        """每个测试前清空队列。"""
        conn = db._conn()
        conn.execute("DELETE FROM message_queue")
        conn.execute("DELETE FROM dead_letter_queue")
        conn.commit()
        self.queue = SQLiteQueueBackend()

    def test_enqueue_and_dequeue(self):
        """入队和出队基本流程。"""
        self.queue.enqueue(
            trace_id="test-001",
            source_id=1,
            msg_json='{"title": "test"}',
            channel_id=1,
            template_id=1
        )
        item = self.queue.dequeue()
        assert item is not None
        assert item["trace_id"] == "test-001"
        assert item["status"] == "processing"

    def test_dequeue_empty_returns_none(self):
        """空队列返回 None。"""
        item = self.queue.dequeue()
        assert item is None

    def test_ack_removes_item(self):
        """ack 后任务从队列移除。"""
        self.queue.enqueue("test-002", 1, '{}', 1, 1)
        item = self.queue.dequeue()
        self.queue.ack(item["id"])
        # 再次 dequeue 应该为空
        assert self.queue.dequeue() is None

    def test_nack_schedules_retry(self):
        """nack 后任务进入重试状态。"""
        self.queue.enqueue("test-003", 1, '{}', 1, 1, max_retries=3)
        item = self.queue.dequeue()
        self.queue.nack(item["id"], "Test error")
        # 任务应该还在队列中（pending 状态，等待重试）
        stats = self.queue.get_stats()
        assert stats["pending"] == 1

    def test_nack_moves_to_dlq_after_max_retries(self):
        """超过最大重试次数后移入死信队列。"""
        self.queue.enqueue("test-004", 1, '{}', 1, 1, max_retries=1)
        item = self.queue.dequeue()
        self.queue.nack(item["id"], "Fatal error")
        # 任务应该进入 DLQ
        stats = self.queue.get_stats()
        assert stats["dlq"] == 1
        assert stats["pending"] == 0

    def test_get_stats(self):
        """统计信息正确。"""
        self.queue.enqueue("test-005", 1, '{}', 1, 1)
        self.queue.enqueue("test-006", 1, '{}', 1, 1)
        stats = self.queue.get_stats()
        assert stats["pending"] == 2
        assert stats["processing"] == 0
        assert stats["dlq"] == 0

    def test_get_dlq_items(self):
        """获取死信队列列表。"""
        self.queue.enqueue("test-007", 1, '{}', 1, 1, max_retries=1)
        item = self.queue.dequeue()
        self.queue.nack(item["id"], "Error")
        dlq_items = self.queue.get_dlq_items()
        assert len(dlq_items) == 1
        assert dlq_items[0]["trace_id"] == "test-007"

    def test_retry_dlq(self):
        """从死信队列重新入队。"""
        self.queue.enqueue("test-008", 1, '{}', 1, 1, max_retries=1)
        item = self.queue.dequeue()
        self.queue.nack(item["id"], "Error")
        dlq_items = self.queue.get_dlq_items()
        # 重新入队
        ok = self.queue.retry_dlq(dlq_items[0]["id"])
        assert ok is True
        # DLQ 应该为空，队列应该有 1 个 pending
        assert self.queue.get_stats()["dlq"] == 0
        assert self.queue.get_stats()["pending"] == 1

    def test_delete_dlq(self):
        """删除死信队列记录。"""
        self.queue.enqueue("test-009", 1, '{}', 1, 1, max_retries=1)
        item = self.queue.dequeue()
        self.queue.nack(item["id"], "Error")
        dlq_items = self.queue.get_dlq_items()
        self.queue.delete_dlq(dlq_items[0]["id"])
        assert self.queue.get_stats()["dlq"] == 0

    def test_recover_processing(self):
        """恢复卡在 processing 状态的任务。"""
        self.queue.enqueue("test-010", 1, '{}', 1, 1)
        item = self.queue.dequeue()  # 状态变为 processing
        # 模拟崩溃后恢复
        self.queue.recover_processing()
        stats = self.queue.get_stats()
        assert stats["pending"] == 1
        assert stats["processing"] == 0

    def test_fifo_order(self):
        """先进先出顺序。"""
        self.queue.enqueue("first", 1, '{}', 1, 1)
        self.queue.enqueue("second", 1, '{}', 1, 1)
        item1 = self.queue.dequeue()
        item2 = self.queue.dequeue()
        assert item1["trace_id"] == "first"
        assert item2["trace_id"] == "second"


class TestGetBackendSingleton:
    """get_backend 单例测试。"""

    def test_returns_same_instance(self):
        """多次调用返回同一实例。"""
        b1 = get_backend()
        b2 = get_backend()
        assert b1 is b2

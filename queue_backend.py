#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
消息队列后端。
默认使用 SQLite，可扩展为 Redis。
"""

import json
import datetime
import threading
import log
from db.connection import _conn

# ── 重试间隔（指数退避） ──
RETRY_DELAYS = [5, 30, 120]  # 秒


class SQLiteQueueBackend:
    """SQLite 队列实现。单写者友好，支持多 worker（通过行级锁）。"""

    def __init__(self):
        self._lock = threading.Lock()

    def enqueue(self, trace_id, source_id, msg_json, channel_id, template_id,
                dedup_key="", max_retries=3):
        """入队一条发送任务。"""
        with self._lock:
            conn = _conn()
            conn.execute(
                """INSERT INTO message_queue
                   (trace_id, source_id, msg_json, channel_id, template_id,
                    dedup_key, max_retries)
                   VALUES (?,?,?,?,?,?,?)""",
                (trace_id, source_id,
                 msg_json if isinstance(msg_json, str) else json.dumps(msg_json, ensure_ascii=False),
                 channel_id, template_id, dedup_key, max_retries)
            )
            conn.commit()

    def dequeue(self):
        """取出一条待处理任务（原子操作，行级锁）。"""
        with self._lock:
            conn = _conn()
            row = conn.execute(
                """SELECT * FROM message_queue
                   WHERE status='pending' AND next_retry_at <= datetime('now')
                   ORDER BY created_at LIMIT 1"""
            ).fetchone()
            if not row:
                return None
            conn.execute(
                "UPDATE message_queue SET status='processing' WHERE id=?",
                (row["id"],)
            )
            conn.commit()
            item = dict(row)
            item["status"] = "processing"
            return item

    def ack(self, queue_id):
        """标记任务成功，从队列移除。"""
        with self._lock:
            conn = _conn()
            conn.execute("DELETE FROM message_queue WHERE id=?", (queue_id,))
            conn.commit()

    def nack(self, queue_id, error=""):
        """标记任务失败，安排重试或移入死信队列。"""
        with self._lock:
            conn = _conn()
            row = conn.execute(
                "SELECT retry_count, max_retries, trace_id, source_id, msg_json, "
                "channel_id, template_id, dedup_key FROM message_queue WHERE id=?",
                (queue_id,)
            ).fetchone()
            if not row:
                return

            retry_count = row["retry_count"] + 1
            max_retries = row["max_retries"]

            if retry_count >= max_retries:
                # 移入死信队列
                conn.execute(
                    """INSERT INTO dead_letter_queue
                       (trace_id, source_id, msg_json, channel_id, template_id,
                        dedup_key, error, retry_count)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (row["trace_id"], row["source_id"], row["msg_json"],
                     row["channel_id"], row["template_id"], row["dedup_key"],
                     str(error)[:1000], retry_count)
                )
                conn.execute("DELETE FROM message_queue WHERE id=?", (queue_id,))
                log.logger.warning(
                    f"[{row['trace_id']}] Moved to DLQ after {retry_count} retries: {error}"
                )
            else:
                # 计算下次重试时间（指数退避）
                delay = RETRY_DELAYS[min(retry_count - 1, len(RETRY_DELAYS) - 1)]
                next_retry = (
                    datetime.datetime.now() + datetime.timedelta(seconds=delay)
                ).strftime("%Y-%m-%d %H:%M:%S")

                conn.execute(
                    """UPDATE message_queue
                       SET status='pending', retry_count=?, next_retry_at=?,
                           last_error=?
                       WHERE id=?""",
                    (retry_count, next_retry, str(error)[:500], queue_id)
                )
                log.logger.info(
                    f"[{row['trace_id']}] Retry {retry_count}/{max_retries} "
                    f"in {delay}s: {error}"
                )

            conn.commit()

    def get_stats(self):
        """返回队列统计信息。"""
        conn = _conn()
        pending = conn.execute(
            "SELECT COUNT(*) FROM message_queue WHERE status='pending'"
        ).fetchone()[0]
        processing = conn.execute(
            "SELECT COUNT(*) FROM message_queue WHERE status='processing'"
        ).fetchone()[0]
        dlq = conn.execute(
            "SELECT COUNT(*) FROM dead_letter_queue"
        ).fetchone()[0]
        return {"pending": pending, "processing": processing, "dlq": dlq}

    def get_dlq_items(self, limit=50):
        """获取死信队列列表。"""
        conn = _conn()
        rows = conn.execute(
            "SELECT * FROM dead_letter_queue ORDER BY moved_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def retry_dlq(self, dlq_id):
        """将死信队列中的任务重新入队。"""
        with self._lock:
            conn = _conn()
            row = conn.execute(
                "SELECT * FROM dead_letter_queue WHERE id=?", (dlq_id,)
            ).fetchone()
            if not row:
                return False
            conn.execute(
                """INSERT INTO message_queue
                   (trace_id, source_id, msg_json, channel_id, template_id,
                    dedup_key, max_retries)
                   VALUES (?,?,?,?,?,?,?)""",
                (row["trace_id"], row["source_id"], row["msg_json"],
                 row["channel_id"], row["template_id"], row["dedup_key"], 3)
            )
            conn.execute("DELETE FROM dead_letter_queue WHERE id=?", (dlq_id,))
            conn.commit()
            return True

    def delete_dlq(self, dlq_id):
        """删除死信队列中的一条记录。"""
        with self._lock:
            conn = _conn()
            conn.execute("DELETE FROM dead_letter_queue WHERE id=?", (dlq_id,))
            conn.commit()

    def recover_processing(self):
        """启动时恢复卡在 processing 状态的任务（进程崩溃遗留）。"""
        with self._lock:
            conn = _conn()
            count = conn.execute(
                "UPDATE message_queue SET status='pending' WHERE status='processing'"
            ).rowcount
            conn.commit()
            if count > 0:
                log.logger.info(f"Recovered {count} stuck queue items")


# ── 单例 ──
_backend = None


def get_backend():
    """获取队列后端单例。"""
    global _backend
    if _backend is None:
        _backend = SQLiteQueueBackend()
    return _backend

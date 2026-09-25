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
                # 注意：必须用 SQLite 的 datetime('now') 做基准——它返回 UTC，
                # 与 dequeue() 的比较条件一致。原先用 Python 的 datetime.now()
                # （本地时间）写入，在 UTC+8 等时区下会让重试被推迟约 8 小时。
                delay = RETRY_DELAYS[min(retry_count - 1, len(RETRY_DELAYS) - 1)]

                conn.execute(
                    """UPDATE message_queue
                       SET status='pending', retry_count=?,
                           next_retry_at=datetime('now', ?),
                           last_error=?
                       WHERE id=?""",
                    (retry_count, f"+{int(delay)} seconds", str(error)[:500], queue_id)
                )
                log.logger.info(
                    f"[{row['trace_id']}] Retry {retry_count}/{max_retries} "
                    f"in {delay}s: {error}"
                )

            conn.commit()

    def defer(self, queue_id, delay_seconds=5, max_defers=50):
        """延迟重排：放回队列但**不消耗重试次数**。

        用于熔断（circuit open）与限流（rate limited）期间的等待——
        这类「没轮到我发」不应算作发送失败，否则消息会在故障期内被耗尽重试次数，
        直接跌进死信队列。

        超过 max_defers 次仍未发出时**直接移入死信队列**，并写明原因是
        「一直没轮到发送」而不是「发送失败」：它从来没被真正发出去，
        走 nack() 会把二者混为一谈（多计一次重试、错误信息也误导排查）。
        """
        with self._lock:
            conn = _conn()
            row = conn.execute(
                "SELECT defer_count FROM message_queue WHERE id=?", (queue_id,)
            ).fetchone()
            if not row:
                return
            dc = (row["defer_count"] or 0) + 1
            if dc <= max_defers:
                # 基准同样用 SQLite 的 datetime('now')（UTC），与 dequeue() 比较条件一致
                conn.execute(
                    "UPDATE message_queue SET status='pending', defer_count=?, "
                    "next_retry_at=datetime('now', ?) WHERE id=?",
                    (dc, f"+{int(delay_seconds)} seconds", queue_id)
                )
                conn.commit()
                return

            conn.execute(
                """INSERT INTO dead_letter_queue
                   (trace_id, source_id, msg_json, channel_id, template_id,
                    dedup_key, error, retry_count)
                   SELECT trace_id, source_id, msg_json, channel_id, template_id,
                          dedup_key, ?, retry_count
                   FROM message_queue WHERE id=?""",
                (f"deferred {dc - 1} times without ever being sent "
                 f"(circuit open / rate limited for too long)", queue_id)
            )
            conn.execute("DELETE FROM message_queue WHERE id=?", (queue_id,))
            conn.commit()
            log.logger.warning(
                f"[defer] queue#{queue_id} deferred {dc - 1} times and never sent; "
                f"moved to DLQ (retry budget untouched)")

    def flush_processing_to_dlq(self, reason="shutdown"):
        """把仍处于 processing 的任务移入死信队列（优雅停机超时兜底）。

        正常情况下 `worker.stop_workers(timeout)` 会等在途任务跑完；
        超时兜底时把它们落到死信队列，避免消息卡在 processing 状态无人处理。
        """
        with self._lock:
            conn = _conn()
            rows = conn.execute(
                "SELECT * FROM message_queue WHERE status='processing'").fetchall()
            for row in rows:
                conn.execute(
                    """INSERT INTO dead_letter_queue
                       (trace_id, source_id, msg_json, channel_id, template_id,
                        dedup_key, error, retry_count)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (row["trace_id"], row["source_id"], row["msg_json"],
                     row["channel_id"], row["template_id"], row["dedup_key"],
                     str(reason)[:1000], row["retry_count"])
                )
                conn.execute("DELETE FROM message_queue WHERE id=?", (row["id"],))
            conn.commit()
            return len(rows)

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

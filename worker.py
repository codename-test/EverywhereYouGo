#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
Worker — 后台消息消费线程。
从 message_queue 取任务，调用 sender_engine 处理，支持重试和死信队列。
"""

import time
import threading
import log
import sender_engine
from queue_backend import get_backend

# ── 配置 ──
POLL_INTERVAL = 0.1  # 队列空时轮询间隔（秒）
WORKER_COUNT = 1     # worker 线程数（SQLite 建议 1，Redis 可多开）

_running = True


def _worker_loop(worker_id=0):
    """单个 worker 的主循环。"""
    queue = get_backend()
    log.logger.info(f"Worker-{worker_id} started")

    while _running:
        item = queue.dequeue()
        if item is None:
            time.sleep(POLL_INTERVAL)
            continue

        trace_id = item.get("trace_id", "?")
        ch_name = f"ch#{item.get('channel_id', '?')}"
        log.logger.debug(f"[Worker-{worker_id}] Processing {trace_id}/{ch_name}")

        try:
            ok, result = sender_engine.process_queue_item(item)

            if ok:
                queue.ack(item["id"])
            else:
                error_msg = result.get("error", "Unknown error") if result else "No result"
                queue.nack(item["id"], error_msg)

            # 更新 message_log 的 channel_results
            if result:
                sender_engine.update_message_results(trace_id, result)

        except Exception as e:
            log.logger.error(f"[Worker-{worker_id}] Unhandled error: {e}")
            try:
                queue.nack(item["id"], str(e)[:500])
            except Exception:
                pass


def start_workers(count=None):
    """启动 worker 线程。"""
    global _running
    _running = True

    n = count or WORKER_COUNT
    queue = get_backend()
    queue.recover_processing()  # 恢复崩溃遗留任务

    for i in range(n):
        t = threading.Thread(
            target=_worker_loop,
            args=(i,),
            daemon=True,
            name=f"worker-{i}"
        )
        t.start()

    log.logger.info(f"Started {n} worker thread(s)")


def stop_workers():
    """通知 worker 停止（等待当前任务完成）。"""
    global _running
    _running = False
    log.logger.info("Workers stopping...")

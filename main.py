#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
EverywhereYouGo (EGo) — 通用信息转发平台
主入口：初始化数据库、启动数据源监听、启动 WebUI。
"""

import os
import sys
import signal
import threading
import time
import logging

import log
import db
import source_manager
from source_manager import SourceManager
from web_ui import run_web_ui, app as web_app

VERSION = "1.0.0"
AUTHOR = "codename-test"
DESCRIPTION = "EverywhereYouGo (EGo) — 通用信息转发平台"

WELCOME = r"""
 ╔═══════════════════════════════════════════╗
 ║   EverywhereYouGo (EGo) 通用信息转发平台  ║
 ║           数据 → 解析 → 路由 → 推送       ║
 ╚═══════════════════════════════════════════╝
"""


def dnd_queue_checker():
    """后台线程：每 30s 检查勿扰状态，结束时自动刷新全部待发送队列。"""
    was_in_dnd = False
    while True:
        try:
            dnd = db.get_dnd()
            if dnd["enabled"]:
                in_dnd = source_manager._is_in_dnd(dnd["start_time"], dnd["end_time"])
                if was_in_dnd and not in_dnd:
                    log.logger.info("DND period ended. Flushing all pending queues...")
                    for s in db.get_sources():
                        if s["enabled"]:
                            try:
                                count = source_manager.flush_queue_for_source(s["id"])
                                if count > 0:
                                    log.logger.info(f"[Source {s['name']}] Flushed {count} queued messages.")
                            except Exception as e:
                                log.logger.error(f"[Source {s['name']}] Failed to flush queue: {e}")
                was_in_dnd = in_dnd
            else:
                if was_in_dnd:
                    log.logger.info("DND disabled manually. Flushing all pending queues...")
                    for s in db.get_sources():
                        if s["enabled"]:
                            try:
                                count = source_manager.flush_queue_for_source(s["id"])
                                if count > 0:
                                    log.logger.info(f"[Source {s['name']}] Flushed {count} queued messages.")
                            except Exception as e:
                                log.logger.error(f"[Source {s['name']}] Failed to flush queue: {e}")
                was_in_dnd = False
        except Exception as e:
            log.logger.error(f"DND checker error: {e}")
        time.sleep(30)


def init_ego():
    """初始化 EGo 核心（数据库、SourceManager、后台线程），返回 mgr。"""
    # 1. 初始化数据库
    log.logger.info("Initializing database...")
    db.init_db()

    # 1.5 加载配置（JSON → SQLite）
    import config_manager
    config_manager.load_all()

    # 2. 应用日志等级
    log_level = db.get_config("log_level", "INFO")
    if log_level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        log.logger.setLevel(getattr(logging, log_level))
        for h in log.logger.handlers:
            h.setLevel(getattr(logging, log_level))
    log.logger.info(f"Log level: {log_level}")

    # 3. 启动所有数据源
    mgr = SourceManager()
    mgr.start_all()

    # 注入 source_mgr 到 web_ui（供 API 调用）
    import web_ui
    web_ui.source_mgr = mgr

    # 4. 启动 DND 队列检查线程
    dnd_thread = threading.Thread(
        target=dnd_queue_checker, daemon=True, name="dnd-checker"
    )
    dnd_thread.start()
    log.logger.info("DND queue checker started.")

    # 5. 启动消息清理线程（每 10 分钟清理一次旧消息）
    def cleanup_loop():
        while True:
            time.sleep(600)
            try:
                db.cleanup_old_messages()
            except Exception as e:
                log.logger.error(f"Cleanup error: {e}")

    cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True, name="cleanup")
    cleanup_thread.start()
    log.logger.info("Message cleanup thread started.")

    # 6. 启动时检查：如果有 pending 队列消息且不在 DND 时段，立即刷新
    try:
        dnd = db.get_dnd()
        in_dnd = dnd["enabled"] and source_manager._is_in_dnd(dnd["start_time"], dnd["end_time"])
        if not in_dnd:
            for s in db.get_sources():
                if s["enabled"]:
                    try:
                        count = source_manager.flush_queue_for_source(s["id"])
                        if count > 0:
                            log.logger.info(f"[Source {s['name']}] Startup flush: {count} queued messages sent.")
                    except Exception as e:
                        log.logger.error(f"[Source {s['name']}] Startup flush error: {e}")
    except Exception as e:
        log.logger.error(f"Startup queue flush error: {e}")

    return mgr


def main():
    """启动入口：Flask 开发服务器。"""
    print(WELCOME)
    print(f"  Version: {VERSION}")
    print()

    mgr = init_ego()
    web_port = int(os.getenv("WEB_PORT", "5000"))
    log.logger.info(f"Web UI → http://0.0.0.0:{web_port}")

    print(f"\n  \033[1;36m➜  WebUI:  http://localhost:{web_port}\033[0m")
    print()

    # 信号处理
    def signal_handler(sig, frame):
        log.logger.info("Shutting down...")
        mgr.stop_all()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        run_web_ui(web_port)
    except KeyboardInterrupt:
        log.logger.info("Shutting down...")
        mgr.stop_all()


if __name__ == "__main__":
    main()

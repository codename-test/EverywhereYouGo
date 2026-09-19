#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
把日志写入 SQLite 的 logging.Handler。

为什么放在 db/ 而不是 log.py：
    保持依赖方向单向 —— main → db → log。
    原先 log.py 的 DBHandler 硬编码 `import db`，造成 log ↔ db 互相依赖；
    现在 log.py 只提供「挂载一个处理器」的接口，具体实现由调用方注入。

用法（见 main.py）：
    from db.log_handler import make_log_handler
    log.setup_db_logging(make_log_handler())
"""

import logging

from .queries import add_log


class DBLogHandler(logging.Handler):
    """把日志记录写入 logs 表。"""

    def emit(self, record):
        try:
            add_log(record.levelname, record.getMessage(), record.name)
        except Exception:
            # 日志落库失败绝不能反过来把业务打挂
            pass


def make_log_handler():
    """构造一个写库的日志处理器。"""
    return DBLogHandler()

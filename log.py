#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""日志模块

只负责控制台输出与「可注入的写库处理器挂载点」。

依赖方向：main → db → log（单向）。
本模块**不 import db**——写库处理器由调用方注入，见 `db/log_handler.py`。
"""

import logging
import colorlog
import os

log_colors_config = {
    'DEBUG':    'cyan',
    'INFO':     'green',
    'WARNING':  'yellow',
    'ERROR':    'red',
    'CRITICAL': 'red,bg_white',
}

logger = logging.getLogger('ego')

console_handler = logging.StreamHandler()

log_level = os.getenv('LOG_LEVEL', 'INFO')
level = getattr(logging, log_level, logging.INFO)
logger.setLevel(level)
console_handler.setLevel(level)

console_formatter = colorlog.ColoredFormatter(
    fmt='%(log_color)s[%(levelname)s] %(message)s',
    log_colors=log_colors_config
)
console_handler.setFormatter(console_formatter)
logger.addHandler(console_handler)


_db_handler = None


def setup_db_logging(handler):
    """挂载「写入数据库」的日志处理器（幂等）。

    handler 由调用方注入，例如：
        from db.log_handler import make_log_handler
        log.setup_db_logging(make_log_handler())
    """
    global _db_handler
    if _db_handler:
        return _db_handler
    handler.setLevel(logger.level)
    logger.addHandler(handler)
    _db_handler = handler
    return handler

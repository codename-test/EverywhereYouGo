#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""日志模块"""

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


class DBHandler(logging.Handler):
    """将日志写入数据库"""
    def emit(self, record):
        try:
            import db
            db.add_log(record.levelname, record.getMessage(), record.name)
        except Exception:
            pass


_db_handler = None


def setup_db_logging():
    global _db_handler
    if _db_handler:
        return
    _db_handler = DBHandler()
    _db_handler.setLevel(logger.level)
    logger.addHandler(_db_handler)

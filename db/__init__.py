#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
db 包 — 统一导出所有数据库接口。
现有代码 `import db` 无需修改即可使用。
"""

from .connection import DB_PATH, _conn
from .schema import init_db
from .queries import *

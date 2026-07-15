#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
配置管理：JSON 文件 ↔ SQLite 双向同步。
config/*.json 为磁盘上的唯一真相源，SQLite 为运行时缓存。
"""

import os
import json
import time
import log

CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")
LAST_SYNC_FILE = os.path.join(CONFIG_DIR, ".last_sync")

_CONFIG_FILES = {
    "parsers":   "parsers.json",
    "sources":   "sources.json",
    "channels":  "channels.json",
    "templates": "templates.json",
    "bindings":  "bindings.json",
}


def _timestamp() -> float:
    return time.time()


def _read_json(name: str):
    """读取 JSON 文件，不存在返回 None。"""
    path = os.path.join(CONFIG_DIR, name)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.logger.warning(f"Failed to read {path}: {e}")
        return None


def _write_json(name: str, data):
    """原子写入 JSON（tmp + rename）。"""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    path = os.path.join(CONFIG_DIR, name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _mark_synced():
    """记录当前同步时间戳。"""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    try:
        with open(LAST_SYNC_FILE, "w") as f:
            f.write(str(_timestamp()))
    except Exception as e:
        log.logger.warning(f"Failed to write .last_sync: {e}")


# ═══════════════════════════════════════════════
#  Export — SQLite → JSON（初始迁移）
# ═══════════════════════════════════════════════

def export_all():
    """从 SQLite 导出所有配置到 JSON。首次运行或手动使用。"""
    import db
    _write_json("parsers.json",  db.get_parsers())
    _write_json("sources.json",  db.get_sources())
    _write_json("channels.json", db.get_channels())
    _write_json("templates.json", db.get_templates())
    _write_json("bindings.json", db.get_all_source_channels())
    _mark_synced()
    log.logger.info("Config exported to JSON files")


# ═══════════════════════════════════════════════
#  Import — JSON → SQLite（启动时加载）
# ═══════════════════════════════════════════════

def _import_table(json_name: str, load_func, clear_func):
    """从 JSON 文件加载到 SQLite 表。"""
    data = _read_json(json_name)
    if not data:
        return False
    clear_func()
    for row in data:
        row = {k: v for k, v in row.items() if not k.startswith("_")}
        load_func(**row)
    return True


def load_all():
    """启动时从 JSON 加载所有配置到 SQLite（先清空再导入）。"""
    import db
    # 确保表已创建（跨线程可见）
    db.init_db()
    from db import (
        create_parser, delete_parser,
        create_source, delete_source,
        create_channel, delete_channel,
        create_template, delete_template,
        create_source_channel, delete_source_channel,
    )

    # 检查是否有 JSON 配置
    if not os.path.isfile(os.path.join(CONFIG_DIR, "parsers.json")):
        log.logger.info("No config files found, exporting from SQLite...")
        export_all()
        return

    log.logger.info("Loading config from JSON files...")

    # 按依赖顺序清空 + 重新导入
    # 1. parsers（最独立）
    for p in db.get_parsers(): delete_parser(p["id"])
    for row in _read_json("parsers.json") or []:
        create_parser(row["name"], row["filename"], row.get("description", ""))

    # 2. channels
    for c in db.get_channels(): delete_channel(c["id"])
    for row in _read_json("channels.json") or []:
        create_channel(row["name"], row["type"], row.get("config", "{}"), row.get("enabled", 1))

    # 3. templates
    for t in db.get_templates(): delete_template(t["id"])
    for row in _read_json("templates.json") or []:
        create_template(row["name"], row.get("engine", "jinja2"),
                        row.get("title_tpl", ""), row.get("content_tpl", ""))

    # 4. sources
    for s in db.get_sources(): delete_source(s["id"])
    for row in _read_json("sources.json") or []:
        create_source(row["name"], row["port"], row.get("parser_id"), row.get("enabled", 1))

    # 5. bindings（依赖 sources/channels/templates）
    for b in db.get_all_source_channels(): delete_source_channel(b["id"])
    for row in _read_json("bindings.json") or []:
        create_source_channel(row["source_id"], row["channel_id"], row["template_id"],
                              row.get("condition_expr", ""), row.get("priority", 0),
                              row.get("enabled", 1), row.get("urgent", 0))

    _mark_synced()
    log.logger.info("Config loaded from JSON files")


# ═══════════════════════════════════════════════
#  Sync — UI 编辑后同步到 JSON
# ═══════════════════════════════════════════════

def sync_table(table_type: str):
    """UI 编辑后，将某张表的全部数据同步回 JSON。"""
    import db
    conn = db._conn()
    
    try:
        tables = {
            "parsers":   ("parsers.json",  "SELECT * FROM parsers"),
            "sources":   ("sources.json",  "SELECT * FROM sources"),
            "channels":  ("channels.json", "SELECT * FROM channels"),
            "templates": ("templates.json","SELECT * FROM templates"),
            "bindings":  ("bindings.json", "SELECT * FROM source_channels"),
        }
        if table_type not in tables:
            log.logger.warning(f"Unknown config table: {table_type}")
            return
        filename, query = tables[table_type]
        rows = [dict(r) for r in conn.execute(query).fetchall()]
        # bindings 过滤无效引用
        if table_type == "bindings":
            valid_sources = {r["id"] for r in conn.execute("SELECT id FROM sources").fetchall()}
            valid_channels = {r["id"] for r in conn.execute("SELECT id FROM channels").fetchall()}
            valid_templates = {r["id"] for r in conn.execute("SELECT id FROM templates").fetchall()}
            rows = [r for r in rows if r["source_id"] in valid_sources and r["channel_id"] in valid_channels and r["template_id"] in valid_templates]
        _write_json(filename, rows)
        _mark_synced()
        log.logger.info(f"Synced {table_type} to JSON")
    except Exception as e:
        log.logger.error(f"Sync {table_type} failed: {e}")


# ═══════════════════════════════════════════════
#  External modification detection
# ═══════════════════════════════════════════════

def is_externally_modified() -> bool:
    """检查 JSON 文件是否被外部修改（文件 mtime > 最后同步时间）。"""
    if not os.path.isfile(LAST_SYNC_FILE):
        return False
    try:
        with open(LAST_SYNC_FILE) as f:
            last_sync = float(f.read().strip())
    except (ValueError, OSError):
        return False
    for filename in _CONFIG_FILES.values():
        path = os.path.join(CONFIG_DIR, filename)
        if os.path.isfile(path) and os.path.getmtime(path) > last_sync:
            return True
    return False


def get_modified_files() -> list:
    """返回被外部修改的文件列表。"""
    if not os.path.isfile(LAST_SYNC_FILE):
        return []
    try:
        with open(LAST_SYNC_FILE) as f:
            last_sync = float(f.read().strip())
    except (ValueError, OSError):
        return []
    modified = []
    for name, filename in _CONFIG_FILES.items():
        path = os.path.join(CONFIG_DIR, filename)
        if os.path.isfile(path) and os.path.getmtime(path) > last_sync:
            modified.append(name)
    return modified

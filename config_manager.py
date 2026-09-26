#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
配置管理：JSON 文件 ↔ SQLite 双向同步。
config/*.json 为磁盘上的唯一真相源，SQLite 为运行时缓存。
"""

import os
import json
import time
import fcntl
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

# 配置 Schema：每类配置的必需字段
_SCHEMA = {
    "parsers":   {"required": ["id", "name", "filename"]},
    "sources":   {"required": ["id", "name"]},
    "channels":  {"required": ["id", "name", "type"]},
    "templates": {"required": ["id", "name"]},
    "bindings":  {"required": ["id", "source_id", "channel_id", "template_id"]},
}


def _validate_config(name: str, data) -> list:
    """校验配置数据，返回错误列表（空列表表示通过）。"""
    errors = []
    schema = _SCHEMA.get(name)
    if not schema:
        return errors
    if not isinstance(data, list):
        return [f"{name}: expected list, got {type(data).__name__}"]
    required = schema["required"]
    for i, row in enumerate(data):
        if not isinstance(row, dict):
            errors.append(f"{name}[{i}]: expected dict, got {type(row).__name__}")
            continue
        missing = [f for f in required if f not in row]
        if missing:
            errors.append(f"{name}[{i}]: missing required fields: {', '.join(missing)}")
    return errors


def _timestamp() -> float:
    return time.time()


def _read_json(name: str):
    """读取 JSON 文件，不存在返回 None。"""
    path = os.path.join(CONFIG_DIR, name)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_SH)  # 共享锁（读）
            try:
                return json.load(f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except Exception as e:
        log.logger.warning(f"Failed to read {path}: {e}")
        return None


def _write_json(name: str, data):
    """原子写入 JSON（tmp + rename），带排他文件锁防并发写。"""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    path = os.path.join(CONFIG_DIR, name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)  # 排他锁（写）
        try:
            json.dump(data, f, ensure_ascii=False, indent=2)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
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

# 导入规格：(schema_key, SQL 表名, JSON 文件名, INSERT 语句, 行 → 参数)
# schema_key 同时是 `_SCHEMA` 与 `_CONFIG_FILES` 的键。
# `created_at` 一并还原（备份里带则用备份值，缺失回落 CURRENT_TIMESTAMP）——
# 否则恢复一次备份，所有"创建时间"都会变成恢复时刻。
# （source_channels 表无 created_at 列，不涉及。）
_IMPORT_SPECS = (
    ("parsers", "parsers", "parsers.json",
     "INSERT INTO parsers (id, name, filename, description, created_at) "
     "VALUES (?,?,?,?, COALESCE(?, CURRENT_TIMESTAMP))",
     lambda r: (r["id"], r["name"], r["filename"], r.get("description", ""),
                r.get("created_at") or None)),

    ("channels", "channels", "channels.json",
     "INSERT INTO channels (id, name, type, config, enabled, created_at) "
     "VALUES (?,?,?,?,?, COALESCE(?, CURRENT_TIMESTAMP))",
     lambda r: (r["id"], r["name"], r["type"], r.get("config", "{}"),
                r.get("enabled", 1), r.get("created_at") or None)),

    ("templates", "templates", "templates.json",
     "INSERT INTO templates (id, name, engine, title_tpl, content_tpl, created_at) "
     "VALUES (?,?,?,?,?, COALESCE(?, CURRENT_TIMESTAMP))",
     lambda r: (r["id"], r["name"], r.get("engine", "jinja2"),
                r.get("title_tpl", ""), r.get("content_tpl", ""),
                r.get("created_at") or None)),

    ("sources", "sources", "sources.json",
     """INSERT INTO sources (id, name, slug, port, path, parent_id, parser_id, enabled, created_at)
        VALUES (?,?,?,?,?,?,?,?, COALESCE(?, CURRENT_TIMESTAMP))""",
     lambda r: (r["id"], r["name"], r.get("slug"), r.get("port"),
                r.get("path", ""), r.get("parent_id"), r.get("parser_id"),
                r.get("enabled", 1), r.get("created_at") or None)),

    ("bindings", "source_channels", "bindings.json",
     """INSERT INTO source_channels
        (id, source_id, channel_id, template_id, condition_expr,
         dedup_key_expr, dedup_window, priority, enabled, urgent)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
     lambda r: (r["id"], r["source_id"], r["channel_id"], r["template_id"],
                r.get("condition_expr", ""), r.get("dedup_key_expr", ""),
                r.get("dedup_window", 3600), r.get("priority", 0),
                r.get("enabled", 1), r.get("urgent", 0))),
)


def import_from_json(only=None):
    """从 JSON 文件导入 SQLite（支持部分恢复）。

    对每个要处理的 JSON：清空对应表 → 按 JSON 重建（保留 id / created_at）；
    跳过的表**原样保留**（不清空）。这样"缺文件的备份"是部分恢复，
    而不是把没带上的表静默清空。

    only: 只处理这些 schema_key（`parsers` / `sources` / `channels` /
    `templates` / `bindings`）。**备份恢复必须传它**——因为 config/ 目录里通常
    已经存在上一轮导出留下的旧文件，"按磁盘上有没有判断"会把陈旧内容当成
    备份内容导入。None = 按磁盘上是否存在决定（仅用于 load_all 的首次导入）。

    注意区分：
      - 要处理且文件存在、内容是 `[]` → 该表被**清空**（显式表达"我就要空表"）
      - 不处理 / 文件不存在          → 该表**原样保留**

    事务性：所有 DELETE / INSERT 在**单个事务**内完成，失败整体回滚，
    不会留下「表已清空但未导入」的半成品。

    返回 (counts, skipped)：counts = schema_key → 导入行数；
    skipped = 未处理（故原样保留）的 schema_key 列表。
    """
    import db
    db.init_db()
    conn = db._conn()

    counts = {}
    skipped = []
    try:
        for key, table, json_name, insert_sql, to_params in _IMPORT_SPECS:
            if only is not None and key not in only:
                skipped.append(key)
                log.logger.info("Config import: %s not in backup, table %s left unchanged",
                                json_name, table)
                continue
            data = _read_json(json_name)
            if data is None:
                # 备份里没有这个文件 → 该表不动（不删不建）
                skipped.append(key)
                log.logger.info("Config import: %s absent, table %s left unchanged",
                                json_name, table)
                continue
            errors = _validate_config(key, data)
            if errors:
                log.logger.warning(f"Config validation errors: {'; '.join(errors[:5])}")
            conn.execute("DELETE FROM %s" % table)
            for row in data:
                conn.execute(insert_sql, to_params(row))
            counts[key] = len(data)

        # 部分恢复时，绑定可能指向已被替换掉的 source / channel / template
        # → 清掉孤儿绑定（否则路由会持续匹配到不存在的对象而反复失败，
        #    与 db.delete_channel 里的级联清理同因）
        conn.execute("""DELETE FROM source_channels
                        WHERE source_id   NOT IN (SELECT id FROM sources)
                           OR channel_id  NOT IN (SELECT id FROM channels)
                           OR template_id NOT IN (SELECT id FROM templates)""")

        conn.commit()
    except Exception:
        conn.rollback()
        log.logger.error("Config import from JSON failed, rolled back (DB unchanged)")
        raise

    _mark_synced()
    log.logger.info("Config imported from JSON files: %s%s", counts,
                    (" (left unchanged: %s)" % skipped) if skipped else "")
    return counts, skipped


def load_all():
    """启动时加载配置。数据库为准，JSON 仅做导出备份。

    - 数据库已有配置数据 → 以数据库为准，刷新 JSON 备份后返回
    - 数据库为空且存在 JSON 配置 → 从 JSON 导入（首次迁移）
    - 数据库为空且无 JSON 配置 → 从数据库导出初始 JSON

    注意：**备份恢复（Restore）不走这里**，而是直接调 import_from_json()。
    恢复要求「JSON → DB」的无条件覆盖；而 load_all() 在 DB 非空时以 DB 为准、
    会把 JSON 反向刷回，从而覆盖掉刚恢复的配置（v1.3.2 review #8）。
    """
    import db
    # 确保表已创建（跨线程可见）
    db.init_db()

    conn = db._conn()

    # 数据库是否已有配置数据（任一配置表非空即视为已有数据）
    row_total = 0
    for _t in ("parsers", "sources", "channels", "templates", "source_channels"):
        try:
            row_total += conn.execute("SELECT COUNT(*) FROM %s" % _t).fetchone()[0]
        except Exception:
            pass

    has_json = os.path.isfile(os.path.join(CONFIG_DIR, "parsers.json"))

    if row_total > 0:
        # 数据库为准：不从 JSON 覆盖，仅刷新 JSON 备份
        log.logger.info("Config data found in DB, using database as source of truth; refreshing JSON backup...")
        export_all()
        return

    # 数据库为空：无 JSON 配置则导出初始配置
    if not has_json:
        log.logger.info("No config files found, exporting from SQLite...")
        export_all()
        return

    # 数据库为空 + 有 JSON：从 JSON 导入（首次迁移）
    log.logger.info("Empty database, loading config from JSON files...")
    import_from_json()


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

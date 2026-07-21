#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""api/system.py — 健康检查/设置/版本/队列"""

import os
import shutil
import db
import version_checker
from queue_backend import get_backend
from flask import Blueprint, request, jsonify

system_bp = Blueprint("system", __name__)

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")
DB_PATH = os.getenv("DB_PATH", "ego.db")


@system_bp.route("/api/health", methods=["GET"])
def api_health():
    """健康检查：SQLite 连接、磁盘空间、配置文件。"""
    checks = {}
    all_ok = True

    # 1. SQLite 连接
    try:
        db._conn().execute("SELECT 1")
        checks["sqlite"] = {"ok": True}
    except Exception as e:
        checks["sqlite"] = {"ok": False, "error": str(e)[:200]}
        all_ok = False

    # 2. 磁盘空间
    try:
        total, used, free = shutil.disk_usage(os.path.dirname(DB_PATH) or ".")
        free_mb = free // (1024 * 1024)
        checks["disk"] = {"ok": free_mb > 100, "free_mb": free_mb}
        if free_mb <= 100:
            all_ok = False
    except Exception as e:
        checks["disk"] = {"ok": False, "error": str(e)[:200]}
        all_ok = False

    # 3. 配置文件
    try:
        config_files = ["parsers.json", "sources.json", "channels.json", "templates.json", "bindings.json"]
        missing = [f for f in config_files if not os.path.isfile(os.path.join(CONFIG_DIR, f))]
        checks["config"] = {"ok": len(missing) == 0, "missing": missing}
        if missing:
            all_ok = False
    except Exception as e:
        checks["config"] = {"ok": False, "error": str(e)[:200]}
        all_ok = False

    # 4. 队列状态
    try:
        mq = get_backend().get_stats()
        checks["queue"] = {"ok": True, **mq}
    except Exception as e:
        checks["queue"] = {"ok": False, "error": str(e)[:200]}

    return jsonify({"status": "ok" if all_ok else "degraded", "checks": checks})


@system_bp.route("/api/settings", methods=["POST"])
def api_update_settings():
    data = request.json
    for k, v in data.items():
        if k == "log_level":
            db.set_log_level(v)
        else:
            db.set_config(k, v)
    return jsonify({"status": "ok"})


@system_bp.route("/api/version/check", methods=["GET"])
def api_version_check():
    """获取缓存的版本信息。"""
    return jsonify(version_checker.get_cache())


@system_bp.route("/api/version/check", methods=["POST"])
def api_version_check_now():
    """立即检查 GitHub 最新版本。"""
    has_update, info = version_checker.check_now()
    return jsonify(info)


# ── 队列统计 ──

@system_bp.route("/api/queue/stats", methods=["GET"])
def api_queue_stats():
    """获取消息队列 + 死信队列统计。"""
    mq = get_backend().get_stats()
    msg = db.get_queue_stats()
    return jsonify({**mq, **msg})


# ── 死信队列管理 ──

@system_bp.route("/api/dlq", methods=["GET"])
def api_dlq_list():
    """获取死信队列列表。"""
    limit = request.args.get("limit", 50, type=int)
    return jsonify(db.get_dlq_items(limit))


@system_bp.route("/api/dlq/<int:dlq_id>/retry", methods=["POST"])
def api_dlq_retry(dlq_id):
    """重新入队一条死信消息。"""
    ok = get_backend().retry_dlq(dlq_id)
    return jsonify({"status": "ok" if ok else "not_found"})


@system_bp.route("/api/dlq/<int:dlq_id>", methods=["DELETE"])
def api_dlq_delete(dlq_id):
    """删除一条死信记录。"""
    get_backend().delete_dlq(dlq_id)
    return jsonify({"status": "ok"})

#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""api/system.py — 健康检查/设置/版本/队列"""

import os
import re
import shutil
import db
import version_checker
from queue_backend import get_backend
from flask import Blueprint, request, jsonify
from api.validation import (
    ValidationError, optional_enum, optional_flag, optional_hhmm, optional_int,
    LOG_LEVELS,
)

system_bp = Blueprint("system", __name__)

_PREFIX_RE = re.compile(r"^[A-Za-z0-9_\-/]*$")

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
    data = request.json or {}
    if not isinstance(data, dict):
        raise ValidationError("request body must be a JSON object")

    # 已知键做类型/取值校验（improvement #27），避免把无效值写进 system_config
    if "log_level" in data:
        data["log_level"] = optional_enum(data, "log_level", LOG_LEVELS, default="INFO")
    for k in ("dnd_start", "dnd_end"):
        if k in data:
            optional_hhmm(data, k)                    # 值不合法会抛 400
    if "dnd_enabled" in data:
        data["dnd_enabled"] = str(optional_flag(data, "dnd_enabled", default=0))
    if "path_prefix" in data:
        p = str(data.get("path_prefix") or "").strip().strip("/")
        if len(p) > 64 or not _PREFIX_RE.match(p):
            raise ValidationError("path_prefix may only contain [A-Za-z0-9_-/] (max 64)")
        data["path_prefix"] = p

    # 熔断参数（留空 = 不覆盖，回退到环境变量/默认值）
    _BREAKER_INT_KEYS = (
        ("breaker_window", 1, 86400),
        ("breaker_min_samples", 1, 100000),
        ("breaker_consecutive", 1, 100000),
        ("breaker_open_base", 1, 86400),
        ("breaker_open_max", 1, 86400),
        ("breaker_half_open_ok", 1, 1000),
    )
    for key, lo, hi in _BREAKER_INT_KEYS:
        if key in data and str(data.get(key) or "").strip() != "":
            data[key] = str(optional_int(data, key, lo, hi))
    if "breaker_failure_ratio" in data:
        raw = str(data.get("breaker_failure_ratio") or "").strip()
        if raw != "":
            try:
                ratio = float(raw)
            except ValueError:
                raise ValidationError("breaker_failure_ratio must be a number in (0, 1]")
            if not (0 < ratio <= 1):
                raise ValidationError("breaker_failure_ratio must be in (0, 1]")
            data["breaker_failure_ratio"] = str(ratio)

    for k, v in data.items():
        if k == "log_level":
            db.set_log_level(v)
        else:
            db.set_config(k, v)

    # 熔断参数带 TTL 缓存，改完立刻失效，避免要等缓存过期才生效
    try:
        from circuit_breaker import invalidate_param_cache
        invalidate_param_cache()
    except Exception:
        pass
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


# ── 韧性：熔断 + 出站限流 ──

@system_bp.route("/api/metrics", methods=["GET"])
def api_metrics():
    """简单可观测性端点（improvement v1.3.0 可选项）。

    返回队列深度 / 死信总数 / 各通道成功率 / 端到端延迟 / 熔断与限流状态。
    定位是「curl 一查就有」，**不引入 Prometheus**。
    可用 `?hours=N` 调整统计窗口（默认 24h，上限 30 天）。
    """
    from circuit_breaker import get_breaker
    from rate_limiter import get_limiter
    try:
        from api.pages import VERSION as _ver
    except Exception:
        _ver = None

    hours = request.args.get("hours", None, type=int)
    if hours is None:
        hours = 24
    hours = max(1, min(int(hours), 24 * 30))

    return jsonify({
        "version": _ver,
        "window_hours": hours,
        "queue": get_backend().get_stats(),
        "messages": db.get_queue_stats(),
        "channels": db.get_channel_stats(hours),
        "latency": db.get_latency_stats(hours),
        "breaker": get_breaker().snapshot(),
        "rate_limits": get_limiter().snapshot(),
    })


@system_bp.route("/api/resilience", methods=["GET"])
def api_resilience():
    """通道熔断状态 + 出站限流配置。"""
    from circuit_breaker import get_breaker
    from rate_limiter import get_limiter
    return jsonify({
        "breaker": get_breaker().snapshot(),
        "rate_limits": get_limiter().snapshot(),
    })


@system_bp.route("/api/resilience/breaker/<int:channel_id>/reset", methods=["POST"])
def api_breaker_reset(channel_id):
    """手工恢复被熔断的通道。"""
    from circuit_breaker import get_breaker
    get_breaker().reset(channel_id)
    return jsonify({"status": "ok"})


@system_bp.route("/api/resilience/rate_limit/<int:channel_id>", methods=["POST"])
def api_rate_limit_set(channel_id):
    """设置通道出站限流（条/分钟），0 = 不限流。"""
    from rate_limiter import get_limiter
    data = request.get_json(silent=True) or {}
    raw = data.get("per_minute", 0)
    try:
        per_minute = int(raw)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "error": "per_minute must be an integer"}), 400
    if per_minute < 0:
        return jsonify({"status": "error", "error": "per_minute must be >= 0"}), 400
    get_limiter().set_rate(channel_id, per_minute)
    return jsonify({"status": "ok", "channel_id": channel_id, "per_minute": per_minute})

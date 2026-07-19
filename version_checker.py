#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
版本检查模块。
启动时及定期从 GitHub 获取最新版本信息，与本地版本对比。
"""

import json
import threading
import time
import urllib.request
import urllib.error
import log

# ── 配置 ──────────────────────────────────────
# GitHub raw 地址，发布新版本时更新此文件即可
GITHUB_VERSION_URL = "https://raw.githubusercontent.com/codename-test/EverywhereYouGo/main/version.json"
CHECK_INTERVAL = 86400  # 24 小时检查一次（秒）

# ── 缓存 ──────────────────────────────────────
_cache = {
    "latest_version": None,   # 最新版本号
    "release_date": None,     # 发布日期
    "changelog": [],          # 更新内容列表
    "url": None,              # 发布页链接
    "has_update": False,      # 是否有新版本
    "checked_at": None,       # 上次检查时间
    "error": None,            # 错误信息
}
_lock = threading.Lock()


def get_local_version():
    """获取本地版本号。"""
    from api.pages import VERSION
    return VERSION


def get_cache():
    """获取缓存的版本信息。"""
    with _lock:
        return dict(_cache)


def check_now():
    """
    立即检查 GitHub 上的最新版本。
    返回 (has_update: bool, info: dict)
    """
    local_ver = get_local_version()
    try:
        req = urllib.request.Request(
            GITHUB_VERSION_URL,
            headers={"User-Agent": "EGo-VersionChecker/1.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        remote_ver = data.get("version", "")
        has_update = _compare_versions(local_ver, remote_ver) < 0

        with _lock:
            _cache["latest_version"] = remote_ver
            _cache["release_date"] = data.get("release_date", "")
            _cache["changelog"] = data.get("changelog", [])
            _cache["url"] = data.get("url", "")
            _cache["has_update"] = has_update
            _cache["checked_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            _cache["error"] = None

        if has_update:
            log.logger.info(f"New version available: {remote_ver} (current: {local_ver})")
        else:
            log.logger.debug(f"Version up to date: {local_ver}")

        return has_update, _cache_to_dict()

    except urllib.error.URLError as e:
        log.logger.warning(f"Version check failed (network): {e}")
        with _lock:
            _cache["error"] = f"Network error: {str(e)[:100]}"
            _cache["checked_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        return False, _cache_to_dict()

    except Exception as e:
        log.logger.error(f"Version check error: {e}")
        with _lock:
            _cache["error"] = str(e)[:200]
            _cache["checked_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        return False, _cache_to_dict()


def _cache_to_dict():
    with _lock:
        return {
            "local_version": get_local_version(),
            "latest_version": _cache["latest_version"],
            "release_date": _cache["release_date"],
            "changelog": _cache["changelog"],
            "url": _cache["url"],
            "has_update": _cache["has_update"],
            "checked_at": _cache["checked_at"],
            "error": _cache["error"],
        }


def _compare_versions(v1, v2):
    """
    比较两个版本号字符串。
    返回: -1 (v1 < v2), 0 (v1 == v2), 1 (v1 > v2)
    """
    def parse(v):
        parts = []
        for p in v.split("."):
            try:
                parts.append(int(p))
            except ValueError:
                parts.append(0)
        while len(parts) < 3:
            parts.append(0)
        return parts

    p1, p2 = parse(v1), parse(v2)
    for a, b in zip(p1, p2):
        if a < b:
            return -1
        if a > b:
            return 1
    return 0


def start_checker_thread():
    """启动后台检查线程：启动时立即检查一次，之后每 24 小时检查一次。"""
    def _loop():
        # 启动后等 5 秒再检查，避免阻塞启动
        time.sleep(5)
        check_now()
        while True:
            time.sleep(CHECK_INTERVAL)
            check_now()

    t = threading.Thread(target=_loop, daemon=True, name="version-checker")
    t.start()
    log.logger.info("Version checker thread started.")

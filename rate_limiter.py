#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
出站通道限流 —— 每通道独立令牌桶。

规格来源：doc/improvement.md #21

背景：Retry 解决不了 API 限额——发送过快 → 429 → 重试 → 再次 429。
限流必须位于「Worker → Rate Limiter → Channel」，而不是靠重试兜底。
（Nginx 只能管入站，管不到出站。）

设计：
  - 速率按「条/分钟」配置，存 channel_rate_limit 表；0 或缺省 = 不限流
  - 桶容量 = 1 分钟额度，允许小幅突发
  - acquire() 最多等 max_wait 秒；拿不到令牌就返回 False，
    由调用方「延迟重排」（queue.defer）而不是长阻塞——
    否则单通道限流会把 worker 线程占住，拖累其它通道。
"""

import os
import time
import threading

import log
from db.connection import _conn

MAX_WAIT_SECONDS = float(os.getenv("EGO_RATE_MAX_WAIT", "1.0"))
_SLEEP_SLICE = 0.1
# 未配置限流的通道，多久回查一次数据库。
# 缓存只在 set_rate() 时更新，若有人直接改库（或另一个进程写入），
# 这个 TTL 让配置在 30s 内自愈，而不必重启服务。
_MISS_TTL = float(os.getenv("EGO_RATE_MISS_TTL", "30"))


class _Bucket:
    __slots__ = ("rate", "capacity", "tokens", "updated")

    def __init__(self, rate):
        self.rate = rate
        self.capacity = float(rate)     # 桶容量 = 1 分钟额度
        self.tokens = float(rate)
        self.updated = time.time()


class RateLimiter:
    def __init__(self):
        self._lock = threading.Lock()
        self._rates = {}        # channel_id -> per_minute
        self._buckets = {}
        self._checked_at = {}   # channel_id -> 上次回查数据库的时间
        self._loaded = False

    # ── 配置 ──
    def load(self):
        """从 SQLite 载入各通道速率。"""
        try:
            rows = _conn().execute(
                "SELECT channel_id, per_minute FROM channel_rate_limit"
            ).fetchall()
        except Exception as e:
            log.logger.warning(f"[RateLimit] load failed: {e}")
            self._loaded = True
            return
        with self._lock:
            self._rates = {r["channel_id"]: int(r["per_minute"] or 0) for r in rows}
            self._checked_at = {cid: time.time() for cid in self._rates}
            self._buckets.clear()
            self._loaded = True
        active = {k: v for k, v in self._rates.items() if v > 0}
        if active:
            log.logger.info(f"[RateLimit] Loaded limits: {active}")

    def get_rate(self, channel_id):
        """取通道速率（条/分钟）。未配置的通道按 TTL 回查数据库，避免缓存僵化。"""
        now = time.time()
        rate = self._rates.get(channel_id)
        if rate is None or (rate == 0 and now - self._checked_at.get(channel_id, 0) > _MISS_TTL):
            try:
                row = _conn().execute(
                    "SELECT per_minute FROM channel_rate_limit WHERE channel_id=?",
                    (channel_id,)
                ).fetchone()
                rate = int(row["per_minute"] or 0) if row else 0
            except Exception:
                rate = 0
            self._rates[channel_id] = rate
            self._checked_at[channel_id] = now
        return rate

    def set_rate(self, channel_id, per_minute):
        """设置通道速率（条/分钟），0 = 不限流。"""
        per_minute = max(0, int(per_minute))
        conn = _conn()
        conn.execute(
            """INSERT INTO channel_rate_limit (channel_id, per_minute, updated_at)
               VALUES (?,?,datetime('now'))
               ON CONFLICT(channel_id) DO UPDATE SET
                   per_minute=excluded.per_minute, updated_at=excluded.updated_at""",
            (channel_id, per_minute)
        )
        conn.commit()
        with self._lock:
            self._rates[channel_id] = per_minute
            self._checked_at[channel_id] = time.time()
            self._buckets.pop(channel_id, None)
        log.logger.info(f"[RateLimit] Channel {channel_id} limit set to {per_minute}/min")

    # ── 取令牌 ──
    def _bucket(self, channel_id, rate):
        b = self._buckets.get(channel_id)
        if b is None or b.rate != rate:
            b = _Bucket(rate)
            self._buckets[channel_id] = b
        return b

    def acquire(self, channel_id, max_wait=None):
        """尝试取一个令牌。返回 True/False（False = 调用方应延迟重排）。"""
        rate = self.get_rate(channel_id)
        if rate <= 0:
            return True
        if max_wait is None:
            max_wait = MAX_WAIT_SECONDS

        per_sec = rate / 60.0
        deadline = time.time() + max_wait

        while True:
            with self._lock:
                b = self._bucket(channel_id, rate)
                now = time.time()
                b.tokens = min(b.capacity, b.tokens + (now - b.updated) * per_sec)
                b.updated = now
                if b.tokens >= 1.0:
                    b.tokens -= 1.0
                    return True
                need = (1.0 - b.tokens) / per_sec

            if time.time() + need > deadline:
                return False
            time.sleep(min(need, _SLEEP_SLICE))

    def snapshot(self):
        with self._lock:
            return {k: v for k, v in self._rates.items() if v > 0}


# ── 单例 ──
_limiter = None


def get_limiter():
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter

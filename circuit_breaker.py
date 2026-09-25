#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
通道熔断器 —— CLOSED / OPEN / HALF_OPEN 三态机。

规格来源：doc/improvement.md #19（v1.3.0 韧性增强）

    CLOSED ──连续失败 / 窗口失败率超阈值──▶ OPEN
       ▲                                    │ 冷却到期
       │ 连续 N 次探测成功                     ▼
       └──────────── HALF_OPEN ◀─────────────┘
                        │ 探测失败
                        └──▶ OPEN（冷却时间翻倍）

规则：
  - 滑动窗口（默认 60s）内失败率 > 50% 且样本数 >= MIN_SAMPLES → 熔断
  - 或连续失败 >= CONSECUTIVE_THRESHOLD → 熔断（照顾低频通道）
  - OPEN 冷却指数退避 30→60→120→240→480→600s（封顶 600s）
  - HALF_OPEN 放行探测，连续 HALF_OPEN_NEEDED(3) 次成功才恢复 CLOSED
  - **4xx 不计失败**，只对 5xx / 超时 / 连接类错误计数
  - 状态持久化到 SQLite(channel_breaker)，重启后恢复

线程安全：单把锁保护全部状态；仅在状态发生变化时落库（热路径不写库）。
"""

import os
import re
import time
import threading
import collections

import log
from db.connection import _conn

# ── 可调参数 ──
# 取值优先级：system_config（设置页/可直接改库） > 环境变量 > 这里的默认值。
# 环境变量在模块加载时读一次作为兜底；system_config 支持运行时调整（带 TTL 缓存），
# 这样用户不必为了改一个阈值重启容器，也不必依赖尚未完备的 API。
WINDOW_SECONDS = float(os.getenv("EGO_BREAKER_WINDOW", "60"))
MIN_SAMPLES = int(os.getenv("EGO_BREAKER_MIN_SAMPLES", "5"))
FAILURE_RATIO = float(os.getenv("EGO_BREAKER_FAILURE_RATIO", "0.5"))
CONSECUTIVE_THRESHOLD = int(os.getenv("EGO_BREAKER_CONSECUTIVE", "5"))
OPEN_BASE_SECONDS = float(os.getenv("EGO_BREAKER_OPEN_BASE", "30"))
OPEN_MAX_SECONDS = float(os.getenv("EGO_BREAKER_OPEN_MAX", "600"))
HALF_OPEN_NEEDED = int(os.getenv("EGO_BREAKER_HALF_OPEN_OK", "3"))

# system_config 里的键名（设置页用同一批键）
CONFIG_KEYS = {
    "window": "breaker_window",
    "min_samples": "breaker_min_samples",
    "failure_ratio": "breaker_failure_ratio",
    "consecutive": "breaker_consecutive",
    "open_base": "breaker_open_base",
    "open_max": "breaker_open_max",
    "half_open_ok": "breaker_half_open_ok",
}

_PARAM_TTL = float(os.getenv("EGO_BREAKER_PARAM_TTL", "30"))
_param_cache = {}
_param_cached_at = 0.0
_param_lock = threading.Lock()


def _read_overrides():
    """从 system_config 读运行时覆盖值（带 TTL 缓存，避免每次发送都查库）。"""
    global _param_cached_at
    now = time.time()
    with _param_lock:
        if _param_cache and now - _param_cached_at < _PARAM_TTL:
            return _param_cache
        out = {}
        try:
            for short, key in CONFIG_KEYS.items():
                row = _conn().execute(
                    "SELECT value FROM system_config WHERE key=?", (key,)).fetchone()
                if row and str(row[0]).strip() != "":
                    out[short] = str(row[0]).strip()
        except Exception:
            out = {}
        _param_cache.clear()
        _param_cache.update(out)
        _param_cached_at = now
        return _param_cache


def param(short, default):
    """取一个熔断参数：system_config > 环境变量默认值。"""
    raw = _read_overrides().get(short)
    if raw is None:
        return default
    try:
        return type(default)(raw)
    except (TypeError, ValueError):
        log.logger.warning(f"[Breaker] invalid override {short}={raw!r}, using {default}")
        return default


def invalidate_param_cache():
    """清掉参数缓存（设置页改完即时生效）。"""
    global _param_cached_at
    with _param_lock:
        _param_cache.clear()
        _param_cached_at = 0.0

CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"

_HTTP_5XX = re.compile(r"\b5\d{2}\b")
_HTTP_4XX = re.compile(r"\b4\d{2}\b")
# 连接 / 超时类关键字（视为服务侧故障）
_FAILURE_HINTS = (
    "timeout", "timed out", "read timed out",
    "connection", "max retries exceeded", "refused",
    "unreachable", "reset by peer", "eof occurred",
)


def classify(error):
    """判断一次失败是否计入熔断。

    返回 True = 计入失败；False = 忽略（4xx 等业务侧拒绝）。
    未知错误保守计入失败——「发送没成功」比「漏计故障」代价更小。
    """
    e = (error or "").lower()
    if _HTTP_5XX.search(e) or any(h in e for h in _FAILURE_HINTS):
        return True
    if _HTTP_4XX.search(e):
        return False
    return True


class _ChannelState:
    __slots__ = ("state", "opened_at", "open_count", "half_open_ok",
                 "window", "consecutive_fail", "probe_in_flight")

    def __init__(self):
        self.state = CLOSED
        self.opened_at = 0.0
        self.open_count = 0        # 累计熔断次数，用于指数退避
        self.half_open_ok = 0
        self.window = collections.deque()   # (ts, is_failure)
        self.consecutive_fail = 0
        self.probe_in_flight = False        # HALF_OPEN 时只允许一个探测在途


class CircuitBreaker:
    def __init__(self):
        self._lock = threading.Lock()
        self._states = {}
        self._loaded = False

    # ── 内部 ──
    def _st(self, channel_id):
        st = self._states.get(channel_id)
        if st is None:
            st = _ChannelState()
            self._states[channel_id] = st
        return st

    def _cooldown(self, st):
        """本次 OPEN 的冷却时长（指数退避，封顶 OPEN_MAX_SECONDS）。"""
        n = max(0, st.open_count - 1)
        return min(param("open_base", OPEN_BASE_SECONDS) * (2 ** n),
                   param("open_max", OPEN_MAX_SECONDS))

    def _evict(self, st, now):
        cutoff = now - param("window", WINDOW_SECONDS)
        w = st.window
        while w and w[0][0] < cutoff:
            w.popleft()

    def _persist(self, st, channel_id):
        try:
            conn = _conn()
            conn.execute(
                """INSERT INTO channel_breaker
                       (channel_id, state, opened_at, open_count, half_open_ok, updated_at)
                   VALUES (?,?,?,?,?,datetime('now'))
                   ON CONFLICT(channel_id) DO UPDATE SET
                       state=excluded.state,
                       opened_at=excluded.opened_at,
                       open_count=excluded.open_count,
                       half_open_ok=excluded.half_open_ok,
                       updated_at=excluded.updated_at""",
                (channel_id, st.state, st.opened_at, st.open_count, st.half_open_ok)
            )
            conn.commit()
        except Exception as e:
            log.logger.warning(f"[Breaker] persist failed (ch={channel_id}): {e}")

    def _open(self, st, channel_id, now, reason):
        st.state = OPEN
        st.opened_at = now
        st.open_count += 1
        st.half_open_ok = 0
        st.window.clear()
        self._persist(st, channel_id)
        log.logger.warning(
            f"[Breaker] Channel {channel_id} → OPEN ({reason}); "
            f"cooldown {int(self._cooldown(st))}s"
        )

    # ── 对外 ──
    def load(self):
        """从 SQLite 恢复状态（进程启动时调用一次）。"""
        if self._loaded:
            return
        try:
            rows = _conn().execute("SELECT * FROM channel_breaker").fetchall()
        except Exception as e:
            log.logger.warning(f"[Breaker] load failed: {e}")
            self._loaded = True
            return
        with self._lock:
            for r in rows:
                st = self._st(r["channel_id"])
                st.state = r["state"] or CLOSED
                st.opened_at = float(r["opened_at"] or 0)
                st.open_count = int(r["open_count"] or 0)
                st.half_open_ok = 0      # 重启后重新计数探测
            self._loaded = True
        if rows:
            log.logger.info(f"[Breaker] Restored {len(rows)} channel state(s) from DB")

    def should_allow(self, channel_id):
        """是否放行本次发送。返回 (allowed: bool, reason: str|None)。"""
        now = time.time()
        with self._lock:
            st = self._st(channel_id)
            if st.state == CLOSED:
                return True, None
            if st.state == HALF_OPEN:
                # 只放**一个**探测：HALF_OPEN 期间如果全放行，一次故障恢复可能
                # 瞬间打出一整批请求给刚出问题的第三方；其余请求按"未发送"处理
                if st.probe_in_flight:
                    return False, "probing (another probe in flight)"
                st.probe_in_flight = True
                return True, None
            # OPEN
            cd = self._cooldown(st)
            if now - st.opened_at >= cd:
                st.state = HALF_OPEN
                st.half_open_ok = 0
                # 这一次本身就是第一个探测，同样要占住闸门，
                # 否则紧随其后的请求会一起被放行
                st.probe_in_flight = True
                self._persist(st, channel_id)
                log.logger.info(f"[Breaker] Channel {channel_id} → HALF_OPEN (probing)")
                return True, None
            left = int(cd - (now - st.opened_at))
            return False, f"open, {left}s left"

    def record(self, channel_id, ok, error=""):
        """记录一次发送结果。"""
        now = time.time()
        with self._lock:
            st = self._st(channel_id)

            if ok:
                st.consecutive_fail = 0
            else:
                if not classify(error):
                    return              # 4xx 等不计失败
                st.consecutive_fail += 1

            if st.state == HALF_OPEN:
                st.probe_in_flight = False      # 探测已返回，释放闸门
                if ok:
                    st.half_open_ok += 1
                    if st.half_open_ok >= param("half_open_ok", HALF_OPEN_NEEDED):
                        st.state = CLOSED
                        st.open_count = 0
                        st.half_open_ok = 0
                        st.window.clear()
                        self._persist(st, channel_id)
                        log.logger.info(f"[Breaker] Channel {channel_id} → CLOSED (recovered)")
                else:
                    self._open(st, channel_id, now, "probe failed")
                return

            if st.state == OPEN:
                return                  # OPEN 期间的结果不计（正常不会走到）

            # CLOSED：维护滑动窗口
            st.window.append((now, not ok))
            self._evict(st, now)

            if not ok and st.consecutive_fail >= param("consecutive", CONSECUTIVE_THRESHOLD):
                self._open(st, channel_id, now,
                           f"{st.consecutive_fail} consecutive failures")
                return

            total = len(st.window)
            fails = sum(1 for _, f in st.window if f)
            if (total >= param("min_samples", MIN_SAMPLES)
                    and fails / total > param("failure_ratio", FAILURE_RATIO)):
                self._open(st, channel_id, now, f"failure ratio {fails}/{total}")

    def reset(self, channel_id):
        """手工恢复某通道（运维用）。"""
        with self._lock:
            st = self._st(channel_id)
            st.state = CLOSED
            st.open_count = 0
            st.half_open_ok = 0
            st.consecutive_fail = 0
            st.window.clear()
            self._persist(st, channel_id)
            log.logger.info(f"[Breaker] Channel {channel_id} manually reset → CLOSED")

    def snapshot(self):
        """当前处于非 CLOSED 的通道（供 API / 日志用）。"""
        now = time.time()
        with self._lock:
            out = []
            for cid, st in self._states.items():
                if st.state == CLOSED:
                    continue
                out.append({
                    "channel_id": cid,
                    "state": st.state,
                    "open_count": st.open_count,
                    "retry_in": (max(0, int(self._cooldown(st) - (now - st.opened_at)))
                                 if st.state == OPEN else 0),
                })
            return out


# ── 单例 ──
_breaker = None


def get_breaker():
    global _breaker
    if _breaker is None:
        _breaker = CircuitBreaker()
    return _breaker

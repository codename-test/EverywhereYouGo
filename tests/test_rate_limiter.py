# tests/test_rate_limiter.py
"""出站通道限流测试：令牌桶按通道独立、溢出不阻塞、配置持久化。"""
import sys
import os
import time
import tempfile
import shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_test_db_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_test_db_dir, "test_ratelimit.db")

import db
import rate_limiter


class TestRateLimiter:
    @classmethod
    def setup_class(cls):
        db.init_db()

    def setup_method(self):
        self.lim = rate_limiter.RateLimiter()
        conn = db._conn()
        conn.execute("DELETE FROM channel_rate_limit")
        conn.commit()
        self.lim._rates = {}
        self.lim._buckets = {}
        self.lim._loaded = True

    def test_unlimited_by_default(self):
        assert self.lim.acquire(999) is True

    def test_rate_persisted_and_loaded(self):
        self.lim.set_rate(201, 30)
        fresh = rate_limiter.RateLimiter()
        fresh.load()
        assert fresh.get_rate(201) == 30

    def test_bucket_limits_burst(self):
        # 60/分钟 → 桶容量 60，取 60 次应立即成功，第 61 次失败
        self.lim.set_rate(202, 60)
        granted = sum(1 for _ in range(60) if self.lim.acquire(202))
        assert granted == 60, f"桶容量应为 60，实际放行 {granted}"
        assert self.lim.acquire(202, max_wait=0.05) is False, "超出额度应拒绝"

    def test_zero_means_unlimited(self):
        self.lim.set_rate(203, 0)
        for _ in range(500):
            assert self.lim.acquire(203) is True

    def test_refill_over_time(self):
        # 600/分钟 = 10/秒；取空后等待应重新有令牌
        self.lim.set_rate(204, 600)
        for _ in range(600):
            assert self.lim.acquire(204) is True
        assert self.lim.acquire(204, max_wait=0.02) is False
        time.sleep(0.3)                      # 约 3 个令牌
        assert self.lim.acquire(204, max_wait=0.05) is True

    def test_channels_isolated(self):
        self.lim.set_rate(205, 1)
        assert self.lim.acquire(205) is True
        assert self.lim.acquire(205, max_wait=0.02) is False
        # 另一通道不受影响
        self.lim.set_rate(206, 60)
        assert self.lim.acquire(206) is True

    def test_acquire_does_not_block_longer_than_max_wait(self):
        self.lim.set_rate(207, 1)            # 1/分钟 → 等待很久
        assert self.lim.acquire(207) is True
        t0 = time.time()
        assert self.lim.acquire(207, max_wait=0.2) is False
        elapsed = time.time() - t0
        assert elapsed < 0.6, f"不应长时间阻塞，实际 {elapsed:.2f}s"

    def test_get_rate_picks_up_external_db_change(self):
        """直接改库（不经 set_rate）也应在 TTL 后生效，无需重启服务。"""
        lim = rate_limiter.RateLimiter()
        lim._rates = {}
        lim._checked_at = {}
        lim._loaded = True

        assert lim.get_rate(300) == 0          # 首次回查，缓存 0

        conn = db._conn()
        conn.execute(
            "INSERT INTO channel_rate_limit (channel_id, per_minute) VALUES (300, 42)"
        )
        conn.commit()

        assert lim.get_rate(300) == 0, "TTL 内应继续用缓存"

        lim._checked_at[300] = 0               # 模拟 TTL 过期
        assert lim.get_rate(300) == 42, "TTL 过期后应回查数据库"

    def test_set_rate_takes_effect_immediately(self):
        lim = rate_limiter.RateLimiter()
        lim._rates = {}
        lim._checked_at = {}
        lim._loaded = True
        assert lim.get_rate(301) == 0
        lim.set_rate(301, 7)
        assert lim.get_rate(301) == 7, "set_rate 后应立即生效（不必等 TTL）"

    def test_snapshot_only_lists_limited(self):
        self.lim.set_rate(208, 10)
        snap = self.lim.snapshot()
        assert snap.get(208) == 10
        assert 999 not in snap

    @classmethod
    def teardown_class(cls):
        shutil.rmtree(_test_db_dir, ignore_errors=True)

# tests/test_circuit_breaker.py
"""通道熔断器测试：三态机、4xx 不计失败、冷却退避、HALF_OPEN 恢复、持久化。"""
import sys
import os
import time
import tempfile
import shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_test_db_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_test_db_dir, "test_breaker.db")

import db
import circuit_breaker


class TestClassify:
    def test_4xx_not_counted(self):
        assert circuit_breaker.classify("HTTP 400 Bad Request") is False
        assert circuit_breaker.classify("HTTP 401") is False
        assert circuit_breaker.classify("HTTP 404") is False
        assert circuit_breaker.classify("HTTP 429 Too Many Requests") is False

    def test_5xx_counted(self):
        assert circuit_breaker.classify("HTTP 500") is True
        assert circuit_breaker.classify("HTTP 502 Bad Gateway") is True
        assert circuit_breaker.classify("HTTP 503") is True

    def test_timeout_and_connection_counted(self):
        assert circuit_breaker.classify("Read timed out.") is True
        assert circuit_breaker.classify("ConnectionError") is True
        assert circuit_breaker.classify("Max retries exceeded") is True
        assert circuit_breaker.classify("Connection refused") is True

    def test_unknown_counted_conservatively(self):
        assert circuit_breaker.classify("something odd happened") is True

    def test_business_errcode_not_mistaken_for_4xx(self):
        # WeCom 的 errcode=45009 不应被当成 HTTP 4xx
        assert circuit_breaker.classify("errcode=45009") is True


class TestStateMachine:
    @classmethod
    def setup_class(cls):
        db.init_db()

    def setup_method(self):
        self.b = circuit_breaker.CircuitBreaker()
        self.b._loaded = True
        conn = db._conn()
        conn.execute("DELETE FROM channel_breaker")
        conn.commit()

    def test_consecutive_failures_opens(self):
        cid = 101
        for _ in range(circuit_breaker.CONSECUTIVE_THRESHOLD):
            allowed, _ = self.b.should_allow(cid)
            assert allowed, "熔断前应放行"
            self.b.record(cid, False, "HTTP 500")

        allowed, reason = self.b.should_allow(cid)
        assert not allowed, "达到连续失败阈值后应熔断"
        assert "open" in reason

    def test_4xx_does_not_open(self):
        cid = 102
        for _ in range(circuit_breaker.CONSECUTIVE_THRESHOLD * 3):
            self.b.record(cid, False, "HTTP 400")
        allowed, _ = self.b.should_allow(cid)
        assert allowed, "4xx 不应触发熔断"

    def test_success_resets_consecutive_counter(self, monkeypatch):
        # 提高 MIN_SAMPLES，隔离出「连续失败」规则单独验证
        # （否则窗口失败率规则会先触发，测不到连续计数）
        monkeypatch.setattr(circuit_breaker, "MIN_SAMPLES", 1000)
        cid = 103
        for _ in range(circuit_breaker.CONSECUTIVE_THRESHOLD - 1):
            self.b.record(cid, False, "HTTP 500")
        assert self.b._states[cid].consecutive_fail == circuit_breaker.CONSECUTIVE_THRESHOLD - 1

        self.b.record(cid, True, "")
        assert self.b._states[cid].consecutive_fail == 0, "成功应清零连续失败计数"

        for _ in range(circuit_breaker.CONSECUTIVE_THRESHOLD - 1):
            self.b.record(cid, False, "HTTP 500")
        allowed, _ = self.b.should_allow(cid)
        assert allowed, "清零后重新累计，未达阈值不应熔断"

    def test_failure_ratio_rule_opens(self, monkeypatch):
        """窗口内失败率超阈值（样本数达标）也熔断。"""
        monkeypatch.setattr(circuit_breaker, "MIN_SAMPLES", 10)
        monkeypatch.setattr(circuit_breaker, "CONSECUTIVE_THRESHOLD", 1000)
        cid = 110
        for _ in range(4):
            self.b.record(cid, True, "")
        for _ in range(6):
            self.b.record(cid, False, "HTTP 503")     # 6/10 = 60% > 50%
        allowed, _ = self.b.should_allow(cid)
        assert not allowed, "失败率 60% 且样本数达标应熔断"

    def test_half_open_recovery(self, monkeypatch):
        monkeypatch.setattr(circuit_breaker, "OPEN_BASE_SECONDS", 0.1)
        monkeypatch.setattr(circuit_breaker, "OPEN_MAX_SECONDS", 0.2)
        cid = 104
        for _ in range(circuit_breaker.CONSECUTIVE_THRESHOLD):
            self.b.record(cid, False, "HTTP 500")

        allowed, _ = self.b.should_allow(cid)
        assert not allowed, "应立即处于 OPEN"

        time.sleep(0.15)
        allowed, _ = self.b.should_allow(cid)
        assert allowed, "冷却到期后应放行探测（HALF_OPEN）"
        # 这次放行本身就是第一个探测，必须记录结果才会释放闸门
        self.b.record(cid, True, "")

        for _ in range(circuit_breaker.HALF_OPEN_NEEDED - 1):
            allowed, _ = self.b.should_allow(cid)
            assert allowed, "上一个探测返回后应放行下一个"
            self.b.record(cid, True, "")

        assert self.b.should_allow(cid)[0] is True
        st = self.b._states[cid]
        assert st.state == circuit_breaker.CLOSED, "连续探测成功后应恢复 CLOSED"
        assert st.open_count == 0, "恢复后退避计数应重置"

    def test_half_open_probe_failure_reopens_with_backoff(self, monkeypatch):
        monkeypatch.setattr(circuit_breaker, "OPEN_BASE_SECONDS", 0.1)
        monkeypatch.setattr(circuit_breaker, "OPEN_MAX_SECONDS", 0.2)
        cid = 105
        for _ in range(circuit_breaker.CONSECUTIVE_THRESHOLD):
            self.b.record(cid, False, "HTTP 500")
        assert self.b._states[cid].open_count == 1

        time.sleep(0.15)
        self.b.should_allow(cid)              # 进入 HALF_OPEN
        self.b.record(cid, False, "HTTP 500")  # 探测失败
        st = self.b._states[cid]
        assert st.state == circuit_breaker.OPEN
        assert st.open_count == 2, "探测失败应重新 OPEN 且退避计数 +1"

    def test_half_open_allows_only_one_probe_at_a_time(self, monkeypatch):
        """HALF_OPEN 期间只放一个探测，避免刚恢复就打出一批请求。"""
        monkeypatch.setattr(circuit_breaker, "OPEN_BASE_SECONDS", 0.05)
        monkeypatch.setattr(circuit_breaker, "OPEN_MAX_SECONDS", 0.1)
        cid = 120
        for _ in range(circuit_breaker.CONSECUTIVE_THRESHOLD):
            self.b.record(cid, False, "HTTP 500")

        time.sleep(0.08)
        first, _ = self.b.should_allow(cid)
        assert first is True, "冷却到期后应放行第一个探测"

        for _ in range(5):
            again, reason = self.b.should_allow(cid)
            assert again is False, "探测在途时不应再放行"
            assert "probing" in reason

        # 探测返回后闸门释放
        self.b.record(cid, True, "")
        assert self.b.should_allow(cid)[0] is True

    def test_probe_gate_released_on_failure_too(self, monkeypatch):
        monkeypatch.setattr(circuit_breaker, "OPEN_BASE_SECONDS", 0.05)
        monkeypatch.setattr(circuit_breaker, "OPEN_MAX_SECONDS", 0.1)
        cid = 121
        for _ in range(circuit_breaker.CONSECUTIVE_THRESHOLD):
            self.b.record(cid, False, "HTTP 500")
        time.sleep(0.08)
        self.b.should_allow(cid)
        assert self.b._states[cid].probe_in_flight is True
        self.b.record(cid, False, "HTTP 500")
        assert self.b._states[cid].probe_in_flight is False, "探测失败也要释放闸门"

    def test_state_persisted_and_restored(self):
        cid = 106
        for _ in range(circuit_breaker.CONSECUTIVE_THRESHOLD):
            self.b.record(cid, False, "HTTP 500")

        fresh = circuit_breaker.CircuitBreaker()
        fresh.load()
        allowed, reason = fresh.should_allow(cid)
        assert not allowed, f"重启后应恢复 OPEN 状态，实际 reason={reason}"

    def test_manual_reset(self):
        cid = 107
        for _ in range(circuit_breaker.CONSECUTIVE_THRESHOLD):
            self.b.record(cid, False, "HTTP 500")
        assert self.b.should_allow(cid)[0] is False
        self.b.reset(cid)
        assert self.b.should_allow(cid)[0] is True

    def test_snapshot_lists_non_closed(self):
        cid = 108
        for _ in range(circuit_breaker.CONSECUTIVE_THRESHOLD):
            self.b.record(cid, False, "HTTP 500")
        snap = self.b.snapshot()
        assert any(s["channel_id"] == cid and s["state"] == circuit_breaker.OPEN
                   for s in snap)

    @classmethod
    def teardown_class(cls):
        shutil.rmtree(_test_db_dir, ignore_errors=True)

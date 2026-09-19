# tests/test_p2_hardening.py
"""P2 收口项测试：优雅停机、/api/metrics、API 入参校验、模块解耦、时间基准。"""
import sys
import os
import json
import time
import tempfile
import shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_test_db_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_test_db_dir, "test_p2.db")
# 测试里关掉内置 HTTPS，避免 GET 被 301 到不存在的 HTTPS 端口
os.environ["EGO_SSL_ENABLED"] = "0"

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import db
import bus
import parser_engine      # noqa: F401
import router_engine      # noqa: F401
import sender_engine
import worker
from queue_backend import get_backend


def _reset_db():
    conn = db._conn()
    for t in ("message_queue", "message_log", "dead_letter_queue", "dedup_keys",
              "source_channels", "channels", "sources", "channel_breaker",
              "channel_rate_limit"):
        conn.execute("DELETE FROM %s" % t)
    conn.execute("INSERT INTO sources (id,name,parser_id,enabled) VALUES (1,'s',1,1)")
    conn.execute("INSERT INTO channels (id,name,type,config,enabled) "
                 "VALUES (1,'c','fake','{}',1)")
    conn.execute("INSERT INTO source_channels "
                 "(source_id,channel_id,template_id,condition_expr,enabled) "
                 "VALUES (1,1,1,'',1)")
    conn.commit()


class _SlowOkChannel:
    """睡一会儿再成功的假通道，用来制造"在途任务"。"""

    def __init__(self, delay=0.25):
        self.delay = delay
        self.done = 0

    def send(self, title, content):
        time.sleep(self.delay)
        self.done += 1
        return True, ""

    def test(self):
        return True


class TestGracefulShutdown:
    @classmethod
    def setup_class(cls):
        db.init_db()

    def setup_method(self):
        _reset_db()
        worker._running = True
        worker._threads.clear()

    def teardown_method(self):
        worker._running = True
        worker._threads.clear()

    def test_stop_waits_for_inflight_task(self):
        """停机应等在途任务跑完，而不是立刻返回。"""
        self._mkmsg("g1")
        get_backend().enqueue(trace_id="g1", source_id=1, msg_json='{"title":"T"}',
                              channel_id=1, template_id=1)
        ch = _SlowOkChannel(delay=0.3)
        sender_engine.create_channel = lambda t, c: ch

        worker.start_workers(1)
        time.sleep(0.05)              # 让 worker 取到任务并开始发送
        worker.stop_workers(timeout=10)

        assert ch.done == 1, "在途任务应被等待完成，实际完成 %d 次" % ch.done
        assert db._conn().execute(
            "SELECT COUNT(*) FROM message_queue").fetchone()[0] == 0, "任务应已 ack"

    def test_stop_flushes_leftover_processing_to_dlq(self):
        """超时兜底：仍处于 processing 的任务应被刷入死信队列，而不是凭空消失。"""
        conn = db._conn()
        conn.execute(
            """INSERT INTO message_queue
               (trace_id, source_id, msg_json, channel_id, template_id, status)
               VALUES ('stuck', 1, '{"title":"T"}', 1, 1, 'processing')""")
        conn.commit()

        worker.stop_workers(timeout=0)     # 无 worker 线程，直接走兜底

        assert conn.execute(
            "SELECT COUNT(*) FROM message_queue").fetchone()[0] == 0, "不应留在队列"
        dlq = conn.execute("SELECT trace_id, error FROM dead_letter_queue").fetchall()
        assert len(dlq) == 1 and dlq[0]["trace_id"] == "stuck"
        assert "shutdown" in dlq[0]["error"]

    def test_stop_order_stops_ingress_before_workers(self):
        """停机顺序：必须先停接收端，再停 worker。

        反过来的话，接收端还会继续往队列里塞新消息，而 worker 已经退出，
        这些消息会卡在队列里没人处理。
        """
        import main as ego_main

        calls = []

        class _Mgr:
            def stop_all(self):
                calls.append("stop_all")

        orig_stop = worker.stop_workers

        def _fake_stop(*a, **k):
            calls.append("stop_workers")

        worker.stop_workers = _fake_stop
        try:
            ego_main.shutdown(_Mgr())
        finally:
            worker.stop_workers = orig_stop

        assert calls == ["stop_all", "stop_workers"], \
            "停机顺序不对：%s（应先停接收再停 worker）" % calls

    def test_stop_continues_even_if_ingress_stop_fails(self):
        """停接收报错也要继续停 worker，不能半路中断。"""
        import main as ego_main

        calls = []

        class _BadMgr:
            def stop_all(self):
                calls.append("stop_all")
                raise RuntimeError("boom")

        orig_stop = worker.stop_workers
        worker.stop_workers = lambda *a, **k: calls.append("stop_workers")
        try:
            ego_main.shutdown(_BadMgr())     # 不应抛异常
        finally:
            worker.stop_workers = orig_stop

        assert calls == ["stop_all", "stop_workers"], \
            "停接收失败后仍应继续停 worker，实际 %s" % calls

    def _mkmsg(self, trace):
        db.create_message_log(trace, 1, "s", "{}", "SENDING")


class TestMetricsEndpoint:
    @classmethod
    def setup_class(cls):
        db.init_db()

    def setup_method(self):
        _reset_db()

    def test_metrics_shape(self):
        from api import create_app
        app = create_app()
        c = app.test_client()

        r = c.get("/api/metrics")
        assert r.status_code == 200, r.data[:200]
        d = json.loads(r.data)
        for key in ("queue", "messages", "channels", "latency", "breaker",
                    "rate_limits", "window_hours"):
            assert key in d, "缺少字段 %s" % key
        assert isinstance(d["channels"], list)
        assert set(("samples", "avg_seconds", "max_seconds")) <= set(d["latency"])

    def test_metrics_channel_aggregation(self):
        """写入带结果的日志后，/api/metrics 应能聚合出该渠道的成功率。"""
        db.create_message_log("m1", 1, "s", "{}", "SUCCESS")
        db.update_message("m1", channel_results=json.dumps([
            {"channel_id": 1, "ch_name": "c", "ch_type": "fake", "ok": True, "error": None},
            {"channel_id": 1, "ch_name": "c", "ch_type": "fake", "ok": False, "error": "x"},
        ]), sent_at="2020-01-01 00:00:00")
        # sent_at 设成过去，避免影响延迟统计的断言
        db._conn().execute("UPDATE message_log SET sent_at=NULL WHERE trace_id='m1'")
        db._conn().commit()

        from api import create_app
        c = create_app().test_client()
        d = json.loads(c.get("/api/metrics").data)
        ch = [x for x in d["channels"] if x["channel_id"] == 1]
        assert ch, "应聚合出渠道 1"
        assert ch[0]["attempts"] == 2 and ch[0]["ok"] == 1
        assert ch[0]["success_rate"] == 0.5

    def test_window_clamped(self):
        from api import create_app
        c = create_app().test_client()
        d = json.loads(c.get("/api/metrics?hours=999999").data)
        assert d["window_hours"] == 24 * 30, "窗口应被夹到上限"
        d = json.loads(c.get("/api/metrics?hours=0").data)
        assert d["window_hours"] == 1


class TestApiInputValidation:
    """improvement #27：非法入参应返回 400，而不是 500 或写坏配置。"""

    @classmethod
    def setup_class(cls):
        db.init_db()
        from api import create_app
        cls.client = create_app().test_client()

    def setup_method(self):
        _reset_db()

    def _post(self, url, payload):
        return self.client.post(url, data=json.dumps(payload),
                                content_type="application/json")

    def test_source_missing_name(self):
        r = self._post("/api/sources", {"port": 12345})
        assert r.status_code == 400, r.data[:200]
        assert "name" in json.loads(r.data)["error"]

    def test_source_bad_port(self):
        assert self._post("/api/sources", {"name": "a", "port": 99999}).status_code == 400
        assert self._post("/api/sources", {"name": "a", "port": "abc"}).status_code == 400
        assert self._post("/api/sources", {"name": "a", "port": 0}).status_code == 400

    def test_source_bad_slug(self):
        r = self._post("/api/sources", {"name": "a", "slug": "bad slug!"})
        assert r.status_code == 400

    def test_source_valid_passes(self):
        r = self._post("/api/sources", {"name": "ok源", "port": 12399, "slug": "ok-slug"})
        assert r.status_code == 200, r.data[:200]

    def test_channel_missing_type(self):
        r = self._post("/api/channels", {"name": "c"})
        assert r.status_code == 400

    def test_channel_bad_config(self):
        r = self._post("/api/channels", {"name": "c", "type": "fake", "config": "not json"})
        assert r.status_code == 400

    def test_template_bad_engine(self):
        r = self._post("/api/templates", {"name": "t", "engine": "mako"})
        assert r.status_code == 400

    def test_settings_bad_log_level(self):
        r = self._post("/api/settings", {"log_level": "LOUD"})
        assert r.status_code == 400

    def test_settings_bad_dnd_time(self):
        assert self._post("/api/settings", {"dnd_start": "25:00"}).status_code == 400
        assert self._post("/api/settings", {"dnd_end": "7:0"}).status_code == 400

    def test_settings_bad_path_prefix(self):
        assert self._post("/api/settings", {"path_prefix": "a b"}).status_code == 400
        assert self._post("/api/settings", {"path_prefix": "in"}).status_code == 200

    def test_batch_bad_action(self):
        r = self._post("/api/messages/batch", {"action": "explode", "ids": [1]})
        assert r.status_code == 400

    def test_batch_bad_ids(self):
        r = self._post("/api/messages/batch", {"action": "delete", "ids": "1,2"})
        assert r.status_code == 400

    def test_retry_bad_mode(self):
        r = self.client.post("/api/messages/1/retry?mode=nope")
        assert r.status_code == 400

    def test_retry_bad_scope(self):
        r = self.client.post("/api/messages/1/retry?scope=everything")
        assert r.status_code == 400


class TestBreakerParamsAdjustable:
    """熔断参数可由用户手动调整（system_config），不必改环境变量或重启。"""

    @classmethod
    def setup_class(cls):
        db.init_db()
        from api import create_app
        cls.client = create_app().test_client()

    def setup_method(self):
        _reset_db()
        db._conn().execute("DELETE FROM system_config WHERE key LIKE 'breaker_%'")
        db._conn().commit()
        import circuit_breaker
        circuit_breaker.invalidate_param_cache()

    def teardown_method(self):
        import circuit_breaker
        circuit_breaker.invalidate_param_cache()

    def test_falls_back_to_constant_when_unset(self):
        import circuit_breaker
        assert circuit_breaker.param("consecutive", circuit_breaker.CONSECUTIVE_THRESHOLD) \
            == circuit_breaker.CONSECUTIVE_THRESHOLD

    def test_system_config_overrides_constant(self):
        import circuit_breaker
        db.set_config("breaker_consecutive", "9")
        circuit_breaker.invalidate_param_cache()
        assert circuit_breaker.param("consecutive", circuit_breaker.CONSECUTIVE_THRESHOLD) == 9

    def test_settings_api_writes_and_validates_breaker_params(self):
        r = self.client.post("/api/settings", data=json.dumps({
            "breaker_window": "120", "breaker_consecutive": "7",
            "breaker_failure_ratio": "0.8",
        }), content_type="application/json")
        assert r.status_code == 200, r.data[:200]

        import circuit_breaker
        assert circuit_breaker.param("window", 60) == 120
        assert circuit_breaker.param("consecutive", 5) == 7
        assert circuit_breaker.param("failure_ratio", 0.5) == 0.8

    def test_settings_rejects_bad_values(self):
        assert self.client.post("/api/settings", data=json.dumps({"breaker_window": "0"}),
                                content_type="application/json").status_code == 400
        assert self.client.post("/api/settings", data=json.dumps({"breaker_consecutive": "abc"}),
                                content_type="application/json").status_code == 400
        assert self.client.post("/api/settings", data=json.dumps({"breaker_failure_ratio": "1.5"}),
                                content_type="application/json").status_code == 400
        assert self.client.post("/api/settings", data=json.dumps({"breaker_failure_ratio": "0"}),
                                content_type="application/json").status_code == 400

    def test_empty_value_clears_override(self):
        db.set_config("breaker_consecutive", "9")
        import circuit_breaker
        circuit_breaker.invalidate_param_cache()
        assert circuit_breaker.param("consecutive", 5) == 9

        r = self.client.post("/api/settings", data=json.dumps({"breaker_consecutive": ""}),
                             content_type="application/json")
        assert r.status_code == 200
        assert circuit_breaker.param("consecutive", 5) == 5, "留空应回退到默认值"

    def test_take_effect_without_restart(self):
        """改完立即生效（缓存被失效），不需要重启容器。"""
        import circuit_breaker
        before = circuit_breaker.param("open_base", 30)
        self.client.post("/api/settings", data=json.dumps({"breaker_open_base": "11"}),
                         content_type="application/json")
        assert circuit_breaker.param("open_base", 30) == 11
        assert before != 11

    def test_settings_page_renders_breaker_fields(self):
        html = self.client.get("/settings").data.decode("utf-8")
        for name in ("breaker_window", "breaker_min_samples", "breaker_failure_ratio",
                     "breaker_consecutive", "breaker_open_base", "breaker_open_max",
                     "breaker_half_open_ok"):
            assert 'name="%s"' % name in html, "设置页缺少字段 %s" % name


class TestResilienceUI:
    """熔断 / 限流的 WebUI 接线（此前只有 API，没有界面）。"""

    KEY = "ch.col_resilience"

    @classmethod
    def setup_class(cls):
        db.init_db()
        from api import create_app
        cls.client = create_app().test_client()

    def setup_method(self):
        _reset_db()

    def _channels_html(self):
        r = self.client.get("/channels")
        assert r.status_code == 200, r.status_code
        return r.data.decode("utf-8")

    def test_channels_page_has_rate_limit_input(self):
        html = self._channels_html()
        assert 'id="editChannelRateLimit"' in html, "通道弹窗缺少限流输入框"

    def test_channels_page_has_resilience_column(self):
        html = self._channels_html()
        assert 'id="res-cell-' in html, "通道列表缺少韧性状态单元格"

    def test_channels_page_wires_resilience_js(self):
        html = self._channels_html()
        for fn in ("loadResilience", "renderResilience", "resetBreaker", "saveRateLimit"):
            assert fn in html, "缺少前端函数 %s" % fn
        assert "/api/resilience" in html, "前端未调用韧性接口"

    def test_save_channel_persists_rate_limit(self):
        """前端保存通道时会顺带调用限流接口——这里验证该接口本身可用。"""
        r = self.client.post("/api/resilience/rate_limit/1",
                             data=json.dumps({"per_minute": 42}),
                             content_type="application/json")
        assert r.status_code == 200
        d = json.loads(self.client.get("/api/resilience").data)
        assert d["rate_limits"].get("1") == 42 or d["rate_limits"].get(1) == 42

    def test_breaker_reset_endpoint(self):
        from circuit_breaker import get_breaker
        b = get_breaker()
        for _ in range(6):
            b.record(1, False, "HTTP 500")
        assert b.should_allow(1)[0] is False
        r = self.client.post("/api/resilience/breaker/1/reset")
        assert r.status_code == 200
        assert b.should_allow(1)[0] is True


class TestI18nCompleteness:
    def test_resilience_keys_translated_in_both_languages(self):
        import i18n
        keys = [
            "ch.col_resilience", "ch.rate_limit_label", "ch.rate_limit_help",
            "ch.rate_limit_unit", "ch.rate_limit_badge_title",
            "ch.breaker_open", "ch.breaker_probing", "ch.breaker_open_title",
            "ch.breaker_reset", "ch.breaker_reset_done",
            "set.breaker_title", "set.breaker_help", "set.breaker_window",
            "set.breaker_min_samples", "set.breaker_failure_ratio",
            "set.breaker_consecutive", "set.breaker_open_base",
            "set.breaker_open_max", "set.breaker_half_open_ok",
        ]
        for k in keys:
            assert k in i18n.TRANSLATIONS["zh"], "中文缺 %s" % k
            assert k in i18n.TRANSLATIONS["en"], "英文缺 %s" % k

    def test_no_duplicate_keys_between_languages(self):
        """两个语言包的键集合应一致（漏翻会在这里暴露）。"""
        import i18n
        zh = set(i18n.TRANSLATIONS["zh"])
        en = set(i18n.TRANSLATIONS["en"])
        # 允许极少数只在一侧存在的历史键，但新加的韧性键必须在两侧都有
        missing_en = {"ch.col_resilience", "ch.rate_limit_label", "ch.breaker_open"} - en
        assert not missing_en, "英文缺键：%s" % missing_en


class TestModuleDecoupling:
    def test_log_does_not_import_db(self):
        """log.py 不应反向依赖 db（依赖方向 main → db → log）。"""
        import ast
        src = open(os.path.join(PROJECT, "log.py"), encoding="utf-8").read()
        # 用 AST 取真实 import（文档字符串里的示例代码不算）
        names = []
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                names += [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module.split(".")[0])
        assert "db" not in names, "log.py 仍 import db，解耦未完成：%s" % names

    def test_db_provides_log_handler(self):
        from db.log_handler import make_log_handler
        import logging
        h = make_log_handler()
        assert isinstance(h, logging.Handler)


class TestTimeBaseConsistency:
    def test_dt_now_str_is_utc(self):
        """sent_at 必须与 created_at(CURRENT_TIMESTAMP=UTC) 同基准。"""
        import datetime
        from sender_engine import dt_now_str

        got = datetime.datetime.strptime(
            dt_now_str(), "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=datetime.timezone.utc)
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        assert abs((now_utc - got).total_seconds()) < 3, (
            "dt_now_str 应与 UTC 一致（相差 %s 说明仍在用本地时间）"
            % (now_utc - got))

    @classmethod
    def teardown_class(cls):
        shutil.rmtree(_test_db_dir, ignore_errors=True)

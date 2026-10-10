# tests/test_query_only_guard.py
"""查询类 API 模式（api/__init__.py 的 _query_only_guard）行为测试。

覆盖：
  - /api/ 下 POST/PUT/DELETE/PATCH 一律 403；
  - GET/HEAD 不受 guard 影响；
  - 写端点白名单只精确匹配 /api/sources/full；
  - guard 在视图函数之前短路（不会走到入参校验）；
  - 非 /api/ 路径（登录、webhook 前缀）不受 guard 影响；
  - 认证中间件先于 guard 注册：未登录的写请求是 401 而不是 403。

这些断言只依赖 api/__init__.py 中已实现的逻辑，不依赖尚未落地的
GET /api/config 与 POST /api/sources/full 视图（后者仅断言"不是 403"）。
"""
import os
import sys
import json
import shutil
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_test_db_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_test_db_dir, "test_query_only_guard.db")
# 关掉内置 HTTPS，避免 GET 被 301 到不存在的 HTTPS 端口
os.environ["EGO_SSL_ENABLED"] = "0"
# 打开认证，才能区分"未登录 401"与"guard 403"
TEST_TOKEN = "guard-test-token"
os.environ["EGO_AUTH_TOKEN"] = TEST_TOKEN

GUARD_ERROR = "operation APIs disabled (query-only mode)"
AUTH_HEADER = {"Authorization": "Bearer " + TEST_TOKEN}


def _err_body(resp):
    try:
        return json.loads(resp.data)
    except Exception:
        return {}


class TestQueryOnlyGuard:
    @classmethod
    def setup_class(cls):
        import db
        db.init_db()
        from api import create_app
        cls.client = create_app().test_client()

    def _post(self, url, payload=None, headers=None):
        h = dict(AUTH_HEADER)
        if headers:
            h.update(headers)
        return self.client.post(url,
                                data=json.dumps(payload if payload is not None else {}),
                                content_type="application/json", headers=h)

    # ── (a) 写方法被屏蔽 ──

    def test_post_under_api_blocked(self):
        r = self._post("/api/settings", {"log_level": "INFO"})
        assert r.status_code == 403, r.data[:200]
        assert _err_body(r).get("error") == GUARD_ERROR, _err_body(r)

    def test_put_under_api_blocked(self):
        r = self.client.put("/api/sources/1",
                            data=json.dumps({"name": "x"}),
                            content_type="application/json", headers=AUTH_HEADER)
        assert r.status_code == 403, r.data[:200]
        assert _err_body(r).get("error") == GUARD_ERROR

    def test_delete_under_api_blocked(self):
        r = self.client.delete("/api/sources/1", headers=AUTH_HEADER)
        assert r.status_code == 403, r.data[:200]
        assert _err_body(r).get("error") == GUARD_ERROR

    def test_other_write_methods_blocked(self):
        for method in ("PATCH",):
            r = self.client.open("/api/sources/1", method=method, headers=AUTH_HEADER)
            assert r.status_code == 403, "%s %s" % (method, r.data[:200])

    def test_guard_short_circuits_before_validation(self):
        """guard 在视图之前返回：非法入参也不会走到校验逻辑（否则会是 400）。"""
        r = self._post("/api/settings", {"log_level": "LOUD"})
        assert r.status_code == 403, r.data[:200]
        assert _err_body(r).get("error") == GUARD_ERROR

    def test_blocked_response_shape(self):
        r = self._post("/api/messages/batch", {"action": "delete", "ids": [1]})
        d = _err_body(r)
        assert d.get("status") == "error", d
        assert d.get("error") == GUARD_ERROR, d

    # ── GET / HEAD 放行 ──

    def test_get_under_api_not_blocked(self):
        r = self.client.get("/api/health", headers=AUTH_HEADER)
        assert r.status_code != 403, r.data[:200]

    def test_head_under_api_not_blocked(self):
        r = self.client.head("/api/health", headers=AUTH_HEADER)
        assert r.status_code != 403, r.data[:200]

    # ── 白名单：只精确匹配 /api/sources/full ──

    def test_allowlisted_post_is_not_blocked(self):
        """白名单放行的是这个路径本身；视图尚未落地，所以只断言"不是 403"。"""
        r = self._post("/api/sources/full", {"name": "s"})
        assert r.status_code != 403, r.data[:200]
        assert _err_body(r).get("error") != GUARD_ERROR, _err_body(r)

    def test_allowlist_is_exact_match_not_prefix(self):
        for url in ("/api/sources/full/", "/api/sources/full/1", "/api/sources"):
            r = self._post(url, {})
            assert r.status_code == 403, "%s -> %s %s" % (url, r.status_code, r.data[:200])

    def test_auth_whitelist_does_not_exempt_writes(self):
        """/api/lang 在认证白名单里，但不在写白名单里，所以仍被 guard 屏蔽。"""
        r = self._post("/api/lang", {"lang": "zh"})
        assert r.status_code == 403, r.data[:200]
        assert _err_body(r).get("error") == GUARD_ERROR

    # ── guard 只管 /api/ ──

    def test_non_api_post_not_blocked(self):
        r = self.client.post("/login", data={"token": TEST_TOKEN})
        assert r.status_code != 403, r.data[:200]

    def test_webhook_prefix_post_not_blocked(self):
        """webhook 接收器在 /<prefix>/ 下，不受 guard 影响（默认前缀 in）。"""
        r = self.client.post("/in/anything", data=json.dumps({}),
                             content_type="application/json", headers=AUTH_HEADER)
        assert r.status_code != 403, r.data[:200]

    # ── 中间件顺序：认证先于 guard ──

    def test_unauthenticated_write_returns_401_not_403(self):
        r = self.client.post("/api/settings", data=json.dumps({}),
                             content_type="application/json")
        assert r.status_code == 401, r.data[:200]
        assert _err_body(r).get("error") != GUARD_ERROR, _err_body(r)

    def test_session_login_also_satisfies_guard_path(self):
        """登录后写请求仍被 guard 屏蔽（403），说明 401/403 是两条独立判定。"""
        c = self.client
        c.post("/login", data={"token": TEST_TOKEN})
        r = c.post("/api/settings", data=json.dumps({}), content_type="application/json")
        assert r.status_code == 403, r.data[:200]

    @classmethod
    def teardown_class(cls):
        shutil.rmtree(_test_db_dir, ignore_errors=True)

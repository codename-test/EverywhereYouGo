# tests/test_query_only_guard.py
"""查询类 API 模式（api/__init__.py 的 _query_only_guard）行为测试。

guard 只约束**外部 Bearer token 调用方**（C 方案）：
  - 带 Bearer token 时，/api/ 下 POST/PUT/DELETE/PATCH 一律 403；
  - GET/HEAD 不受 guard 影响；
  - 写端点白名单只精确匹配 /api/sources/full；
  - guard 在视图函数之前短路（不会走到入参校验）；
  - 非 /api/ 路径（登录、webhook 前缀）不受 guard 影响；
  - 认证中间件先于 guard 注册：未登录的写请求是 401 而不是 403。

放行（不启用 guard）：
  - session 已登录（WebUI 用户）：写请求放行，不是 403；
  - 未配置 EGO_AUTH_TOKEN：guard 整体不生效。

每个测试用独立 test_client，避免 /login 测试把 session 泄漏给后续用例。
"""
import os
import sys
import json
import shutil
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_test_db_dir = tempfile.mkdtemp()
# 关掉内置 HTTPS，避免 GET 被 301 到不存在的 HTTPS 端口
os.environ["EGO_SSL_ENABLED"] = "0"
# 打开认证，才能区分"未登录 401"与"guard 403"
TEST_TOKEN = "guard-test-token"

GUARD_ERROR = "operation APIs disabled (query-only mode)"
AUTH_HEADER = {"Authorization": "Bearer " + TEST_TOKEN}


@pytest.fixture(autouse=True, scope="module")
def _auth_token():
    """整模块测试期间开启认证（create_app 在 setup_class 期捕获 token，须先设好），
    模块结束即还原，避免泄漏到其它测试文件。"""
    orig = os.environ.get("EGO_AUTH_TOKEN")
    os.environ["EGO_AUTH_TOKEN"] = TEST_TOKEN
    try:
        yield
    finally:
        if orig is None:
            os.environ.pop("EGO_AUTH_TOKEN", None)
        else:
            os.environ["EGO_AUTH_TOKEN"] = orig


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
        cls.app = create_app()

    def setup_method(self):
        # 每个测试用全新 client，避免 /login 测试把 session 泄漏给后续测试
        self.client = self.app.test_client()

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

    def test_read_allowlist_permits_config(self):
        """外部 token 可读白名单内的 /api/config。"""
        r = self.client.get("/api/config", headers=AUTH_HEADER)
        assert r.status_code != 403, r.data[:200]

    def test_reads_outside_allowlist_blocked(self):
        """外部 token 只能读白名单；/api/channels(含 config)、/api/messages 等一律 403。"""
        for url in ("/api/channels", "/api/messages", "/api/logs", "/api/parsers", "/api/templates"):
            r = self.client.get(url, headers=AUTH_HEADER)
            assert r.status_code == 403, "%s -> %s %s" % (url, r.status_code, r.data[:120])
            assert _err_body(r).get("error") == "read API not permitted for integration token", _err_body(r)

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

    def test_session_login_bypasses_guard(self):
        """WebUI 走 session 登录：guard 只锁外部 Bearer token，session 写请求放行（不是 403）。"""
        c = self.client
        login = c.post("/login", data={"token": TEST_TOKEN})
        assert login.status_code in (200, 302), login.data[:200]
        r = c.post("/api/settings", data=json.dumps({}), content_type="application/json")
        assert r.status_code != 403, r.data[:200]
        assert _err_body(r).get("error") != GUARD_ERROR, _err_body(r)

    def test_bearer_cannot_establish_session_via_login(self):
        """带 Bearer 头的 /login 被拒(403)：API token 不能换交互式 session 绕过 guard。"""
        c = self.client
        r = c.post("/login", data={"token": TEST_TOKEN}, headers=AUTH_HEADER)
        assert r.status_code == 403, r.data[:200]
        # 没拿到 session：后续无凭据写请求应是未认证 401，而不是 session 放行
        r2 = c.post("/api/settings", data=json.dumps({}), content_type="application/json")
        assert r2.status_code == 401, r2.data[:200]

    @classmethod
    def teardown_class(cls):
        shutil.rmtree(_test_db_dir, ignore_errors=True)



def test_guard_inactive_without_auth_token():
    """未配置 EGO_AUTH_TOKEN 时 guard 整体不生效：即便未登录，/api/ 写请求也不应 403。"""
    old = os.environ.pop("EGO_AUTH_TOKEN", None)
    try:
        from api import create_app
        c = create_app().test_client()
        r = c.post("/api/settings", data=json.dumps({}), content_type="application/json")
        assert r.status_code != 403, r.data[:200]
        assert _err_body(r).get("error") != GUARD_ERROR, _err_body(r)
    finally:
        if old is not None:
            os.environ["EGO_AUTH_TOKEN"] = old

# tests/test_review_hardening.py
"""Review 加固项测试：#1 开放重定向、#2 登录限流、#6 全局 JSON 500、#3 原子写唯一 tmp。"""
import os
import sys
import json
import glob
import shutil
import tempfile
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["EGO_SSL_ENABLED"] = "0"

import db

TEST_TOKEN = "harden-token"


@pytest.fixture(autouse=True, scope="module")
def _auth_token():
    """本模块开启认证（create_app 在 setup_class 期捕获 token，须先设好），结束还原。"""
    orig = os.environ.get("EGO_AUTH_TOKEN")
    os.environ["EGO_AUTH_TOKEN"] = TEST_TOKEN
    try:
        yield
    finally:
        if orig is None:
            os.environ.pop("EGO_AUTH_TOKEN", None)
        else:
            os.environ["EGO_AUTH_TOKEN"] = orig


class TestAuthHardening:
    @classmethod
    def setup_class(cls):
        db.init_db()
        from api import create_app
        cls.app = create_app()

    def setup_method(self):
        # 每个测试清掉限流状态，避免跨用例串扰
        from api import auth as _auth
        _auth._login_fails.clear()

    # ── #1 开放重定向 ──
    def test_safe_next_allows_only_relative(self):
        from api.auth import _safe_next
        assert _safe_next("http://evil.com") == "/"
        assert _safe_next("//evil.com") == "/"
        assert _safe_next("/\\evil.com") == "/"
        assert _safe_next("/sources") == "/sources"
        assert _safe_next("") == "/"

    def test_login_next_open_redirect_blocked(self):
        c = self.app.test_client()
        r = c.post("/login?next=//evil.com", data={"token": TEST_TOKEN})
        assert r.status_code in (301, 302), r.status_code
        loc = r.headers.get("Location", "")
        assert "evil.com" not in loc, loc

    # ── #2 登录限流 ──
    def test_login_throttle_429_after_failures(self):
        c = self.app.test_client()
        codes = [c.post("/login", data={"token": "wrong"}).status_code for _ in range(7)]
        assert 429 in codes, codes

    def test_login_success_clears_throttle(self):
        c = self.app.test_client()
        for _ in range(4):  # 未达阈值
            c.post("/login", data={"token": "wrong"})
        r = c.post("/login", data={"token": TEST_TOKEN})
        assert r.status_code in (301, 302), r.status_code  # 成功，未被限流

    # ── #6 全局异常兜底 ──
    def test_unhandled_api_exception_json_500(self):
        from api import create_app
        app = create_app()

        @app.route("/api/_boom")
        def _boom():
            raise RuntimeError("boom")

        c = app.test_client()
        c.post("/login", data={"token": TEST_TOKEN})  # 建立 session 过 auth
        r = c.get("/api/_boom")
        assert r.status_code == 500, r.status_code
        assert json.loads(r.data).get("status") == "error", r.data[:200]

    def test_unhandled_nonapi_exception_text_500(self):
        from api import create_app
        app = create_app()

        @app.route("/_boom2")
        def _boom2():
            raise RuntimeError("boom2")

        c = app.test_client()
        c.post("/login", data={"token": TEST_TOKEN})
        r = c.get("/_boom2")
        assert r.status_code == 500
        assert b"Internal Server Error" in r.data


def test_write_json_unique_tmp_no_corruption():
    """#3：并发写同一配置文件不损坏、无 .tmp 残留（唯一 tmp 名）。"""
    import config_manager
    tmpdir = tempfile.mkdtemp()
    orig = config_manager.CONFIG_DIR
    config_manager.CONFIG_DIR = tmpdir
    try:
        def w(i):
            config_manager._write_json("sources.json", [{"id": i, "name": "n%d" % i}])
        threads = [threading.Thread(target=w, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        with open(os.path.join(tmpdir, "sources.json"), encoding="utf-8") as f:
            data = json.load(f)  # 必须是合法 JSON（非半写/交错）
        assert isinstance(data, list) and len(data) == 1, data
        leftovers = glob.glob(os.path.join(tmpdir, "sources.json.*.tmp"))
        assert leftovers == [], leftovers
    finally:
        config_manager.CONFIG_DIR = orig
        shutil.rmtree(tmpdir, ignore_errors=True)

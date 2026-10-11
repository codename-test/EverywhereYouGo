# tests/test_source_full_create.py
"""POST /api/sources/full（api/sources.py:62-157）行为测试。

断言只来自已实现的代码：
  - 入参校验走 api.validation.ValidationError，由 api/__init__.py:162-164 统一转成 400；
  - bindings 必须是非空可判定的 list（注意 data.get("bindings") or [] —— 空 dict 会被
    当成空列表放行，所以"非 list"的用例必须用非空值）；
  - parser_id / channel_id / template_id 必须是库里已存在的组件；
  - slug / port 冲突：先预检查（409），并发下唯一索引仍冲突则走 IntegrityError 回滚后 409；
    唯一索引见 db/schema.py:250-252（idx_sources_slug / idx_sources_port）；
  - 只 INSERT，不 upsert；源与绑定同一事务，失败整体 rollback；
  - 成功返回 201 {"id", "bindings", "status": "ok"}。
"""
import os
import sys
import json
import shutil
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_test_db_dir = tempfile.mkdtemp()
os.environ["EGO_SSL_ENABLED"] = "0"
TEST_TOKEN = "source-full-token"
AUTH_HEADER = {"Authorization": "Bearer " + TEST_TOKEN}

import db
import i18n

URL = "/api/sources/full"


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


def _reset_db():
    conn = db._conn()
    for t in ("source_channels", "channels", "sources", "templates", "parsers"):
        conn.execute("DELETE FROM %s" % t)
    conn.execute("INSERT INTO parsers (id,name,filename) VALUES (1,'p1','p1.py')")
    conn.execute("INSERT INTO channels (id,name,type,config,enabled) VALUES (1,'c1','fake','{}',1)")
    conn.execute("INSERT INTO templates (id,name,engine,content_tpl) "
                 "VALUES (1,'t1','plain','x')")
    conn.commit()


def _err(resp):
    try:
        return json.loads(resp.data)
    except Exception:
        return {}


class TestSourceFullCreate:
    @classmethod
    def setup_class(cls):
        db.init_db()
        from api import create_app
        cls.client = create_app().test_client()

    def setup_method(self):
        _reset_db()

    def _post(self, payload):
        return self.client.post(URL, data=json.dumps(payload),
                                content_type="application/json", headers=AUTH_HEADER)

    # ── 成功路径 ──

    def test_success_shape_is_201(self):
        r = self._post({"name": "src", "port": 9101, "slug": "src-slug", "parser_id": 1,
                        "bindings": [{"channel_id": 1, "template_id": 1, "priority": 2}]})
        assert r.status_code == 201, r.data[:200]
        d = _err(r)
        assert set(d) == {"id", "bindings", "status"}, d
        assert d["status"] == "ok", d
        assert isinstance(d["bindings"], list) and len(d["bindings"]) == 1, d

    def test_rows_actually_written(self):
        r = self._post({"name": "src", "slug": "src-slug", "parser_id": 1,
                        "bindings": [{"channel_id": 1, "template_id": 1}]})
        d = _err(r)
        sid = d["id"]
        assert db.get_source(sid) is not None, "源应已写入"
        rows = db.get_source_channels(sid)
        assert len(rows) == 1 and rows[0]["priority"] == 0, rows

    def test_insert_only_does_not_touch_existing_rows(self):
        conn = db._conn()
        conn.execute("INSERT INTO sources (id,name,slug,parser_id,enabled) "
                     "VALUES (77,'old','old-slug',1,1)")
        conn.commit()
        r = self._post({"name": "new", "slug": "new-slug", "parser_id": 1,
                        "bindings": [{"channel_id": 1, "template_id": 1}]})
        assert r.status_code == 201, r.data[:200]
        old = db.get_source(77)
        assert old and old["name"] == "old", "已有记录不应被覆盖：%s" % old
        assert db.get_source_by_slug("old-slug")["id"] == 77

    def test_single_transaction_one_commit(self):
        orig_conn_fn = db._conn
        real = db._conn()
        calls = []

        class _Spy:
            def __getattr__(self, name):
                return getattr(real, name)

            def execute(self, *a, **k):
                sql = a[0] if a else ""
                tag = "sources" if "INSERT INTO sources" in sql else \
                      "source_channels" if "INSERT INTO source_channels" in sql else "other"
                calls.append(("execute", tag))
                return real.execute(*a, **k)

            def commit(self):
                calls.append(("commit", None))
                return real.commit()

        db._conn = lambda: _Spy()
        try:
            r = self._post({"name": "src", "slug": "s", "parser_id": 1,
                            "bindings": [{"channel_id": 1, "template_id": 1},
                                         {"channel_id": 1, "template_id": 1}]})
        finally:
            db._conn = orig_conn_fn
        assert r.status_code == 201, r.data[:200]
        # 写事务：1 次源插入 + 2 次绑定插入 + 恰好 1 次 commit，commit 紧跟绑定之后
        assert calls[:4] == [("execute", "sources"), ("execute", "source_channels"),
                             ("execute", "source_channels"), ("commit", None)], calls
        # 之后只有 config_manager.sync_table 的只读 SELECT，绝不能再有第二次 commit（否则不是单事务）
        assert not any(c[0] == "commit" for c in calls[4:]), calls

    # ── 入参校验（400） ──

    def test_name_required(self):
        r = self._post({"slug": "s", "parser_id": 1})
        assert r.status_code == 400, r.data[:200]

    def test_parser_id_required(self):
        r = self._post({"name": "src", "slug": "s"})
        assert r.status_code == 400, r.data[:200]
        assert _err(r).get("error") == "parser_id is required", _err(r)

    def test_parser_id_nonexistent(self):
        r = self._post({"name": "src", "slug": "s", "parser_id": 999})
        assert r.status_code == 400, r.data[:200]
        assert "999 does not exist" in _err(r).get("error", ""), _err(r)

    def test_binding_channel_nonexistent(self):
        r = self._post({"name": "src", "slug": "s", "parser_id": 1,
                        "bindings": [{"channel_id": 999, "template_id": 1}]})
        assert r.status_code == 400, r.data[:200]
        assert "channel_id 999 does not exist" in _err(r).get("error", ""), _err(r)

    def test_binding_template_nonexistent(self):
        r = self._post({"name": "src", "slug": "s", "parser_id": 1,
                        "bindings": [{"channel_id": 1, "template_id": 999}]})
        assert r.status_code == 400, r.data[:200]
        assert "template_id 999 does not exist" in _err(r).get("error", ""), _err(r)

    def test_bindings_must_be_list(self):
        r = self._post({"name": "src", "slug": "s", "parser_id": 1,
                        "bindings": {"channel_id": 1, "template_id": 1}})
        assert r.status_code == 400, r.data[:200]
        assert _err(r).get("error") == "bindings must be a list", _err(r)

    def test_binding_item_must_be_object(self):
        r = self._post({"name": "src", "slug": "s", "parser_id": 1, "bindings": [1]})
        assert r.status_code == 400, r.data[:200]
        assert _err(r).get("error") == "bindings[0] must be a JSON object", _err(r)

    def test_validation_failure_writes_nothing(self):
        r = self._post({"name": "src", "slug": "s", "parser_id": 999,
                        "bindings": [{"channel_id": 1, "template_id": 1}]})
        assert r.status_code == 400, r.data[:200]
        assert db.get_source_by_slug("s") is None, "校验失败不应写入任何行"

    # ── 冲突（409） ──

    def test_slug_conflict_409(self):
        conn = db._conn()
        conn.execute("INSERT INTO sources (id,name,slug,parser_id,enabled) "
                     "VALUES (77,'old','dup-slug',1,1)")
        conn.commit()
        r = self._post({"name": "new", "slug": "dup-slug", "parser_id": 1})
        assert r.status_code == 409, r.data[:200]
        assert _err(r).get("error") == "slug already exists", _err(r)

    def test_port_conflict_409(self):
        conn = db._conn()
        conn.execute("INSERT INTO sources (id,name,port,parser_id,enabled) "
                     "VALUES (77,'old',9101,1,1)")
        conn.commit()
        r = self._post({"name": "new", "port": 9101, "parser_id": 1})
        assert r.status_code == 409, r.data[:200]
        # i18n._() 需要请求上下文（读 session 语言）；用 test_request_context 复现视图默认语言 zh
        with self.client.application.test_request_context():
            expected = i18n._("err.port_in_use")
        assert _err(r).get("error") == expected, _err(r)

    def test_subroute_path_conflict_409(self):
        """同一组下 path 重复（nginx 路径 /<prefix>/<slug>/<path> 最后一段）→ 409。"""
        conn = db._conn()
        conn.execute("INSERT INTO sources (id,name,slug,parser_id,enabled) "
                     "VALUES (77,'group','grp',1,1)")
        conn.execute("INSERT INTO sources (id,name,slug,parent_id,path,parser_id,enabled) "
                     "VALUES (78,'sub','sub-1',77,'movies',1,1)")
        conn.commit()
        r = self._post({"name": "new-sub", "parser_id": 1, "parent_id": 77, "path": "movies"})
        assert r.status_code == 409, r.data[:200]
        assert _err(r).get("error") == "path already exists under this source", _err(r)
        assert db.get_sub_route_by_path(77, "movies")["id"] == 78, "既有子路由不能被重复/覆盖"

    def test_listener_failure_rolls_back_and_500(self):
        """端口模式源监听启动失败 → 回滚刚建的源+绑定，返回 500（保留的必须可用）。"""
        class _FakeSM:
            def start_source(self, sid):
                return (False, "Address already in use")
        from api import create_app
        client = create_app(source_mgr=_FakeSM()).test_client()
        r = client.post(URL, data=json.dumps({
            "name": "src", "port": 9101, "slug": "lf-slug", "parser_id": 1,
            "bindings": [{"channel_id": 1, "template_id": 1}]}),
            content_type="application/json", headers=AUTH_HEADER)
        assert r.status_code == 500, r.data[:200]
        assert "listener failed to start" in _err(r).get("error", ""), _err(r)
        assert db.get_source_by_slug("lf-slug") is None, "监听失败应回滚源"
        assert db.get_all_source_channels() == [], "监听失败应回滚绑定"

    def test_subpath_unique_index_enforced(self):
        """DB 唯一索引 idx_sources_subpath 拦住绕过代码查重的重复 (parent_id,path)。"""
        conn = db._conn()
        conn.execute("INSERT INTO sources (id,name,slug,parser_id,enabled) VALUES (77,'g','grp',1,1)")
        conn.execute("INSERT INTO sources (id,name,slug,parent_id,path,parser_id,enabled) "
                     "VALUES (78,'s1','s1',77,'movies',1,1)")
        conn.commit()
        import sqlite3
        try:
            conn.execute("INSERT INTO sources (id,name,slug,parent_id,path,parser_id,enabled) "
                         "VALUES (79,'s2','s2',77,'movies',1,1)")
            conn.commit()
            assert False, "重复 (parent_id,path) 应被唯一索引拒绝"
        except sqlite3.IntegrityError:
            conn.rollback()

    def test_integrity_error_fallback_409_and_rollback(self):
        """绕过预检查，让唯一索引（db/schema.py:250-252）真正触发 IntegrityError。"""
        conn = db._conn()
        conn.execute("INSERT INTO sources (id,name,slug,parser_id,enabled) "
                     "VALUES (77,'old','dup-slug',1,1)")
        conn.commit()
        orig = db.get_source_by_slug
        db.get_source_by_slug = lambda s: None          # 预检查被绕过
        try:
            r = self._post({"name": "new", "slug": "dup-slug", "parser_id": 1,
                            "bindings": [{"channel_id": 1, "template_id": 1}]})
        finally:
            db.get_source_by_slug = orig
        assert r.status_code == 409, r.data[:200]
        assert _err(r).get("error") == "slug or port already exists", _err(r)
        assert db.get_source_by_slug("dup-slug")["id"] == 77, "既有行不能被覆盖"
        assert len(db.get_source_channels(77)) == 0, "回滚后不应留下绑定"

    def test_rollback_leaves_no_partial_rows(self):
        """绑定插入失败时整体回滚：源行也不能存在。"""
        orig_conn_fn = db._conn
        real = db._conn()

        class _Boom:
            def __getattr__(self, name):
                return getattr(real, name)

            def execute(self, sql, *a, **k):
                if "INSERT INTO source_channels" in sql:
                    raise RuntimeError("boom on binding insert")
                return real.execute(sql, *a, **k)

        db._conn = lambda: _Boom()
        try:
            r = self._post({"name": "src", "slug": "rb-slug", "parser_id": 1,
                            "bindings": [{"channel_id": 1, "template_id": 1}]})
        finally:
            db._conn = orig_conn_fn
        assert r.status_code == 500, r.data[:200]
        assert _err(r).get("error") == "boom on binding insert", _err(r)
        assert db.get_source_by_slug("rb-slug") is None, "源行应被回滚"
        assert db.get_all_source_channels() == [], "绑定应被回滚"

    # ── P1：创建子路由必须挂有效顶层父级 + 非空路径 ──

    def test_create_subroute_dangling_parent_400(self):
        r = self._post({"name": "sub", "parent_id": 999, "path": "p", "parser_id": 1,
                        "bindings": [{"channel_id": 1, "template_id": 1}]})
        assert r.status_code == 400, r.data[:200]
        assert "parent source does not exist" in _err(r).get("error", ""), r.data

    def test_create_subroute_empty_path_400(self):
        conn = db._conn()
        conn.execute("INSERT INTO sources (id,name,slug,parser_id,enabled) VALUES (77,'g','grp',1,1)")
        conn.commit()
        r = self._post({"name": "sub", "parent_id": 77, "path": "", "parser_id": 1,
                        "bindings": [{"channel_id": 1, "template_id": 1}]})
        assert r.status_code == 400, r.data[:200]
        assert "non-empty path" in _err(r).get("error", ""), r.data

    @classmethod
    def teardown_class(cls):
        shutil.rmtree(_test_db_dir, ignore_errors=True)

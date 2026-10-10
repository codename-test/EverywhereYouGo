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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_test_db_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_test_db_dir, "test_source_full.db")
os.environ["EGO_SSL_ENABLED"] = "0"
TEST_TOKEN = "source-full-token"
os.environ["EGO_AUTH_TOKEN"] = TEST_TOKEN
AUTH_HEADER = {"Authorization": "Bearer " + TEST_TOKEN}

import db
import i18n

URL = "/api/sources/full"


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
            db._conn = lambda: real
        assert r.status_code == 201, r.data[:200]
        assert calls == [("execute", "sources"), ("execute", "source_channels"),
                         ("execute", "source_channels"), ("commit", None)], calls

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
        assert _err(r).get("error") == i18n._("err.port_in_use"), _err(r)

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
            db._conn = lambda: real
        assert r.status_code == 500, r.data[:200]
        assert _err(r).get("error") == "boom on binding insert", _err(r)
        assert db.get_source_by_slug("rb-slug") is None, "源行应被回滚"
        assert db.get_all_source_channels() == [], "绑定应被回滚"

    @classmethod
    def teardown_class(cls):
        shutil.rmtree(_test_db_dir, ignore_errors=True)

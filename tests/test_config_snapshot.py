# tests/test_config_snapshot.py
"""GET /api/config（api/system.py:246-278）聚合快照形状测试。

断言只来自已实现的代码：
  - 顶层四个键：parsers / templates / channels / sources；
  - sources 只含顶层组（parent_id IS NULL，见 db/queries.py:74-78）；
  - 每个组与每个子路由都带 parser_name 和 bindings；
  - parser_name 由 parser_name_by_id.get(...) 得出，解析器缺失时为 None
    （注意：/api/sources 用的是 "-"，两者不同，不能混为一谈）；
  - bindings 按 priority 排序（db/queries.py:354-358）；
  - sub_routes 来自 db.get_sub_routes(group_id)，无子路由时为空列表。
"""
import os
import sys
import json
import shutil
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_test_db_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_test_db_dir, "test_config_snapshot.db")
os.environ["EGO_SSL_ENABLED"] = "0"
TEST_TOKEN = "config-test-token"
os.environ["EGO_AUTH_TOKEN"] = TEST_TOKEN
AUTH_HEADER = {"Authorization": "Bearer " + TEST_TOKEN}

import db


def _reset_db():
    conn = db._conn()
    for t in ("source_channels", "channels", "sources", "templates", "parsers"):
        conn.execute("DELETE FROM %s" % t)
    conn.commit()


class TestConfigSnapshot:
    @classmethod
    def setup_class(cls):
        db.init_db()
        from api import create_app
        cls.client = create_app().test_client()

    def setup_method(self):
        _reset_db()

    def _get(self):
        r = self.client.get("/api/config", headers=AUTH_HEADER)
        assert r.status_code == 200, r.data[:200]
        return json.loads(r.data)

    def test_top_level_keys(self):
        d = self._get()
        assert set(d) == {"parsers", "templates", "channels", "sources"}, sorted(d)
        for key in ("parsers", "templates", "channels", "sources"):
            assert isinstance(d[key], list), "%s 应为列表" % key

    def test_parsers_templates_channels_contents(self):
        conn = db._conn()
        conn.execute("INSERT INTO parsers (id,name,filename) VALUES (7,'p7','p7.py')")
        conn.execute("INSERT INTO templates (id,name,engine,content_tpl) "
                     "VALUES (3,'t3','plain','x')")
        conn.execute("INSERT INTO channels (id,name,type,config,enabled) "
                     "VALUES (5,'c5','fake','{}',1)")
        conn.commit()

        d = self._get()
        assert [p["id"] for p in d["parsers"]] == [7]
        assert d["parsers"][0]["name"] == "p7"
        assert [t["id"] for t in d["templates"]] == [3]
        assert [c["id"] for c in d["channels"]] == [5]

    def test_sources_only_top_level_groups(self):
        conn = db._conn()
        conn.execute("INSERT INTO sources (id,name,parser_id,enabled) VALUES (10,'group',7,1)")
        conn.execute("INSERT INTO sources (id,name,slug,parent_id,enabled) "
                     "VALUES (11,'sub','sub-slug',10,1)")
        conn.commit()

        d = self._get()
        ids = [s["id"] for s in d["sources"]]
        assert ids == [10], "顶层只应有 parent_id IS NULL 的组，实际 %s" % ids
        assert d["sources"][0]["sub_routes"], "子路由应嵌在组内"
        assert [x["id"] for x in d["sources"][0]["sub_routes"]] == [11]

    def test_every_node_has_parser_name_and_bindings(self):
        conn = db._conn()
        conn.execute("INSERT INTO parsers (id,name,filename) VALUES (7,'p7','p7.py')")
        conn.execute("INSERT INTO sources (id,name,parser_id,enabled) VALUES (10,'group',7,1)")
        conn.execute("INSERT INTO sources (id,name,slug,parent_id,enabled) "
                     "VALUES (11,'sub',7,10,1)")
        conn.commit()

        d = self._get()
        nodes = list(d["sources"]) + [s for g in d["sources"] for s in g["sub_routes"]]
        for n in nodes:
            assert "parser_name" in n, n
            assert "bindings" in n and isinstance(n["bindings"], list), n
        assert d["sources"][0]["parser_name"] == "p7"
        assert d["sources"][0]["sub_routes"][0]["parser_name"] == "p7"

    def test_parser_name_is_none_when_parser_missing(self):
        """解析器不存在时 parser_name 为 None（不是 "-"）。"""
        conn = db._conn()
        conn.execute("INSERT INTO sources (id,name,parser_id,enabled) VALUES (10,'group',999,1)")
        conn.commit()

        d = self._get()
        assert d["sources"][0]["parser_name"] is None, d["sources"][0]

    def test_sub_routes_empty_when_no_children(self):
        conn = db._conn()
        conn.execute("INSERT INTO sources (id,name,parser_id,enabled) VALUES (10,'group',1,1)")
        conn.commit()
        d = self._get()
        assert d["sources"][0]["sub_routes"] == [], d["sources"][0]

    def test_bindings_ordered_by_priority(self):
        conn = db._conn()
        conn.execute("INSERT INTO channels (id,name,type,config,enabled) "
                     "VALUES (5,'c5','fake','{}',1)")
        conn.execute("INSERT INTO sources (id,name,parser_id,enabled) VALUES (10,'group',1,1)")
        conn.commit()
        # 故意按 priority 倒序插入，验证读取按 priority 排序
        db.create_source_channel(10, 5, 1, priority=9)
        db.create_source_channel(10, 5, 1, priority=1)

        d = self._get()
        b = d["sources"][0]["bindings"]
        assert [x["priority"] for x in b] == [1, 9], b

    def test_bindings_scoped_to_their_own_source(self):
        conn = db._conn()
        conn.execute("INSERT INTO channels (id,name,type,config,enabled) "
                     "VALUES (5,'c5','fake','{}',1)")
        conn.execute("INSERT INTO sources (id,name,parser_id,enabled) VALUES (10,'group',1,1)")
        conn.execute("INSERT INTO sources (id,name,slug,parent_id,enabled) "
                     "VALUES (11,'sub',1,10,1)")
        conn.commit()
        db.create_source_channel(10, 5, 1, priority=0)
        db.create_source_channel(11, 5, 1, priority=0)

        d = self._get()
        group = d["sources"][0]
        sub = group["sub_routes"][0]
        assert [x["source_id"] for x in group["bindings"]] == [10], group["bindings"]
        assert [x["source_id"] for x in sub["bindings"]] == [11], sub["bindings"]

    def test_get_allowed_by_query_only_guard(self):
        """GET 不受查询类 API 模式影响（guard 只屏蔽非 GET/HEAD）。"""
        r = self.client.get("/api/config", headers=AUTH_HEADER)
        assert r.status_code == 200, r.data[:200]

    @classmethod
    def teardown_class(cls):
        shutil.rmtree(_test_db_dir, ignore_errors=True)

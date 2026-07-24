# tests/test_source_cascade.py
"""delete_source 级联删除测试：删数据源时同步清理子路由与通道绑定。"""
import sys
import os
import tempfile
import shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# 使用临时数据库
_test_db_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_test_db_dir, "test_ego.db")

import db


class TestDeleteSourceCascade:
    """delete_source 级联删除测试。"""

    @classmethod
    def setup_class(cls):
        db.init_db()

    @classmethod
    def teardown_class(cls):
        shutil.rmtree(_test_db_dir, ignore_errors=True)

    def setup_method(self):
        conn = db._conn()
        conn.execute("DELETE FROM source_channels")
        conn.execute("DELETE FROM sources")
        conn.commit()

    def _mk_group_with_subs(self):
        """建一个路径组 + 两个子路由，返回 (组id, 子路由1id, 子路由2id)。"""
        gid = db.create_source(name="G", slug="g")
        s1 = db.create_source(name="S1", parent_id=gid, path="")
        s2 = db.create_source(name="S2", parent_id=gid, path="movie")
        return gid, s1, s2

    def test_delete_group_removes_sub_routes(self):
        """删除路径组后，其子路由一并被删除。"""
        gid, s1, s2 = self._mk_group_with_subs()
        db.delete_source(gid)
        conn = db._conn()
        assert conn.execute("SELECT COUNT(*) FROM sources WHERE id=?",
                            (gid,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM sources WHERE parent_id=?",
                            (gid,)).fetchone()[0] == 0

    def test_delete_group_removes_bindings(self):
        """删除路径组后，组与子路由的通道绑定全部被清理。"""
        gid, s1, s2 = self._mk_group_with_subs()
        db.create_source_channel(gid, 1, 1)
        db.create_source_channel(s1, 1, 1)
        db.create_source_channel(s2, 1, 1)
        db.delete_source(gid)
        n = db._conn().execute(
            "SELECT COUNT(*) FROM source_channels WHERE source_id IN (?,?,?)",
            (gid, s1, s2)).fetchone()[0]
        assert n == 0

    def test_delete_leaf_keeps_siblings(self):
        """删除单个子路由不影响组和兄弟子路由。"""
        gid, s1, s2 = self._mk_group_with_subs()
        db.delete_source(s1)
        conn = db._conn()
        assert conn.execute("SELECT COUNT(*) FROM sources WHERE id=?",
                            (s1,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM sources WHERE id IN (?,?)",
                            (gid, s2)).fetchone()[0] == 2

    def test_delete_leaves_no_orphans(self):
        """删除后不留下 parent_id 悬空的孤儿记录。"""
        gid, s1, s2 = self._mk_group_with_subs()
        db.delete_source(gid)
        orphans = db._conn().execute(
            "SELECT COUNT(*) FROM sources WHERE parent_id IS NOT NULL "
            "AND parent_id NOT IN (SELECT id FROM sources)").fetchone()[0]
        assert orphans == 0

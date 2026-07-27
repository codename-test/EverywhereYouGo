# tests/test_backup_hardening.py
"""备份恢复加固测试：路径穿越防护（#22）+ ZIP 炸弹防护（#32）。"""
import sys
import os
import io
import zipfile
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# 使用临时数据库，避免污染真实库
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test_backup.db")

import db
db.init_db()

from api.backup import _safe_filename
from api import create_app


class TestSafeFilename:
    """_safe_filename 文件名校验。"""

    def test_normal_json(self):
        assert _safe_filename("sources.json") is True

    def test_normal_py(self):
        assert _safe_filename("emby.py") is True

    def test_reject_dotdot(self):
        assert _safe_filename("..") is False
        assert _safe_filename("..json") is False

    def test_reject_separator(self):
        assert _safe_filename("a/b.json") is False
        assert _safe_filename("a\\b.json") is False

    def test_reject_hidden(self):
        assert _safe_filename(".hidden") is False

    def test_reject_empty(self):
        assert _safe_filename("") is False


def _make_zip(entries):
    """entries: {arcname: content_bytes} → BytesIO zip。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    buf.seek(0)
    return buf


class TestRestore:
    """通过 Flask 测试客户端验证 /api/restore 加固。"""

    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.config_dir = os.path.join(self.tmp, "config")
        self.parsers_dir = os.path.join(self.tmp, "parsers")
        os.makedirs(self.config_dir)
        os.makedirs(self.parsers_dir)
        self.app = create_app(source_mgr=None)
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _patch_dirs(self, monkeypatch):
        import api.backup as bk
        import config_manager
        monkeypatch.setattr(bk, "PARSERS_DIR", self.parsers_dir)
        # /api/restore 在函数内 `from config_manager import CONFIG_DIR`，
        # 调用时才绑定，故 patch 源模块属性即可生效。
        monkeypatch.setattr(config_manager, "CONFIG_DIR", self.config_dir)

    def test_traversal_is_flattened(self, monkeypatch):
        """恶意条目 'config/../../evil.json' 只能被压平写入 config 目录，无法逃逸。"""
        self._patch_dirs(monkeypatch)
        zip_buf = _make_zip({
            "config/../../evil.json": b"[]",
            "config/ok.json": b"[]",
        })
        resp = self.client.post(
            "/api/restore",
            data={"file": (zip_buf, "b.zip")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        # evil.json 被 basename 压平后落在 config_dir 内
        assert os.path.isfile(os.path.join(self.config_dir, "evil.json"))
        assert os.path.isfile(os.path.join(self.config_dir, "ok.json"))
        # 沙箱外的上级目录绝不会出现该文件
        assert not os.path.isfile(os.path.join(self.tmp, "evil.json"))
        assert not os.path.isfile(os.path.join(os.path.dirname(self.tmp), "evil.json"))

    def test_zip_bomb_rejected(self, monkeypatch):
        """解压总大小超过 10MB 上限时整体拒绝，且不写入任何文件。"""
        self._patch_dirs(monkeypatch)
        big = b"0" * (6 * 1024 * 1024)  # 高可压缩，zip 很小但 file_size=6MB
        zip_buf = _make_zip({
            "config/a.json": big,
            "config/b.json": big,  # 合计 12MB > 10MB
        })
        resp = self.client.post(
            "/api/restore",
            data={"file": (zip_buf, "b.zip")},
            content_type="multipart/form-data",
        )
        data = resp.get_json()
        assert data["ok"] is False
        assert not os.path.isfile(os.path.join(self.config_dir, "a.json"))
        assert not os.path.isfile(os.path.join(self.config_dir, "b.json"))

    def test_normal_restore_ok(self, monkeypatch):
        """合法扁平备份正常恢复。"""
        self._patch_dirs(monkeypatch)
        zip_buf = _make_zip({
            "config/channels.json": b"[]",
            "parsers/myparser.py": b"# parser\n",
        })
        resp = self.client.post(
            "/api/restore",
            data={"file": (zip_buf, "b.zip")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        assert os.path.isfile(os.path.join(self.config_dir, "channels.json"))
        assert os.path.isfile(os.path.join(self.parsers_dir, "myparser.py"))

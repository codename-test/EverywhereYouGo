# tests/test_backup_hardening.py
"""备份恢复加固测试：路径穿越防护（#22）+ ZIP 炸弹防护（#32）。"""
import sys
import os
import io
import zipfile
import tempfile
import json
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
        import plugin_paths
        # 插件目录拆分后，恢复写入走 plugin_paths 解析出的**用户目录**
        # （内置目录随镜像发布，不参与恢复），所以 patch 这一层。
        monkeypatch.setattr(plugin_paths, "PARSERS_USER", self.parsers_dir)
        monkeypatch.setattr(plugin_paths, "CHANNELS_USER",
                            os.path.join(self.tmp, "channels"))
        # 保留对 bk 常量的 patch，兼容仍在直接用它的代码路径
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

class TestRestoreReload:
    """v1.3.1 改进清单 #1：Restore 后应重载插件代码，刷新运行中缓存。"""

    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.config_dir = os.path.join(self.tmp, "config")
        self.parsers_dir = os.path.join(self.tmp, "parsers")
        self.channels_dir = os.path.join(self.tmp, "channels")
        os.makedirs(self.config_dir)
        os.makedirs(self.parsers_dir)
        os.makedirs(self.channels_dir)
        self.app = create_app(source_mgr=None)
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _patch_dirs(self, monkeypatch):
        import api.backup as bk
        import config_manager
        import plugin_paths
        monkeypatch.setattr(plugin_paths, "PARSERS_USER", self.parsers_dir)
        monkeypatch.setattr(plugin_paths, "CHANNELS_USER", self.channels_dir)
        monkeypatch.setattr(bk, "PARSERS_DIR", self.parsers_dir)
        monkeypatch.setattr(config_manager, "CONFIG_DIR", self.config_dir)

    def _make_zip(self, entries):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, data in entries.items():
                if isinstance(data, str):
                    data = data.encode("utf-8")
                zf.writestr(name, data)
        buf.seek(0)
        return buf

    def _restore(self, monkeypatch, entries):
        self._patch_dirs(monkeypatch)
        buf = self._make_zip(entries)
        resp = self.client.post(
            "/api/restore", data={"file": (buf, "b.zip")},
            content_type="multipart/form-data")
        assert resp.status_code == 200, resp.get_data()
        return resp.get_json()

    def _parser_code(self, tag):
        return f"def parse(b, h, q):\n    return {{'v': '{tag}'}}\n"

    def test_restore_reload_parser(self, monkeypatch):
        """恢复新版解析器后，run_parser 应读到新版而非运行中旧缓存。"""
        import parser_loader
        import db

        self._patch_dirs(monkeypatch)
        db.create_parser("my", "my.py", "")
        with open(os.path.join(self.parsers_dir, "my.py"), "w", encoding="utf-8") as f:
            f.write(self._parser_code("OLD"))
        parser_loader.load_parser("my.py")

        before = parser_loader.run_parser("my.py", b"", {}, {})
        assert before["v"] == "OLD", "运行中缓存应先读旧版"

        data = self._restore(monkeypatch, {"parsers/my.py": self._parser_code("NEW")})
        assert data["ok"] is True

        after = parser_loader.run_parser("my.py", b"", {}, {})
        assert after["v"] == "NEW", "restore 后缓存未刷新"

    def test_restore_reload_channel(self, monkeypatch):
        """恢复新版通道插件后，create_channel 应实例化新版。"""
        import channel_loader
        import db

        self._patch_dirs(monkeypatch)
        ch_src_old = (
            "from channel_base import BaseChannel\n"
            "class Channel(BaseChannel):\n"
            "    CHANNEL_TYPE = 'mytest_channel'\n"
            "    CHANNEL_NAME = 'MyTest'\n"
            "    def send(self, title, content):\n"
            "        return (True, 'OLD')\n"
            "    def test(self):\n"
            "        return True\n"
        )
        with open(os.path.join(self.channels_dir, "mytest_channel.py"), "w", encoding="utf-8") as f:
            f.write(ch_src_old)
        channel_loader.load_plugin("mytest_channel.py")
        assert channel_loader.load_plugin("mytest_channel.py").Channel("x").send("t", "c") == (True, "OLD")

        ch_src_new = ch_src_old.replace("'OLD'", "'NEW'")
        self._restore(monkeypatch, {"channels/mytest_channel.py": ch_src_new})

        ch = channel_loader.load_plugin("mytest_channel.py").Channel("x")
        assert ch.send("t", "c") == (True, "NEW"), "restore 后通道缓存未刷新"



class TestExportMasking:
    """v1.3.1 改进清单 #4: Export 脱敏敏感字段（密码/授权码/token）。"""

    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.config_dir = os.path.join(self.tmp, "config")
        os.makedirs(self.config_dir)
        os.environ["DB_PATH"] = os.path.join(self.tmp, "db.db")
        db.init_db()
        import api
        self.app = api.create_app(source_mgr=None)
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        db.create_channel("mail", "smtp",
                          '{"host":"x","password":"secret123","username":"a@b","port":465,"token":"tok"}', 1)

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_export_all_masks_sensitive(self):
        r = self.client.get("/api/export/all")
        data = r.get_json()
        cfg = data["channels"][0]["config"]
        assert cfg["password"] == "***", cfg
        assert cfg["token"] == "***", cfg
        assert cfg["username"] == "a@b", "非敏感字段应保留"
        assert cfg["host"] == "x"

    def test_export_single_channel_masks(self):
        r = self.client.get("/api/export/channel/1")
        assert r.status_code == 200
        data = r.get_json()
        assert data["config"]["password"] == "***"

    def test_backup_zip_keeps_full_config(self, monkeypatch):
        """Backup 拷贝 config/ 文件（channels.json 含完整凭据），不脱敏。"""
        import config_manager
        monkeypatch.setattr(config_manager, "CONFIG_DIR", self.config_dir)
        # 模拟应用启动时从 DB 同步的**完整** channels.json
        channels = [
            {"id": 1, "name": "mail", "type": "smtp",
             "config": json.dumps({"host": "x", "password": "secret123",
                                   "username": "a@b", "port": 465, "token": "tok"}),
             "enabled": 1, "created_at": "t"}
        ]
        with open(os.path.join(self.config_dir, "channels.json"), "w", encoding="utf-8") as f:
            json.dump(channels, f, ensure_ascii=False)
        r = self.client.get("/api/backup")
        assert r.status_code == 200
        zf = zipfile.ZipFile(io.BytesIO(r.data))
        raw = zf.read("config/channels.json")
        channels_out = json.loads(raw)
        cfg = channels_out[0]["config"]
        if isinstance(cfg, str):
            cfg = json.loads(cfg)
        assert cfg["password"] == "secret123", "backup 应保留完整密码（不脱敏）"
        assert cfg["token"] == "tok"
        assert cfg["username"] == "a@b"

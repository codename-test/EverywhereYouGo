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

import config_manager

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


_CORE_NAMES = ["config/%s" % fn for fn in config_manager._CONFIG_FILES.values()]


def _core_config(overrides=None):
    """5 个核心快照文件的 ZIP 条目（恢复要求齐全，v1.3.2 review #9）。

    默认每个文件 b"[]"（空表）；overrides 用 "config/xxx.json" → bytes 覆盖。
    """
    entries = {n: b"[]" for n in _CORE_NAMES}
    if overrides:
        entries.update(overrides)
    return entries


def _use_temp_db(tmp_dir):
    """把当前线程的 DB 切到 tmp_dir 下的独立库，返回原 DB_PATH。

    v1.3.2 review #8 起，/api/restore 会**真正**执行「JSON → SQLite 全量替换」。
    而所有测试文件共用同一个 DB_PATH（各自模块级 `os.environ["DB_PATH"]=...`
    只有第一个 import db 的生效），因此 restore 相关测试必须用独立库，
    否则会清空共享测试库、污染后续测试文件（曾导致 test_event_chain 9 个失败）。
    """
    import db as _db
    import db.connection as _dbconn

    old_path = _dbconn.DB_PATH
    conn = getattr(_dbconn._local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        try:
            del _dbconn._local.conn
        except AttributeError:
            pass
    _dbconn.DB_PATH = os.path.join(tmp_dir, "isolated.db")
    _db.init_db()
    return old_path


def _restore_db(old_path):
    """把 DB_PATH 切回原值（配合 _use_temp_db）。"""
    import db.connection as _dbconn
    conn = getattr(_dbconn._local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        try:
            del _dbconn._local.conn
        except AttributeError:
            pass
    _dbconn.DB_PATH = old_path


class TestRestore:
    """通过 Flask 测试客户端验证 /api/restore 加固。"""

    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.config_dir = os.path.join(self.tmp, "config")
        self.parsers_dir = os.path.join(self.tmp, "parsers")
        os.makedirs(self.config_dir)
        os.makedirs(self.parsers_dir)
        # restore 会重建 DB（review #8）→ 用独立库，避免污染共享测试库
        self._old_db = _use_temp_db(self.tmp)
        self.app = create_app(source_mgr=None)
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def teardown_method(self):
        _restore_db(self._old_db)
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
        zip_buf = _make_zip(_core_config({
            "config/../../evil.json": b"[]",
            "config/ok.json": b"[]",
        }))
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
        zip_buf = _make_zip(_core_config({
            "config/channels.json": b"[]",
            "parsers/myparser.py": b"def parse(b, h, q):\n    return {'v': 'ok'}\n",
        }))
        resp = self.client.post(
            "/api/restore",
            data={"file": (zip_buf, "b.zip")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        assert os.path.isfile(os.path.join(self.config_dir, "channels.json"))
        assert os.path.isfile(os.path.join(self.parsers_dir, "myparser.py"))


    def test_atomic_restore_rollback_on_fail(self, monkeypatch):
        """#9: 校验失败 → 原文件不动、无半成功（无新文件写入）。"""
        self._patch_dirs(monkeypatch)
        NL = chr(10)
        valid = "def parse(b, h, q):" + NL + "    return {'v': 'OLD'}" + NL
        with open(os.path.join(self.parsers_dir, "my.py"), "w", encoding="utf-8") as f:
            f.write(valid)
        bad = "def parse(b, h, q):" + NL + "    return {  # unclosed"
        zip_buf = _make_zip({"parsers/bad.py": bad.encode("utf-8")})
        resp = self.client.post(
            "/api/restore", data={"file": (zip_buf, "b.zip")},
            content_type="multipart/form-data"
        )
        data = resp.get_json()
        assert data["ok"] is False, data
        assert "恢复校验失败" in data["error"], data
        assert os.path.isfile(os.path.join(self.parsers_dir, "my.py"))
        with open(os.path.join(self.parsers_dir, "my.py"), encoding="utf-8") as f:
            assert f.read() == valid, "原文件被破坏"
        assert not os.path.isfile(os.path.join(self.parsers_dir, "bad.py"))

    def test_atomic_restore_rollback_config_fail(self, monkeypatch):
        """#9: 配置 JSON 解析失败 → 所有文件都不写。"""
        self._patch_dirs(monkeypatch)
        ok_src = "def parse(b, h, q):" + chr(10) + "    return {'v': 'x'}" + chr(10)
        zip_buf = _make_zip({
            "config/channels.json": b"[{bad json",
            "parsers/ok.py": ok_src.encode("utf-8"),
        })
        resp = self.client.post(
            "/api/restore", data={"file": (zip_buf, "b.zip")},
            content_type="multipart/form-data"
        )
        data = resp.get_json()
        assert data["ok"] is False, data
        assert not os.path.isfile(os.path.join(self.config_dir, "channels.json"))
        assert not os.path.isfile(os.path.join(self.parsers_dir, "ok.py"))

    def test_restore_config_overwrites_db(self, monkeypatch):
        """#8: Restore 的配置必须真正覆盖 DB（JSON → DB），而不是被 DB 反刷回去。

        回归场景：当前 DB 有通道 A，备份 ZIP 里是通道 B。
        修复前 restore 调 load_all() → DB 非空 → 以 DB 为准把 JSON 刷回，
        结果是 B 被 A 覆盖，"配置恢复"变成 no-op（只有插件文件真恢复）。
        """
        import db as _db
        self._patch_dirs(monkeypatch)
        # 当前 DB：一个只存在于 DB 的通道
        _db.create_channel("db-only", "wechat_work_bot", "{}", 1)
        assert [c["name"] for c in _db.get_channels()] == ["db-only"]

        # 备份：channels.json 里是一个不同的通道
        backup_channels = [{"id": 77, "name": "from-backup",
                            "type": "wechat_work_bot", "config": "{}", "enabled": 1}]
        zip_buf = _make_zip(_core_config({
            "config/channels.json": json.dumps(backup_channels).encode("utf-8")}))
        resp = self.client.post("/api/restore",
                                data={"file": (zip_buf, "b.zip")},
                                content_type="multipart/form-data")
        data = resp.get_json()
        assert data["ok"] is True, data
        assert "config_imported" in data, "恢复应报告配置导入结果: %s" % data

        names = [c["name"] for c in _db.get_channels()]
        assert names == ["from-backup"], "备份配置未覆盖 DB：%s" % names

    def test_restore_restarts_listeners(self, monkeypatch):
        """#8: 配置按备份重建后，必须重启 source listener（端口/绑定随新配置）。

        注意 API 侧读的是 `current_app.source_mgr`（Flask app 属性）——
        main.py 曾经只设模块级 `web_ui.source_mgr`，导致这里恒为 None、
        重启监听是空操作。
        """
        self._patch_dirs(monkeypatch)
        calls = []

        class _FakeSM:
            def stop_all(self):
                calls.append("stop_all")

            def start_all(self):
                calls.append("start_all")

        app = create_app(source_mgr=_FakeSM())
        app.config["TESTING"] = True
        client = app.test_client()
        zip_buf = _make_zip(_core_config())
        resp = client.post("/api/restore",
                           data={"file": (zip_buf, "b.zip")},
                           content_type="multipart/form-data")
        data = resp.get_json()
        assert data["ok"] is True, data
        assert data.get("listeners_restarted") is True, data
        assert calls == ["stop_all", "start_all"], calls

    def test_restore_preserves_created_at(self, monkeypatch):
        """恢复应还原备份里的 created_at，而不是落成「恢复时刻」。

        回归：导入 INSERT 原先不带 created_at，恢复一次备份会让所有
        解析器/通道/数据源/模板的创建时间都变成恢复那一刻。
        """
        import db as _db
        self._patch_dirs(monkeypatch)
        stamp = "2020-01-02 03:04:05"
        backup = [{"id": 7, "name": "c7", "type": "wechat_work_bot",
                   "config": "{}", "enabled": 1, "created_at": stamp}]
        zip_buf = _make_zip(_core_config({
            "config/channels.json": json.dumps(backup).encode("utf-8")}))
        resp = self.client.post("/api/restore",
                                data={"file": (zip_buf, "b.zip")},
                                content_type="multipart/form-data")
        assert resp.get_json()["ok"] is True
        rows = {c["name"]: c for c in _db.get_channels()}
        assert rows["c7"]["created_at"] == stamp, rows["c7"]["created_at"]

        # 老备份缺 created_at → 回落 CURRENT_TIMESTAMP，不报错
        legacy = [{"id": 8, "name": "c8", "type": "wechat_work_bot",
                   "config": "{}", "enabled": 1}]
        zip2 = _make_zip(_core_config({
            "config/channels.json": json.dumps(legacy).encode("utf-8")}))
        resp2 = self.client.post("/api/restore",
                                 data={"file": (zip2, "b.zip")},
                                 content_type="multipart/form-data")
        assert resp2.get_json()["ok"] is True
        rows2 = {c["name"]: c for c in _db.get_channels()}
        assert rows2["c8"]["created_at"], "缺字段应回落 CURRENT_TIMESTAMP"

    def test_incomplete_backup_restores_partial(self, monkeypatch):
        """#9: 缺核心文件的 ZIP **不拒绝** —— 有什么恢复什么，未包含的表保持原样。

        触发场景：用户手搓/截断的 ZIP 只带了 channels.json + templates.json。
        旧行为是"缺失 = 空配置"，会把 parsers / sources / bindings 全清掉。
        """
        import db as _db
        self._patch_dirs(monkeypatch)
        _db.create_channel("db-only", "wechat_work_bot", "{}", 1)
        assert _db.create_source("keep-src", 25999, None, 1), "前置条件：建源失败"

        # 备份只含 channels.json（+ 默认的 templates.json 等被删掉）
        backup_channels = [{"id": 77, "name": "from-backup",
                            "type": "wechat_work_bot", "config": "{}", "enabled": 1}]
        partial = _core_config({
            "config/channels.json": json.dumps(backup_channels).encode("utf-8")})
        for missing in ("config/parsers.json", "config/sources.json",
                        "config/bindings.json"):
            del partial[missing]

        resp = self.client.post("/api/restore",
                                data={"file": (_make_zip(partial), "b.zip")},
                                content_type="multipart/form-data")
        data = resp.get_json()
        assert data["ok"] is True, data
        # 有提示，且指明未包含的文件
        assert data["warnings"], data
        assert any("sources.json" in w for w in data["warnings"]), data
        assert set(data["config_skipped"]) == {"parsers", "sources", "bindings"}, data

        # 备份里有的 → 按备份替换
        assert [c["name"] for c in _db.get_channels()] == ["from-backup"]
        # 备份里没有的 → 原样保留，没有被清空
        assert [x["name"] for x in _db.get_sources()] == ["keep-src"],             "未包含在备份里的表不应被清空"

    def test_partial_restore_ignores_stale_on_disk_file(self, monkeypatch):
        """缺文件必须按 **ZIP 内容**判断，不能按磁盘上有没有。

        config/ 目录里通常已经存在上一轮导出留下的 parsers.json（陈旧）。
        若按磁盘判断，ZIP 没带 parsers.json 也会被当成"带了"→ 把陈旧内容导入。
        """
        import db as _db
        self._patch_dirs(monkeypatch)
        # 磁盘上放一个陈旧的 parsers.json（模拟上一轮的导出残留）
        with open(os.path.join(self.config_dir, "parsers.json"),
                  "w", encoding="utf-8") as f:
            json.dump([{"id": 1, "name": "stale", "filename": "stale.py"}], f)
        _db.create_parser("real", "real.py", "")

        partial = _core_config()
        del partial["config/parsers.json"]          # ZIP 不含 parsers.json
        resp = self.client.post("/api/restore",
                                data={"file": (_make_zip(partial), "b.zip")},
                                content_type="multipart/form-data")
        data = resp.get_json()
        assert data["ok"] is True, data
        assert "parsers" in data["config_skipped"], data
        assert "parsers" not in data["config_imported"], data
        # parsers 表保持原样，没被磁盘上的陈旧文件覆盖
        names = [x["filename"] for x in _db.get_parsers()]
        assert "real.py" in names, names
        assert "stale.py" not in names, \
            "磁盘上的陈旧 parsers.json 不应被导入：%s" % names

    def test_incomplete_backup_warns_in_dry_run(self, monkeypatch):
        """#10: dry-run 也提示备份不完整（但仍说"可以恢复"）。"""
        self._patch_dirs(monkeypatch)
        partial = _core_config()
        del partial["config/sources.json"]
        resp = self.client.post("/api/restore?dry_run=1",
                                data={"file": (_make_zip(partial), "b.zip")},
                                content_type="multipart/form-data")
        data = resp.get_json()
        assert data["dry_run"] is True
        assert data["ok"] is True, data
        assert any("sources.json" in w for w in data["warnings"]), data
        assert data["errors"] == [], data

    def test_partial_restore_prunes_dangling_bindings(self, monkeypatch):
        """部分恢复后，指向已消失对象的绑定要被清掉（否则路由反复匹配失败）。"""
        import db as _db
        self._patch_dirs(monkeypatch)
        cid = _db.create_channel("old-ch", "wechat_work_bot", "{}", 1)
        sid = _db.create_source("s1", 25998, None, 1)
        assert _db.create_source_channel(sid, cid, 1), "前置条件：建绑定失败"
        assert len(_db.get_source_channels(sid)) == 1

        # 备份里 channels 换成另一个通道 → 旧绑定成为孤儿
        backup_channels = [{"id": 999, "name": "new-ch",
                            "type": "wechat_work_bot", "config": "{}", "enabled": 1}]
        payload = _core_config({
            "config/channels.json": json.dumps(backup_channels).encode("utf-8")})
        resp = self.client.post("/api/restore",
                                data={"file": (_make_zip(payload), "b.zip")},
                                content_type="multipart/form-data")
        assert resp.get_json()["ok"] is True
        assert _db.get_source_channels(sid) == [], "孤儿绑定应被清理"

    def test_dry_run_validates_without_writing(self, monkeypatch):
        """#10: dry-run 做真实校验（含插件可加载），但一个字节都不写。"""
        self._patch_dirs(monkeypatch)
        NL = chr(10)
        bad = "def parse(b, h, q):" + NL + "    return {  # unclosed"
        payload = _core_config({"parsers/bad.py": bad.encode("utf-8")})
        resp = self.client.post("/api/restore?dry_run=1",
                                data={"file": (_make_zip(payload), "b.zip")},
                                content_type="multipart/form-data")
        data = resp.get_json()
        assert data["ok"] is False, data
        assert any("bad.py" in e for e in data["errors"]), data
        assert not os.path.isfile(os.path.join(self.parsers_dir, "bad.py"))

    def test_dry_run_ok_reports_staged(self, monkeypatch):
        """#10: 校验通过时 dry-run 回报将要写入的文件，errors 为空。"""
        self._patch_dirs(monkeypatch)
        resp = self.client.post("/api/restore?dry_run=1",
                                data={"file": (_make_zip(_core_config()), "b.zip")},
                                content_type="multipart/form-data")
        data = resp.get_json()
        assert data["ok"] is True, data
        assert data["errors"] == [], data
        assert set(data["staged"]["config"]) == {
            "parsers.json", "sources.json", "channels.json",
            "templates.json", "bindings.json"}, data["staged"]

    def test_bad_json_shape_rejected_at_staging(self, monkeypatch):
        """#9: 配置结构非法要在**替换文件之前**拒绝。

        旧实现只在导入时 log.warning，形状错要等 KeyError 才炸 ——
        而那时插件文件已经替换完了，会留下"插件是新版、配置没恢复"的半成品。
        """
        self._patch_dirs(monkeypatch)
        NL = chr(10)
        ok_src = ("def parse(b, h, q):" + NL + "    return {'v': 'x'}" + NL).encode()
        # channels.json 是 dict 而非 list
        payload = _core_config({"config/channels.json": b'{"a": 1}',
                                "parsers/ok.py": ok_src})
        resp = self.client.post("/api/restore",
                                data={"file": (_make_zip(payload), "b.zip")},
                                content_type="multipart/form-data")
        data = resp.get_json()
        assert data["ok"] is False, data
        assert any("channels.json" in e for e in data["errors"]), data
        assert not os.path.isfile(os.path.join(self.parsers_dir, "ok.py")),             "结构校验失败时不应写入任何插件文件"

    def test_oversized_upload_rejected_413(self):
        """#11: 超过 MAX_CONTENT_LENGTH 的上传在 HTTP 层被拒（413），不进入视图。"""
        app = create_app(source_mgr=None)
        app.config["TESTING"] = True
        app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024      # 1MB，便于构造
        client = app.test_client()
        big = b"0" * (2 * 1024 * 1024)
        resp = client.post("/api/restore",
                           data={"file": (io.BytesIO(big), "big.zip")},
                           content_type="multipart/form-data")
        assert resp.status_code == 413, resp.status_code
        assert "error" in resp.get_json()

    def test_backup_upload_limit_before_read(self, monkeypatch):
        """#11: 备份上传超过 MAX_BACKUP_UPLOAD_SIZE → 早于 file.read() 被拒。"""
        import api.backup as bk
        monkeypatch.setattr(bk, "MAX_BACKUP_UPLOAD_SIZE", 2 * 1024 * 1024)
        app = create_app(source_mgr=None)
        app.config["TESTING"] = True
        client = app.test_client()
        payload = b"0" * (3 * 1024 * 1024)                  # 3MB > 2MB
        resp = client.post("/api/restore",
                           data={"file": (io.BytesIO(payload), "b.zip")},
                           content_type="multipart/form-data")
        data = resp.get_json()
        assert data["ok"] is False, data
        assert "2MB" in data["error"], data
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
        # restore 会重建 DB（review #8）→ 用独立库，避免污染共享测试库
        self._old_db = _use_temp_db(self.tmp)
        self.app = create_app(source_mgr=None)
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def teardown_method(self):
        _restore_db(self._old_db)
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
        # 恢复要求核心快照文件齐全（review #9），测试默认补齐
        full = _core_config()
        full.update(entries)
        buf = self._make_zip(full)
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


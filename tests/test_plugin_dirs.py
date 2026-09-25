# tests/test_plugin_dirs.py
"""插件目录拆分：内置（随镜像）/ 用户（挂 Volume）分离后的行为。

布局见 plugin_paths.py：
    parsers_builtin/ + parsers/      channels_builtin/ + channels/
解析顺序用户优先；与内置同名**上传即拒绝**；内置插件只读。
"""
import sys
import os
import io
import json
import zipfile
import tempfile
import shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "test_plugin_dirs.db"))
os.environ["EGO_SSL_ENABLED"] = "0"

import db
import plugin_paths


def _mk_zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    buf.seek(0)
    return buf


class _TmpUserDirs:
    """把「用户目录」指向临时目录；内置目录保持真实（emby.py 等仍是内置）。"""

    def __init__(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = (plugin_paths.PARSERS_USER, plugin_paths.CHANNELS_USER)
        plugin_paths.PARSERS_USER = os.path.join(self.tmp, "parsers")
        plugin_paths.CHANNELS_USER = os.path.join(self.tmp, "channels")
        plugin_paths.ensure_user_dirs()
        for mod in ("parser_loader", "channel_loader"):
            m = sys.modules.get(mod)
            if m and hasattr(m, "_parser_cache"):
                m._parser_cache.clear()
            if m and hasattr(m, "_channel_cache"):
                m._channel_cache.clear()

    def write(self, kind, filename, code="def parse(b,h,q): return {'title':'T'}\n"):
        d = plugin_paths.user_dir(kind)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, filename), "w", encoding="utf-8") as f:
            f.write(code)
        return os.path.join(d, filename)

    def cleanup(self):
        plugin_paths.PARSERS_USER, plugin_paths.CHANNELS_USER = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestResolution:
    def setup_method(self):
        self.d = _TmpUserDirs()

    def teardown_method(self):
        self.d.cleanup()

    def test_builtin_resolves_when_no_user_copy(self):
        p = plugin_paths.resolve("parser", "emby.py")
        assert p and p.endswith(os.path.join("parsers_builtin", "emby.py"))
        assert plugin_paths.source_of("parser", "emby.py") == "builtin"
        assert plugin_paths.is_builtin("channel", "wechat_work_bot.py") is True

    def test_user_copy_shadows_builtin(self):
        """手工放进用户目录的同名文件应生效（这是"改内置插件"的官方出口）。"""
        self.d.write("parser", "emby.py")
        p = plugin_paths.resolve("parser", "emby.py")
        assert p and self.d.tmp in p, "用户目录应优先于内置目录"
        assert plugin_paths.source_of("parser", "emby.py") == "user"

    def test_path_traversal_rejected(self):
        assert plugin_paths.resolve("parser", "../main.py") is None
        assert plugin_paths.resolve("parser", "/etc/passwd") is None
        assert plugin_paths.conflict_reason("parser", "../evil.py") is not None

    def test_list_plugins_reports_source(self):
        self.d.write("channel", "my_channel.py", "class Channel: pass\n")
        items = {x["filename"]: x["source"] for x in plugin_paths.list_plugins("channel")}
        assert items["my_channel.py"] == "user"
        assert items["wechat_work_bot.py"] == "builtin"

    def test_list_plugins_marks_shadow_builtin(self):
        """#5: 同名用户+内置 → list_plugins 标记 shadow_builtin=True；否则 False。"""
        self.d.write("parser", "emby.py")          # 遮蔽内置 emby.py
        it = {x["filename"]: x for x in plugin_paths.list_plugins("parser")}
        assert it["emby.py"]["source"] == "user"
        assert it["emby.py"]["shadow_builtin"] is True, "遮蔽内置应标记 shadow_builtin"
        # 未遮蔽的内置（无用户副本）→ False
        assert it["generic_json.py"]["shadow_builtin"] is False
        # 新增用户插件，无同名内置 → False
        self.d.write("parser", "brand_new.py")
        it2 = {x["filename"]: x for x in plugin_paths.list_plugins("parser")}
        assert it2["brand_new.py"]["shadow_builtin"] is False
        # 通道侧：遮蔽 wechat_work_bot.py
        self.d.write("channel", "wechat_work_bot.py", "class Channel: pass\n")
        itc = {x["filename"]: x for x in plugin_paths.list_plugins("channel")}
        assert itc["wechat_work_bot.py"]["shadow_builtin"] is True
        assert itc["smtp_email.py"]["shadow_builtin"] is False, "未遮蔽的内置通道 False"

    def test_conflict_rule(self):
        assert plugin_paths.conflict_reason("parser", "emby.py") is not None   # 与内置同名
        assert plugin_paths.conflict_reason("parser", "brand_new.py") is None
        assert plugin_paths.conflict_reason("parser", "_hidden.py") is not None
        assert plugin_paths.conflict_reason("parser", "a.txt") is not None


class TestUploadAndEditPolicy:
    @classmethod
    def setup_class(cls):
        db.init_db()
        from api import create_app
        cls.client = create_app().test_client()

    def setup_method(self):
        self.d = _TmpUserDirs()
        conn = db._conn()
        conn.execute("DELETE FROM parsers WHERE id > 1")   # 保留内置 emby 记录
        conn.commit()

    def teardown_method(self):
        self.d.cleanup()

    def test_upload_with_builtin_name_rejected(self):
        r = self.client.post("/api/parsers", data={
            "name": "fake emby",
            "file": (io.BytesIO(b"def parse(b,h,q): return {}\n"), "emby.py"),
        }, content_type="multipart/form-data")
        assert r.status_code == 400, r.data[:200]
        assert "同名" in json.loads(r.data)["error"]

    def test_upload_new_name_lands_in_user_dir(self):
        r = self.client.post("/api/parsers", data={
            "name": "mine",
            "file": (io.BytesIO(b"def parse(b,h,q): return {'title':'x'}\n"), "mine.py"),
        }, content_type="multipart/form-data")
        assert r.status_code == 200, r.data[:200]
        assert os.path.isfile(os.path.join(plugin_paths.user_dir("parser"), "mine.py"))

    def test_bad_new_parser_not_created(self):
        """新插件语法错误：400，且不创建文件。"""
        r = self.client.post("/api/parsers", data={
            "name": "bad",
            "file": (io.BytesIO(b"def parse(b,h,q):\n    return {"), "badnew.py"),
        }, content_type="multipart/form-data")
        assert r.status_code == 400, r.data[:200]
        assert "语法" in json.loads(r.data)["error"]
        assert not os.path.isfile(os.path.join(plugin_paths.user_dir("parser"), "badnew.py"))

    def test_bad_content_update_preserves_old(self):
        """PUT 解析器内容语法错误：400，且旧内容保持不变（原子写）。"""
        r = self.client.post("/api/parsers", data={
            "name": "mine",
            "file": (io.BytesIO(b"def parse(b,h,q): return {'title':'OLD'}\n"), "mine.py"),
        }, content_type="multipart/form-data")
        assert r.status_code == 200, r.data[:200]
        pid = json.loads(r.data)["id"]
        r = self.client.put(f"/api/parsers/{pid}/content",
                            data=json.dumps({"content": "def parse(b,h,q):\n    return {"}),
                            content_type="application/json")
        assert r.status_code == 400, r.data[:200]
        content = open(os.path.join(plugin_paths.user_dir("parser"), "mine.py"),
                       "r", encoding="utf-8").read()
        assert "OLD" in content, "旧内容应保持不变"

    def test_bad_channel_content_update_preserves_old(self):
        """通道 PUT 内容语法错误：400，且旧内容保持不变。"""
        good = ("from channel_base import BaseChannel\n"
                "class Channel(BaseChannel):\n"
                "    CHANNEL_TYPE='badchan'\n"
                "    def send(self,title,content):\n"
                "        return (True,'OLD')\n"
                "    def test(self):\n        return True\n")
        r = self.client.post("/api/channel_plugins",
                             data={"file": (io.BytesIO(good.encode()), "badchan.py")},
                             content_type="multipart/form-data")
        assert r.status_code == 200, r.data[:200]
        bad = ("from channel_base import BaseChannel\n"
               "class Channel(BaseChannel):\n"
               "    CHANNEL_TYPE='badchan'\n"
               "    def send(self,title,content):\n"
               "        return (True,")
        r = self.client.put("/api/channel_plugins/badchan.py",
                            data=json.dumps({"content": bad}),
                            content_type="application/json")
        assert r.status_code == 400, r.data[:200]
        content = open(os.path.join(plugin_paths.user_dir("channel"), "badchan.py"),
                       "r", encoding="utf-8").read()
        assert "OLD" in content

    def test_builtin_parser_is_readonly(self):
        pid = db.create_parser("Emby", "emby.py", "") or 1
        r = self.client.put(f"/api/parsers/{pid}/content",
                            data=json.dumps({"content": "x = 1\n"}),
                            content_type="application/json")
        assert r.status_code == 400, "内置解析器不应可编辑"
        r = self.client.delete(f"/api/parsers/{pid}")
        assert r.status_code == 400, "内置解析器不应可删除"

    def test_builtin_channel_plugin_is_readonly(self):
        r = self.client.put("/api/channel_plugins/wechat_work_bot.py",
                            data=json.dumps({"content": "x = 1\n"}),
                            content_type="application/json")
        assert r.status_code == 400
        r = self.client.delete("/api/channel_plugins/wechat_work_bot.py")
        assert r.status_code == 400

    def test_upload_channel_with_builtin_name_rejected(self):
        r = self.client.post("/api/channel_plugins", data={
            "file": (io.BytesIO(b"class Channel: pass\n"), "bark.py"),
        }, content_type="multipart/form-data")
        assert r.status_code == 400


class TestBackupRestoreCoversUserPlugins:
    @classmethod
    def setup_class(cls):
        db.init_db()
        from api import create_app
        cls.client = create_app().test_client()

    def setup_method(self):
        self.d = _TmpUserDirs()

    def teardown_method(self):
        self.d.cleanup()

    def test_backup_includes_user_channels_and_parsers(self):
        self.d.write("channel", "my_ch.py", "class Channel: pass\n")
        self.d.write("parser", "my_p.py")
        r = self.client.get("/api/backup")
        assert r.status_code == 200
        names = zipfile.ZipFile(io.BytesIO(r.data)).namelist()
        assert "channels/my_ch.py" in names, "备份应包含用户通道插件（P0 项）"
        assert "parsers/my_p.py" in names

    def test_backup_excludes_builtins(self):
        """内置插件不该进备份 —— 否则恢复时会用旧副本遮蔽新版内置。"""
        r = self.client.get("/api/backup")
        names = zipfile.ZipFile(io.BytesIO(r.data)).namelist()
        assert "parsers/emby.py" not in names
        assert "channels/wechat_work_bot.py" not in names

    def test_restore_writes_user_dirs_and_skips_builtins(self):
        z = _mk_zip({
            "channels/restored_ch.py": b"class Channel: pass\n",
            "parsers/restored_p.py": b"def parse(b,h,q): return {}\n",
            "parsers/emby.py": b"# stale builtin copy\n",
            "channels/bark.py": b"# stale builtin copy\n",
        })
        r = self.client.post("/api/restore", data={"file": (z, "b.zip")},
                             content_type="multipart/form-data")
        assert r.status_code == 200, r.data[:300]
        body = json.loads(r.data)
        assert os.path.isfile(os.path.join(plugin_paths.user_dir("channel"), "restored_ch.py"))
        assert os.path.isfile(os.path.join(plugin_paths.user_dir("parser"), "restored_p.py"))
        # 与内置同名的条目应被跳过，而不是写进用户目录去遮蔽内置
        assert not os.path.isfile(os.path.join(plugin_paths.user_dir("parser"), "emby.py"))
        assert not os.path.isfile(os.path.join(plugin_paths.user_dir("channel"), "bark.py"))
        assert set(body.get("skipped_builtin", [])) == {"emby.py", "bark.py"}


class TestUpgradeKeepsUserPlugins:
    """升级 = 重建容器。用户插件不丢，靠的是「用户目录挂了卷、内置目录不挂卷」。

    这是 P0 里最容易被忽略的一环：光把目录拆开还不够，必须**每套部署配置**都
    真的挂了用户目录的卷，否则升级照样丢。
    """

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    PROFILES = ("default", "t1-host", "t2-bridge", "t3-nginx", "t4-acme")

    def _read(self, *parts):
        with open(os.path.join(self.ROOT, *parts), encoding="utf-8") as f:
            return f.read()

    def test_every_compose_profile_mounts_user_plugin_volumes(self):
        for prof in self.PROFILES:
            text = self._read("deploy", prof, "docker-compose.yml")
            assert ":/app/parsers" in text, "%s 未挂载用户解析器目录" % prof
            assert ":/app/channels" in text, "%s 未挂载用户通道插件目录" % prof

    def test_compose_never_mounts_builtin_dirs(self):
        """内置目录一旦挂卷，镜像升级就再也更新不进去。"""
        for prof in self.PROFILES:
            text = self._read("deploy", prof, "docker-compose.yml")
            # 只看非注释行（注释里会提到 parsers_builtin/ 作为说明）
            code = "\n".join(l.split("#")[0] for l in text.splitlines())
            assert "parsers_builtin" not in code, "%s 不该给内置解析器目录挂卷" % prof
            assert "channels_builtin" not in code, "%s 不该给内置通道目录挂卷" % prof

    def test_compose_declares_the_volumes(self):
        for prof in self.PROFILES:
            text = self._read("deploy", prof, "docker-compose.yml")
            assert "ego_parsers:" in text, "%s 缺少 ego_parsers 卷声明" % prof
            assert "ego_channels:" in text, "%s 缺少 ego_channels 卷声明" % prof

    def test_dockerfile_volume_includes_user_dirs_only(self):
        import re
        text = self._read("Dockerfile")
        m = re.search(r"^VOLUME\s*\[(.*?)\]", text, re.M)
        assert m, "Dockerfile 里找不到 VOLUME 声明"
        vol = m.group(1)
        assert "/app/parsers" in vol and "/app/channels" in vol
        assert "builtin" not in vol, "内置目录不应出现在 VOLUME 中"

    def test_user_plugin_survives_cache_reset(self):
        """清掉加载器缓存（等价于进程重启）后，用户插件仍能解析到。"""
        d = _TmpUserDirs()
        try:
            d.write("parser", "survivor.py")
            d.write("channel", "survivor_ch.py", "class Channel: pass\n")

            import parser_loader, channel_loader
            parser_loader._parser_cache.clear()
            channel_loader._channel_cache.clear()

            assert plugin_paths.resolve("parser", "survivor.py") is not None
            assert plugin_paths.source_of("channel", "survivor_ch.py") == "user"
            assert parser_loader.load_parser("survivor.py") is not None
            assert channel_loader.load_plugin("survivor_ch.py") is not None
        finally:
            d.cleanup()


class TestPluginMetadataAndMissingState:
    """插件元信息（来源/版本）与"插件缺失"状态。"""

    @classmethod
    def setup_class(cls):
        db.init_db()
        from api import create_app
        cls.client = create_app().test_client()

    def setup_method(self):
        self.d = _TmpUserDirs()
        db.sync_builtin_parsers()      # 等价于应用启动时的登记
        conn = db._conn()
        conn.execute("DELETE FROM channels WHERE id > 1")
        conn.commit()

    def teardown_method(self):
        self.d.cleanup()

    def test_read_source_meta(self):
        meta = plugin_paths.read_source_meta(
            os.path.join(plugin_paths.builtin_dir("parser"), "generic_json.py"), "PARSER")
        assert meta["name"] == "通用 JSON"
        assert meta["version"], "内置解析器应声明 PARSER_VERSION"

    def test_all_builtin_plugins_declare_version(self):
        """内置插件都该有版本号，否则升级时无法判断新旧。"""
        for kind, prefix in (("parser", "PARSER"), ("channel", "CHANNEL")):
            for item in plugin_paths.list_plugins(kind):
                if item["source"] != "builtin":
                    continue
                meta = plugin_paths.read_source_meta(item["path"], prefix)
                assert meta["version"], "%s 缺少 %s_VERSION" % (item["filename"], prefix)

    def test_parsers_api_exposes_source_and_version(self):
        items = {p["filename"]: p for p in json.loads(self.client.get("/api/parsers").data)}
        assert items["emby.py"]["source"] == "builtin"
        assert items["emby.py"]["version"]
        assert items["generic_json.py"]["name"] == "通用 JSON"
    def test_parsers_api_exposes_shadow_builtin(self):
        """#5: /api/parsers 暴露 shadow_builtin（用户遮蔽内置 → True）。"""
        self.d.write("parser", "emby.py")
        items = {p["filename"]: p for p in json.loads(self.client.get("/api/parsers").data)}
        assert "shadow_builtin" in items["emby.py"], "shadow_builtin 字段缺失"
        assert items["emby.py"]["shadow_builtin"] is True, "用户遮蔽内置应 True"
        assert items["generic_json.py"]["shadow_builtin"] is False, "未遮蔽内置 False"

    def test_channel_plugins_api_exposes_version_and_source(self):
        items = {p["filename"]: p for p in
                 json.loads(self.client.get("/api/channel_plugins").data)}
        assert items["wechat_work_bot.py"]["source"] == "builtin"
        assert items["wechat_work_bot.py"]["channel_version"]
        assert items["smtp_email.py"]["channel_name"]

    def test_channels_api_flags_missing_plugin(self):
        conn = db._conn()
        conn.execute("INSERT INTO channels (id,name,type,config,enabled) "
                     "VALUES (900,'ok','wechat_work_bot','{}',1)")
        conn.execute("INSERT INTO channels (id,name,type,config,enabled) "
                     "VALUES (901,'gone','no_such_plugin','{}',1)")
        conn.commit()

        items = {c["name"]: c for c in json.loads(self.client.get("/api/channels").data)}
        assert items["ok"]["plugin_missing"] is False
        assert items["gone"]["plugin_missing"] is True, "插件文件不存在的通道应被标记"

    def test_loader_error_message_is_actionable(self):
        import channel_loader
        try:
            channel_loader.load_plugin("no_such_plugin.py")
            assert False, "应抛 FileNotFoundError"
        except FileNotFoundError as e:
            assert "deleted" in str(e), "错误信息要提示可能是插件被删除：%s" % e

    def test_i18n_keys_present(self):
        import i18n
        for k in ("ch.plugin_source", "ch.plugin_version", "ch.source_builtin",
                  "ch.source_user", "ch.plugin_missing", "ch.plugin_missing_title"):
            assert k in i18n.TRANSLATIONS["zh"] and k in i18n.TRANSLATIONS["en"], k


class TestUpgradeCompatibility:
    """从旧版本升级到「内置/用户目录分离」后，旧数据仍要能用。

    覆盖三类"旧": 旧备份包（含内置插件副本）、旧数据库（只有 emby 一行）、
    旧配置（DB 里存裸文件名）。
    """

    @classmethod
    def setup_class(cls):
        db.init_db()
        from api import create_app
        cls.client = create_app().test_client()

    def setup_method(self):
        self.d = _TmpUserDirs()

    def teardown_method(self):
        self.d.cleanup()

    # ── 旧备份包 ──

    def test_old_backup_restores_user_plugin_and_skips_builtin_copy(self):
        """旧版备份会把内置解析器一起打进去 —— 恢复时不能让它遮蔽新版内置。"""
        z = _mk_zip({
            "config/parsers.json": b"[]",
            "parsers/emby.py": "# 旧版打包进来的内置副本\n".encode("utf-8"),
            "parsers/legacy_user.py": b"def parse(b,h,q): return {}\n",
            "channels/legacy_ch.py": b"class Channel: pass\n",
        })
        r = self.client.post("/api/restore", data={"file": (z, "old.zip")},
                             content_type="multipart/form-data")
        assert r.status_code == 200, r.data[:300]

        user_parsers = plugin_paths.user_dir("parser")
        assert os.path.isfile(os.path.join(user_parsers, "legacy_user.py")), \
            "旧备份里的用户插件应落到用户目录"
        assert os.path.isfile(os.path.join(plugin_paths.user_dir("channel"), "legacy_ch.py"))
        assert not os.path.isfile(os.path.join(user_parsers, "emby.py")), \
            "内置副本不该写进用户目录"
        assert json.loads(r.data).get("skipped_builtin") == ["emby.py"]

    def test_restore_result_lists_channels(self):
        z = _mk_zip({"channels/x.py": b"class Channel: pass\n"})
        r = self.client.post("/api/restore?dry_run=1", data={"file": (z, "b.zip")},
                             content_type="multipart/form-data")
        body = json.loads(r.data)
        assert body["channels"] == ["channels/x.py"], "dry-run 要能列出通道插件"

    # ── 旧数据库 ──

    def test_old_db_gains_new_builtins_without_touching_existing_rows(self):
        conn = db._conn()
        conn.execute("DELETE FROM parsers WHERE filename != 'emby.py'")
        conn.commit()
        before = [dict(p) for p in db.get_parsers() if p["filename"] == "emby.py"]
        assert before, "前置条件：旧库里应已有 emby 一行"

        added = db.sync_builtin_parsers()
        assert "generic_json.py" in added and "generic_text.py" in added

        after = [dict(p) for p in db.get_parsers() if p["filename"] == "emby.py"]
        assert after == before, "升级不应改动已有的解析器记录（id / 名称 / 描述）"

    def test_old_config_filenames_stay_bare(self):
        """DB 里始终存裸文件名，拆目录后旧配置不用改。"""
        db.sync_builtin_parsers()
        for p in db.get_parsers():
            fn = p["filename"]
            assert "/" not in fn and "\\" not in fn, "不该存路径：%s" % fn
            assert plugin_paths.resolve("parser", fn) is not None, \
                "旧配置里的 %s 应仍能解析到" % fn

    def test_bindings_referencing_builtin_channel_still_resolve(self):
        """通道绑定存的是类型名（wechat_work_bot），升级后仍要能定位到插件。"""
        fn = plugin_paths.channel_filename("wechat_work_bot")
        assert fn == "wechat_work_bot.py"
        assert plugin_paths.resolve("channel", fn) is not None
        assert plugin_paths.source_of("channel", fn) == "builtin"

    # ── 用户自带的内置同名文件（官方"改内置"出口）──

    def test_user_copy_of_builtin_wins_after_upgrade(self):
        self.d.write("parser", "emby.py",
                     "def parse(b,h,q): return {'title':'my-custom'}\n")
        path = plugin_paths.resolve("parser", "emby.py")
        assert plugin_paths.source_of("parser", "emby.py") == "user"
        assert "my-custom" in open(path, encoding="utf-8").read(), \
            "用户目录优先级应让自定义版本在升级后依然生效"

class TestChannelApiShadowedPlugin:
    """#5: 通道主表 shadow 字段（同名用户副本遮蔽内置）。"""

    @classmethod
    def setup_class(cls):
        db.init_db()
        from api import create_app
        cls.client = create_app().test_client()

    def setup_method(self):
        self.d = _TmpUserDirs()
        conn = db._conn()
        conn.execute(
            "INSERT INTO channels (name,type,config,enabled) VALUES (?,?,?,1)",
            ("sh_ch", "wechat_work_bot", "{}"))
        conn.commit()
        # 清掉队列等无关行
        for t in ("message_queue", "source_channels"):
            try:
                conn.execute("DELETE FROM %s" % t)
            except sqlite3.OperationalError:
                pass
        conn.commit()

    def teardown_method(self):
        self.d.cleanup()

    def _ch(self, name="sh_ch"):
        return {p["name"]: p for p in json.loads(self.client.get("/api/channels").data)}[name]

    def test_channel_api_flags_shadowed_plugin(self):
        """未遮蔽 → False；同名用户副本遮蔽内置后 → True。"""
        ch = self._ch()
        assert ch["plugin_shadowed"] is False, "未遮蔽应 False"
        # 放同名用户副本遮蔽内置 wechat_work_bot.py
        self.d.write("channel", "wechat_work_bot.py", "class Channel: pass\n")
        ch = self._ch()
        assert ch["plugin_shadowed"] is True, "遮蔽后应 True"
        assert ch["plugin_missing"] is False, "文件存在，不算缺失"

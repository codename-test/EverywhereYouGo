# tests/test_parser_loader.py
"""解析器加载与 title 自动生成测试。"""
import sys
import os
import json
import tempfile
import shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import parser_loader


# ── 辅助：创建临时 parser 目录 ──

class TempParserDir:
    """用临时目录替换 PARSERS_DIR，测试结束后恢复。"""

    def __init__(self):
        self.original_dir = parser_loader.PARSERS_DIR
        self.tmp_dir = tempfile.mkdtemp()
        parser_loader.PARSERS_DIR = self.tmp_dir
        # 同时清缓存
        parser_loader._parser_cache.clear()

    def write_parser(self, filename, code):
        path = os.path.join(self.tmp_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)

    def cleanup(self):
        parser_loader.PARSERS_DIR = self.original_dir
        parser_loader._parser_cache.clear()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)


# ── 加载内置 emby 解析器 ──

class TestLoadBuiltinParser:
    """加载项目自带的 emby.py 解析器。"""

    def test_load_emby_parser(self):
        mod = parser_loader.load_parser("emby.py")
        assert hasattr(mod, "parse")

    def test_emby_parse_returns_dict(self):
        body = json.dumps({
            "Event": "library.new",
            "Item": {"Name": "Test Movie", "Type": "Movie", "ProductionYear": "2024"},
            "Server": {"Name": "Emby", "Url": "http://localhost:8096"}
        }).encode()
        result = parser_loader.run_parser("emby.py", body, {}, {})
        assert isinstance(result, dict)
        assert result["title"] == "Test Movie"
        assert result["event"] == "library.new"
        assert result["media_type"] == "Movie"


# ── 解析器校验 ──

class TestParserValidation:
    """解析器格式校验。"""

    def setup_method(self):
        self.tmp = TempParserDir()

    def teardown_method(self):
        self.tmp.cleanup()

    def test_no_parse_function_raises(self):
        self.tmp.write_parser("bad.py", "x = 1\n")
        try:
            parser_loader.load_parser("bad.py")
            assert False, "Should have raised"
        except AttributeError as e:
            assert "parse()" in str(e)

    def test_non_dict_return_raises(self):
        self.tmp.write_parser("bad2.py", "def parse(b, h, q): return 'string'\n")
        try:
            parser_loader.run_parser("bad2.py", b"", {}, {})
            assert False, "Should have raised"
        except TypeError as e:
            assert "dict" in str(e)

    def test_file_not_found_raises(self):
        try:
            parser_loader.load_parser("nonexistent.py")
            assert False, "Should have raised"
        except FileNotFoundError:
            pass

    def test_empty_dict_gets_default(self):
        self.tmp.write_parser("empty.py", "def parse(b, h, q): return {}\n")
        result = parser_loader.run_parser("empty.py", b"", {}, {})
        assert "data" in result
        assert result["data"] == "空消息"


# ── Title 自动生成 ──

class TestAutoTitle:
    """解析器未返回 title 时的自动生成逻辑。"""

    def setup_method(self):
        self.tmp = TempParserDir()

    def teardown_method(self):
        self.tmp.cleanup()

    def test_title_from_Name_field(self):
        self.tmp.write_parser("t1.py",
            "def parse(b, h, q): return {'Name': 'MyItem', 'type': 'movie'}\n")
        result = parser_loader.run_parser("t1.py", b"", {}, {})
        assert result["title"] == "MyItem"

    def test_title_from_title_field(self):
        self.tmp.write_parser("t2.py",
            "def parse(b, h, q): return {'title': 'Already Set'}\n")
        result = parser_loader.run_parser("t2.py", b"", {}, {})
        assert result["title"] == "Already Set"

    def test_title_from_Subject_field(self):
        self.tmp.write_parser("t3.py",
            "def parse(b, h, q): return {'Subject': 'Hello'}\n")
        result = parser_loader.run_parser("t3.py", b"", {}, {})
        assert result["title"] == "Hello"

    def test_title_from_Event_field(self):
        self.tmp.write_parser("t4.py",
            "def parse(b, h, q): return {'Event': 'library.new'}\n")
        result = parser_loader.run_parser("t4.py", b"", {}, {})
        assert result["title"] == "library.new"

    def test_title_fallback_first_nonempty(self):
        self.tmp.write_parser("t5.py",
            "def parse(b, h, q): return {'foo': '', 'bar': 'first_value', 'baz': 'second'}\n")
        result = parser_loader.run_parser("t5.py", b"", {}, {})
        assert result["title"] == "first_value"

    def test_title_fallback_all_empty(self):
        self.tmp.write_parser("t6.py",
            "def parse(b, h, q): return {'a': '', 'b': ''}\n")
        result = parser_loader.run_parser("t6.py", b"", {}, {})
        assert result["title"] == "未命名"

    def test_title_not_overwritten_if_present(self):
        self.tmp.write_parser("t7.py",
            "def parse(b, h, q): return {'title': 'Explicit', 'Name': 'Ignored'}\n")
        result = parser_loader.run_parser("t7.py", b"", {}, {})
        assert result["title"] == "Explicit"


# ── 缓存机制 ──

class TestCaching:
    """解析器缓存与重载。"""

    def setup_method(self):
        self.tmp = TempParserDir()

    def teardown_method(self):
        self.tmp.cleanup()

    def test_same_file_returns_cached_module(self):
        self.tmp.write_parser("cached.py", "def parse(b, h, q): return {'v': 1}\n")
        mod1 = parser_loader.load_parser("cached.py")
        mod2 = parser_loader.load_parser("cached.py")
        assert mod1 is mod2

    def test_reload_clears_cache(self):
        self.tmp.write_parser("reloadable.py", "def parse(b, h, q): return {'v': 1}\n")
        mod1 = parser_loader.load_parser("reloadable.py")

        # reload 后返回新模块对象（缓存已清除）
        mod2 = parser_loader.reload_parser("reloadable.py")
        assert mod1 is not mod2

        # 缓存中应该有新模块
        assert "reloadable.py" in parser_loader._parser_cache
        assert parser_loader._parser_cache["reloadable.py"] is mod2

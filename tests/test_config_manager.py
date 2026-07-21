# tests/test_config_manager.py
"""配置管理测试：Schema 校验 + 文件锁读写。"""
import sys
import os
import tempfile
import shutil
import json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config_manager
from config_manager import _validate_config, _read_json, _write_json


class TestValidateConfig:
    """_validate_config Schema 校验测试。"""

    def test_valid_parsers(self):
        """合法的 parsers 配置通过校验。"""
        data = [{"id": 1, "name": "p1", "filename": "p1.py"}]
        assert _validate_config("parsers", data) == []

    def test_parsers_missing_required(self):
        """缺少必需字段报错。"""
        data = [{"id": 1, "name": "p1"}]  # 缺 filename
        errors = _validate_config("parsers", data)
        assert len(errors) == 1
        assert "filename" in errors[0]

    def test_valid_sources(self):
        """合法的 sources 配置通过校验。"""
        data = [{"id": 1, "name": "s1", "port": 9001}]
        assert _validate_config("sources", data) == []

    def test_sources_missing_port(self):
        """sources 缺 port 报错。"""
        data = [{"id": 1, "name": "s1"}]
        errors = _validate_config("sources", data)
        assert any("port" in e for e in errors)

    def test_valid_channels(self):
        """合法的 channels 配置通过校验。"""
        data = [{"id": 1, "name": "c1", "type": "wechatwork"}]
        assert _validate_config("channels", data) == []

    def test_valid_templates(self):
        """合法的 templates 配置通过校验。"""
        data = [{"id": 1, "name": "t1"}]
        assert _validate_config("templates", data) == []

    def test_valid_bindings(self):
        """合法的 bindings 配置通过校验。"""
        data = [{"id": 1, "source_id": 1, "channel_id": 1, "template_id": 1}]
        assert _validate_config("bindings", data) == []

    def test_bindings_missing_fields(self):
        """bindings 缺多个字段全部报出。"""
        data = [{"id": 1}]  # 缺 source_id, channel_id, template_id
        errors = _validate_config("bindings", data)
        assert len(errors) == 1
        assert "source_id" in errors[0]
        assert "channel_id" in errors[0]
        assert "template_id" in errors[0]

    def test_not_list_returns_error(self):
        """非 list 类型报错。"""
        errors = _validate_config("parsers", {"id": 1})
        assert len(errors) == 1
        assert "expected list" in errors[0]

    def test_row_not_dict_returns_error(self):
        """行不是 dict 报错。"""
        errors = _validate_config("parsers", ["not a dict"])
        assert len(errors) == 1
        assert "expected dict" in errors[0]

    def test_multiple_rows_partial_invalid(self):
        """多行中部分无效，错误带索引。"""
        data = [
            {"id": 1, "name": "p1", "filename": "p1.py"},  # ok
            {"id": 2, "name": "p2"},  # 缺 filename
        ]
        errors = _validate_config("parsers", data)
        assert len(errors) == 1
        assert "[1]" in errors[0]

    def test_unknown_config_name_passes(self):
        """未知配置名不校验，直接通过。"""
        assert _validate_config("unknown_type", [{"any": "thing"}]) == []

    def test_empty_list_passes(self):
        """空列表通过校验。"""
        assert _validate_config("parsers", []) == []


class TestFileLockIO:
    """文件锁读写测试。"""

    @classmethod
    def setup_class(cls):
        """使用临时配置目录。"""
        cls._orig_dir = config_manager.CONFIG_DIR
        cls._tmp_dir = tempfile.mkdtemp()
        config_manager.CONFIG_DIR = cls._tmp_dir

    @classmethod
    def teardown_class(cls):
        """恢复原配置目录。"""
        config_manager.CONFIG_DIR = cls._orig_dir
        shutil.rmtree(cls._tmp_dir, ignore_errors=True)

    def test_write_then_read(self):
        """写入后能正确读回。"""
        data = [{"id": 1, "name": "test", "filename": "test.py"}]
        _write_json("test.json", data)
        result = _read_json("test.json")
        assert result == data

    def test_read_nonexistent_returns_none(self):
        """读取不存在的文件返回 None。"""
        assert _read_json("nonexistent.json") is None

    def test_write_unicode(self):
        """中文内容正确写入（ensure_ascii=False）。"""
        data = [{"id": 1, "name": "测试通道"}]
        _write_json("unicode.json", data)
        # 直接读文件验证未被 ASCII 转义
        with open(os.path.join(self._tmp_dir, "unicode.json"), encoding="utf-8") as f:
            content = f.read()
        assert "测试通道" in content
        assert _read_json("unicode.json") == data

    def test_write_atomic_no_tmp_left(self):
        """原子写入后不残留 .tmp 文件。"""
        _write_json("atomic.json", [{"id": 1}])
        assert not os.path.exists(os.path.join(self._tmp_dir, "atomic.json.tmp"))

    def test_read_corrupt_json_returns_none(self):
        """损坏的 JSON 返回 None（不抛异常）。"""
        path = os.path.join(self._tmp_dir, "corrupt.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{invalid json")
        assert _read_json("corrupt.json") is None

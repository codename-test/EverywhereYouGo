# tests/test_renderer.py
"""模板渲染测试。"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from renderer import render_template


class TestSimpleEngine:
    """Simple {var} 替换引擎。"""

    def test_basic_substitution(self):
        result = render_template("simple",
                                 title_tpl="{name} 入库了",
                                 content_tpl="类型: {media_type}",
                                 msg={"name": "阿凡达", "media_type": "Movie"})
        assert result["title"] == "阿凡达 入库了"
        assert result["content"] == "类型: Movie"

    def test_multiple_same_variable(self):
        result = render_template("simple",
                                 title_tpl="{name} - {name}",
                                 content_tpl="",
                                 msg={"name": "test"})
        assert result["title"] == "test - test"

    def test_missing_variable_keeps_template(self):
        result = render_template("simple",
                                 title_tpl="{name} {nonexistent}",
                                 content_tpl="",
                                 msg={"name": "test"})
        # KeyError 时回退到原始模板字符串
        assert result["title"] == "{name} {nonexistent}"

    def test_empty_title_uses_msg_title(self):
        result = render_template("simple",
                                 title_tpl="",
                                 content_tpl="",
                                 msg={"title": "默认标题"})
        assert result["title"] == "默认标题"

    def test_empty_content_generates_fallback(self):
        result = render_template("simple",
                                 title_tpl="",
                                 content_tpl="",
                                 msg={"title": "T", "name": "test", "year": "2024"})
        # 空 content 自动生成 key-value 列表
        assert "name" in result["content"]
        assert "test" in result["content"]

    def test_numeric_variable(self):
        result = render_template("simple",
                                 title_tpl="Year: {year}",
                                 content_tpl="",
                                 msg={"year": 2024})
        assert result["title"] == "Year: 2024"


class TestJinja2Engine:
    """Jinja2 模板引擎。"""

    def test_basic_jinja2(self):
        result = render_template("jinja2",
                                 title_tpl="{{ msg.name }} 入库了",
                                 content_tpl="类型: {{ msg.media_type }}",
                                 msg={"name": "阿凡达", "media_type": "Movie"})
        assert result["title"] == "阿凡达 入库了"
        assert result["content"] == "类型: Movie"

    def test_jinja2_filter(self):
        result = render_template("jinja2",
                                 title_tpl="{{ msg.name | upper }}",
                                 content_tpl="",
                                 msg={"name": "test"})
        assert result["title"] == "TEST"

    def test_jinja2_conditional(self):
        tpl = "{% if msg.type == 'Movie' %}电影{% else %}其他{% endif %}"
        result = render_template("jinja2", title_tpl=tpl, content_tpl="",
                                 msg={"type": "Movie"})
        assert result["title"] == "电影"

        result2 = render_template("jinja2", title_tpl=tpl, content_tpl="",
                                  msg={"type": "Music"})
        assert result2["title"] == "其他"

    def test_jinja2_loop(self):
        result = render_template("jinja2",
                                 title_tpl="",
                                 content_tpl="{% for k, v in msg.items() %}{{ k }}={{ v }} {% endfor %}",
                                 msg={"a": "1", "b": "2"})
        assert "a=1" in result["content"]
        assert "b=2" in result["content"]

    def test_jinja2_nested_access(self):
        result = render_template("jinja2",
                                 title_tpl="{{ msg.item.name }}",
                                 content_tpl="",
                                 msg={"item": {"name": "nested"}})
        assert result["title"] == "nested"

    def test_empty_title_uses_msg_title(self):
        result = render_template("jinja2",
                                 title_tpl="",
                                 content_tpl="",
                                 msg={"title": "默认标题"})
        assert result["title"] == "默认标题"

    def test_invalid_template_returns_fallback(self):
        result = render_template("jinja2",
                                 title_tpl="{{ msg.nonexistent.deep }}",
                                 content_tpl="",
                                 msg={"nonexistent": None})
        # Jinja2 渲染失败时回退到默认值
        assert result["title"] is not None


class TestSSTIProtection:
    """SSTI（服务端模板注入）防护测试。"""

    def test_ssti_class_access_blocked(self):
        """尝试通过 __class__ 访问 Python 类应被沙箱阻止。"""
        # 典型的 SSTI payload
        payload = "{{ ''.__class__.__mro__[1].__subclasses__() }}"
        result = render_template("jinja2", title_tpl=payload, content_tpl="", msg={})
        # SandboxedEnvironment 会阻止访问 __class__，返回空或错误
        assert "__class__" not in result["title"]
        assert "subclasses" not in result["title"]

    def test_ssti_import_blocked(self):
        """尝试导入模块应被阻止。"""
        payload = "{% import os %}{{ os.popen('id').read() }}"
        result = render_template("jinja2", title_tpl=payload, content_tpl="", msg={})
        # 导入失败，返回回退值
        assert "uid=" not in result["title"]

    def test_ssti_getattr_blocked(self):
        """尝试通过 attr 过滤器访问私有属性应被阻止。"""
        payload = "{{ msg|attr('__class__') }}"
        result = render_template("jinja2", title_tpl=payload, content_tpl="", msg={"test": "value"})
        # attr 过滤器访问 __class__ 应被阻止
        assert "__class__" not in str(result["title"])

    def test_normal_template_still_works(self):
        """正常模板在沙箱中应正常工作。"""
        result = render_template("jinja2",
                                 title_tpl="{{ msg.title }} - {{ msg.year }}",
                                 content_tpl="{{ msg.overview }}",
                                 msg={"title": "星际穿越", "year": 2014, "overview": "科幻电影"})
        assert result["title"] == "星际穿越 - 2014"
        assert result["content"] == "科幻电影"


class TestEngineFallback:
    """引擎选择。"""

    def test_unknown_engine_uses_simple(self):
        result = render_template("unknown_engine",
                                 title_tpl="{name}",
                                 content_tpl="",
                                 msg={"name": "test"})
        assert result["title"] == "test"

    def test_return_structure(self):
        result = render_template("simple", title_tpl="T", content_tpl="C", msg={})
        assert "title" in result
        assert "content" in result
        assert isinstance(result["title"], str)
        assert isinstance(result["content"], str)

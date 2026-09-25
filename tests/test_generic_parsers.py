# tests/test_generic_parsers.py
"""三个通用解析器：JSON / 表单 / 文本。

设计要点：解析结果必须是**扁平标量字段**（含点号路径），因为路由条件只认标量，
列表会让条件取不到值。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import parser_loader


def _run(name, body, headers=None, query=None):
    return parser_loader.run_parser(name, body, headers or {}, query or {})


class TestGenericJson:
    P = "generic_json.py"

    def test_flattens_nested_with_dotted_path(self):
        body = '{"Event":"library.new","Item":{"Name":"星际穿越","Type":"Movie"}}'.encode("utf-8")
        r = _run(self.P, body)
        assert r["event"] == "library.new"
        assert r["item.name"] == "星际穿越"
        assert r["item.type"] == "Movie"

    def test_scalar_array_becomes_string(self):
        """数组必须合成字符串，否则路由条件取不到值（router 只认标量）。"""
        r = _run(self.P, b'{"Tags":["a","b"]}')
        assert r["tags"] == "a, b"
        assert isinstance(r["tags"], str)

    def test_nested_array_of_objects_indexed(self):
        r = _run(self.P, b'{"args":[{"k":"a"},{"k":"b"}]}')
        assert r["args.0.k"] == "a"
        assert r["args.1.k"] == "b"

    def test_title_guessed_from_name(self):
        r = _run(self.P, '{"Item":{"Name":"标题来了"}}'.encode("utf-8"))
        assert r["title"] == "标题来了"

    def test_query_params_merged_without_overriding_body(self):
        r = _run(self.P, b'{"event":"from_body"}', query={"event": "from_query",
                                                          "src": "q"})
        assert r["event"] == "from_body"
        assert r["src"] == "q"

    def test_invalid_json_degrades_to_raw_text(self):
        r = _run(self.P, b"this is not json\nsecond line")
        assert r["title"] == "this is not json"
        assert "second line" in r["content"]

    def test_empty_body(self):
        r = _run(self.P, b"")
        assert r["title"] == ""

    def test_all_values_are_scalars(self):
        r = _run(self.P, b'{"a":{"b":{"c":[1,2]}},"d":null,"e":true}')
        for k, v in r.items():
            assert isinstance(v, (str, int, float, bool)), "字段 %s 不是标量：%r" % (k, v)

    def test_content_is_kv_listing(self):
        r = _run(self.P, b'{"a":1,"b":2}')
        assert "- **a**: 1" in r["content"]


class TestGenericForm:
    P = "generic_form.py"

    def test_urlencoded(self):
        r = _run(self.P, b"name=%E5%BC%A0%E4%B8%89&level=INFO",
                 {"Content-Type": "application/x-www-form-urlencoded"})
        assert r["name"] == "张三"
        assert r["level"] == "INFO"
        assert r["title"] == "张三"

    def test_repeated_field_merged_to_string(self):
        r = _run(self.P, b"tag=a&tag=b&tag=c",
                 {"Content-Type": "application/x-www-form-urlencoded"})
        assert r["tag"] == "a, b, c"
        assert isinstance(r["tag"], str)

    def test_plus_decoded_as_space(self):
        r = _run(self.P, b"msg=hello+world",
                 {"Content-Type": "application/x-www-form-urlencoded"})
        assert r["msg"] == "hello world"

    def test_unknown_content_type_still_parsed(self):
        """不认识的编码也尽量按 urlencoded 解析，不丢消息。"""
        r = _run(self.P, b"a=1&b=2", {"Content-Type": "text/plain"})
        assert r["a"] == "1" and r["b"] == "2"

    def test_multipart_with_text_field(self):
        boundary = "----EGOboundary"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="host"\r\n\r\n'
            "nas-01\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="level"\r\n\r\n'
            "WARN\r\n"
            f"--{boundary}--\r\n"
        ).encode()
        r = _run(self.P, body,
                 {"Content-Type": "multipart/form-data; boundary=%s" % boundary})
        assert r["host"] == "nas-01"
        assert r["level"] == "WARN"

    def test_multipart_file_becomes_name_and_size(self):
        boundary = "----EGOboundary"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="report"; filename="r.txt"\r\n'
            "Content-Type: text/plain\r\n\r\n"
            "hello file\r\n"
            f"--{boundary}--\r\n"
        ).encode()
        r = _run(self.P, body,
                 {"Content-Type": "multipart/form-data; boundary=%s" % boundary})
        assert r["report.filename"] == "r.txt"
        assert r["report.size"] == len("hello file")
        assert "hello file" not in "".join(str(v) for k, v in r.items()
                                          if k != "content"), "文件内容不应被塞进变量"

    def test_title_falls_back_to_first_value(self):
        r = _run(self.P, b"zzz=first&aaa=second",
                 {"Content-Type": "application/x-www-form-urlencoded"})
        assert r["title"] == "first"


class TestGenericText:
    P = "generic_text.py"

    def test_first_nonblank_line_is_title(self):
        r = _run(self.P, b"\n\n[ALERT] disk full\nlevel=ERROR\n")
        assert r["title"] == "[ALERT] disk full"

    def test_kv_lines_extracted(self):
        r = _run(self.P, b"[ALERT] disk\nlevel=ERROR\nhost=nas-01\nusage: 91%\n")
        assert r["level"] == "ERROR"
        assert r["host"] == "nas-01"
        assert r["usage"] == "91%"          # 冒号写法也认

    def test_full_text_in_content_and_text(self):
        body = b"line1\nline2\n"
        r = _run(self.P, body)
        assert "line1" in r["content"] and "line2" in r["content"]
        assert r["text"] == r["content"]

    def test_non_kv_lines_ignored(self):
        r = _run(self.P, b"title here\njust a sentence with spaces\nkey=value\n")
        assert r["key"] == "value"
        assert "just a sentence with spaces" in r["content"]

    def test_repeated_key_merged(self):
        r = _run(self.P, b"k=1\nk=2\n")
        assert r["k"] == "1, 2"

    def test_long_text_truncated(self):
        r = _run(self.P, b"x" * 30000)
        assert len(r["content"]) <= 20100

    def test_query_merged(self):
        r = _run(self.P, b"a=1\n", query={"src": "q"})
        assert r["src"] == "q"

    def test_line_count_exposed(self):
        r = _run(self.P, b"a\nb\nc\n")
        assert r["line_count"] == 3


class TestBuiltinParsersRegistered:
    """新增的内置解析器必须能被登记进 parsers 表，否则 WebUI 里选不到。"""

    def test_sync_registers_all_builtin_parsers(self):
        import tempfile
        os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "sync.db")
        import importlib
        import db as _db
        importlib.reload(_db)
        _db.init_db()
        added = _db.sync_builtin_parsers()
        names = {p["filename"] for p in _db.get_parsers()}
        assert {"emby.py", "generic_json.py", "generic_form.py",
                "generic_text.py"} <= names, names
        assert "generic_json.py" in added
        assert _db.sync_builtin_parsers() == [], "重复调用应幂等"

    def test_meta_read_from_source(self):
        import tempfile
        os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "meta.db")
        import importlib
        import db as _db
        importlib.reload(_db)
        _db.init_db()
        _db.sync_builtin_parsers()
        by_name = {p["filename"]: p for p in _db.get_parsers()}
        assert by_name["generic_json.py"]["name"] == "通用 JSON"
        assert by_name["generic_text.py"]["name"] == "通用文本"

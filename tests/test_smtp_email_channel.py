# tests/test_smtp_email_channel.py
"""SMTP 邮件通道。

重点覆盖：
  - 加密方式按端口推断（+ 显式覆盖）
  - 收件人/抄送的分隔符解析
  - 认证失败时必须把用户引向「授权码」（这是国内邮箱最常见的坑）
  - 端到端：对着一个极简 SMTP 桩服务器真的把邮件发出去
"""
import os
import re
import socket
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import channel_loader

Mod = channel_loader.load_plugin("smtp_email.py")
EmailChannel = Mod.Channel


def _cfg(**over):
    base = {
        "smtp_host": "smtp.example.com",
        "smtp_port": "587",
        "username": "me@example.com",
        "password": "authcode",
        "from_addr": "",
        "to_addrs": "a@example.com",
        "cc_addrs": "",
        "encryption": "auto",
    }
    base.update(over)
    return base


# ── 极简 SMTP 桩服务器（够 smtplib 走完 EHLO/AUTH/MAIL/RCPT/DATA/QUIT）──

class StubSMTP(threading.Thread):
    def __init__(self, auth_ok=True):
        super().__init__(daemon=True)
        self.auth_ok = auth_ok
        self.messages = []
        self.commands = []
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]

    def run(self):
        try:
            conn, _ = self._sock.accept()
        except OSError:
            return
        f = conn.makefile("rwb")

        def send(line):
            f.write((line + "\r\n").encode())
            f.flush()

        send("220 stub ESMTP")
        data_mode, body = False, []
        while True:
            raw = f.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if data_mode:
                if line == ".":
                    data_mode = False
                    self.messages.append("\n".join(body))
                    body = []
                    send("250 OK queued")
                else:
                    body.append(line)
                continue

            self.commands.append(line.split(" ")[0].upper())
            up = line.upper()
            if up.startswith(("EHLO", "HELO")):
                send("250-stub")
                send("250-AUTH PLAIN")       # 只广告 PLAIN，桩好实现
                send("250 OK")
            elif up.startswith("AUTH"):
                send("235 2.7.0 Authentication successful" if self.auth_ok
                     else "535 5.7.8 Authentication credentials invalid")
            elif up.startswith("MAIL FROM"):
                send("250 OK")
            elif up.startswith("RCPT TO"):
                send("250 OK")
            elif up.startswith("DATA"):
                send("354 End data with <CR><LF>.<CR><LF>")
                data_mode = True
            elif up.startswith("NOOP"):
                send("250 OK")
            elif up.startswith("QUIT"):
                send("221 Bye")
                break
            else:
                send("250 OK")
        try:
            f.close()
            conn.close()
        except Exception:
            pass


class TestEncryptionInference:
    """按端口推断加密方式（auto）。

    依据：25=IANA smtp(RFC5321)、587=submission(RFC4409, STARTTLS)、
    465=submissions(RFC8314, 隐式 TLS)；994 无 IANA 注册，是网易自定的 SSL 端口。
    """

    def _enc(self, port, enc="auto"):
        return EmailChannel(_cfg(smtp_port=str(port), encryption=enc))._resolve_encryption()

    def test_port_mapping(self):
        assert self._enc(465) == "ssl"
        assert self._enc(994) == "ssl"        # 网易自定
        assert self._enc(587) == "starttls"
        assert self._enc(25) == "none"
        assert self._enc(2525) == "none"      # 非标端口只能靠显式覆盖

    def test_explicit_override_wins(self):
        assert self._enc(465, "starttls") == "starttls"
        assert self._enc(587, "ssl") == "ssl"
        assert self._enc(25, "starttls") == "starttls"
        assert self._enc(587, "none") == "none"

    def test_blank_or_unknown_falls_back_to_auto(self):
        assert self._enc(465, "") == "ssl"
        assert self._enc(587, "whatever") == "starttls"


class TestAddressParsing:
    def test_separators(self):
        c = EmailChannel(_cfg(to_addrs="a@x.com, b@x.com;c@x.com\nd@x.com e@x.com"))
        assert c._addresses("to_addrs") == [
            "a@x.com", "b@x.com", "c@x.com", "d@x.com", "e@x.com"]

    def test_blank_entries_dropped(self):
        c = EmailChannel(_cfg(cc_addrs=" , \n ,"))
        assert c._addresses("cc_addrs") == []


class TestValidation:
    def test_missing_host(self):
        assert "服务器" in EmailChannel(_cfg(smtp_host=""))._validate()

    def test_bad_port(self):
        assert "端口" in EmailChannel(_cfg(smtp_port="abc"))._validate()
        assert "端口" in EmailChannel(_cfg(smtp_port="70000"))._validate()

    def test_missing_credential_mentions_auth_code(self):
        msg = EmailChannel(_cfg(password=""))._validate()
        assert "授权码" in msg, "缺凭据时要提示授权码，而不是只说密码"

    def test_missing_recipient(self):
        assert "收件人" in EmailChannel(_cfg(to_addrs="  "))._validate()

    def test_valid_passes(self):
        assert EmailChannel(_cfg())._validate() is None


class TestBuildMessage:
    def test_headers_and_multipart(self):
        c = EmailChannel(_cfg(to_addrs="a@x.com", cc_addrs="c@x.com",
                              from_addr="from@x.com"))
        msg, recipients = c._build_message("标题", "正文 **粗体**")
        assert msg["Subject"] == "标题"
        assert "from@x.com" in msg["From"]
        assert msg["To"] == "a@x.com"
        assert msg["Cc"] == "c@x.com"
        assert recipients == ["a@x.com", "c@x.com"]
        parts = list(msg.iter_parts())
        assert len(parts) == 2, "应有纯文本 + HTML 两个可选部分"
        assert parts[0].get_content_type() == "text/plain"
        assert parts[1].get_content_type() == "text/html"
        assert "<strong>粗体</strong>" in parts[1].get_content(), "正文应渲染成 HTML"

    def test_from_defaults_to_username(self):
        c = EmailChannel(_cfg())
        msg, _ = c._build_message("t", "b")
        assert "me@example.com" in msg["From"]


class TestEndToEnd:
    def _send(self, auth_ok=True, **over):
        stub = StubSMTP(auth_ok=auth_ok)
        stub.start()
        try:
            cfg = _cfg(smtp_host="127.0.0.1", smtp_port=str(stub.port),
                       encryption="none", **over)
            ch = EmailChannel(cfg)
            ok, err = ch.send("EGo 测试", "正文")
            return stub, ok, err
        finally:
            pass

    def test_send_succeeds(self):
        stub, ok, err = self._send()
        stub.join(timeout=5)
        assert ok is True, err
        assert len(stub.messages) == 1
        assert "EGo" in stub.messages[0] or "Subject" in stub.messages[0]
        assert "AUTH" in stub.commands and "QUIT" in stub.commands

    def test_auth_failure_points_to_auth_code(self):
        stub, ok, err = self._send(auth_ok=False)
        stub.join(timeout=5)
        assert ok is False
        assert "授权码" in err, "认证失败必须把用户引向授权码：%s" % err

    def test_test_method_returns_reason(self):
        stub = StubSMTP(auth_ok=False)
        stub.start()
        ch = EmailChannel(_cfg(smtp_host="127.0.0.1", smtp_port=str(stub.port),
                               encryption="none"))
        ok, err = ch.test()
        stub.join(timeout=5)
        assert ok is False and "授权码" in err

    def test_test_method_success(self):
        """#5: SMTP test() 成功路径 — 连接+登录+NOOP 全过 → (True, "")。"""
        stub = StubSMTP(auth_ok=True)
        stub.start()
        try:
            ch = EmailChannel(_cfg(smtp_host="127.0.0.1", smtp_port=str(stub.port),
                                   encryption="none"))
            ok, err = ch.test()
            assert ok is True, "SMTP test() 成功路径应通过：%s" % err
            assert err == ""
            assert "AUTH" in stub.commands and "NOOP" in stub.commands, stub.commands
        finally:
            stub.join(timeout=5)

    def test_loader_unpacks_tuple_result(self):
        """test_channel 要能把 (ok, err) 里的原因带给用户。"""
        assert channel_loader._unpack_test_result(True) == (True, "")
        assert channel_loader._unpack_test_result(False) == (False, "")
        assert channel_loader._unpack_test_result((True, "")) == (True, "")
        assert channel_loader._unpack_test_result((False, "boom")) == (False, "boom")

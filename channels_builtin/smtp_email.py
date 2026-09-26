#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""SMTP 邮件通道

⚠️ 关于密码：**多数邮箱要的不是登录密码，而是「授权码」**。
QQ / 163 / 126 / 新浪 / Gmail 等都会拒绝登录密码。所以：
  - 字段标签写成「密码 / 授权码」，不写「密码」
  - desc 里说明要先去邮箱设置开启 SMTP 服务并生成授权码
  - 认证失败（535 等）时把错误信息直接引到授权码上

加密方式靠端口推断（auto），也可用 encryption 显式覆盖：
  25 / 其他 → 明文；587 → STARTTLS；465、994 → 隐式 TLS(SSL)
推断依据：IANA 注册 25=smtp（RFC5321）、587=submission（RFC4409，STARTTLS）、
465=submissions（RFC8314，隐式 TLS）；994 无 IANA 注册，是网易自定的 SMTP SSL 端口。
"""

import smtplib
import ssl

import markdown
from email.message import EmailMessage
from email.utils import formataddr

import log

# 已知隐式 TLS 端口（465 为 IANA submissions；994 为网易自定）
_IMPLICIT_TLS_PORTS = (465, 994)
_STARTTLS_PORTS = (587,)


class Channel(BaseChannel):
    CHANNEL_TYPE = "smtp_email"
    CHANNEL_NAME = "邮件 (SMTP)"
    CHANNEL_VERSION = "1.0"
    CONFIG_FIELDS = [
        {
            "name": "smtp_host", "type": "text",
            "label": "SMTP Server", "label_zh": "SMTP 服务器",
            "desc": "SMTP server hostname",
            "desc_zh": "SMTP 服务器地址，如 smtp.qq.com",
            "placeholder": "smtp.qq.com", "required": True, "default": ""
        },
        {
            "name": "smtp_port", "type": "text",
            "label": "Port", "label_zh": "端口",
            "desc": "465 = implicit TLS, 587 = STARTTLS, 25 = plain",
            "desc_zh": "465 = 隐式 TLS，587 = STARTTLS，25 = 明文（加密方式留空时按端口自动判断）",
            "placeholder": "465", "required": True, "default": "465"
        },
        {
            "name": "username", "type": "text",
            "label": "Username", "label_zh": "登录账号",
            "desc": "Usually the full email address",
            "desc_zh": "通常是完整邮箱地址",
            "placeholder": "you@example.com", "required": True, "default": ""
        },
        {
            "name": "password", "type": "password",
            "label": "Password / Auth Code", "label_zh": "密码 / 授权码",
            "desc": ("Most providers (QQ, 163, 126, Sina, Gmail…) reject your login password. "
                     "Enable SMTP in your mailbox settings and generate an authorization code "
                     "(Gmail calls it an App Password) — put THAT here."),
            "desc_zh": ("多数邮箱不接受登录密码。请先到邮箱设置里开启 SMTP 服务并生成"
                        "「授权码」（Gmail 叫「应用专用密码」），把授权码填在这里。"),
            "placeholder": "授权码 / App Password", "required": True, "default": ""
        },
        {
            "name": "from_addr", "type": "text",
            "label": "From", "label_zh": "发件人",
            "desc": "Empty = use username. Most providers require it to match the account.",
            "desc_zh": "留空则用登录账号；多数邮箱要求发件人与账号一致，不一致会被拒",
            "placeholder": "(留空 = 用登录账号)", "required": False, "default": ""
        },
        {
            "name": "to_addrs", "type": "textarea",
            "label": "To", "label_zh": "收件人",
            "desc": "One or more addresses, separated by comma / semicolon / newline",
            "desc_zh": "一个或多个地址，用逗号、分号或换行分隔",
            "placeholder": "a@example.com\nb@example.com", "required": True, "default": ""
        },
        {
            "name": "cc_addrs", "type": "textarea",
            "label": "Cc (optional)", "label_zh": "抄送（可选）",
            "desc": "Same separator rules as To",
            "desc_zh": "分隔方式同「收件人」",
            "placeholder": "", "required": False, "default": ""
        },
        {
            "name": "encryption", "type": "text",
            "label": "Encryption", "label_zh": "加密方式",
            "desc": "auto / ssl / starttls / none. Leave auto to infer from the port.",
            "desc_zh": "auto / ssl / starttls / none，留 auto 或留空 = 按端口自动判断",
            "placeholder": "auto", "required": False, "default": "auto"
        },
    ]

    def __init__(self, config: dict):
        super().__init__(config)
        self.host = (config.get("smtp_host") or "").strip()
        self.username = (config.get("username") or "").strip()
        self.password = config.get("password") or ""
        self.from_addr = (config.get("from_addr") or "").strip()
        self.encryption = (config.get("encryption") or "auto").strip().lower()

        raw_port = str(config.get("smtp_port") or "").strip()
        try:
            self.port = int(raw_port)
        except ValueError:
            self.port = -1          # 交给 _validate 报错

    # ── 内部 ──

    def _addresses(self, key):
        raw = str(self.config.get(key) or "")
        for sep in (";", ",", "\n", "\r", " "):
            raw = raw.replace(sep, "\n")
        return [a.strip() for a in raw.split("\n") if a.strip()]

    def _validate(self):
        if not self.host:
            return "SMTP 服务器不能为空"
        if self.port <= 0 or self.port > 65535:
            return "端口不合法，应为 1-65535 的整数"
        if not self.username:
            return "登录账号不能为空"
        if not self.password:
            return ("密码 / 授权码不能为空。多数邮箱不接受登录密码，"
                    "请到邮箱设置里开启 SMTP 服务并生成「授权码」")
        if not self._addresses("to_addrs"):
            return "收件人不能为空"
        return None

    def _resolve_encryption(self):
        """auto 时按端口推断；显式值直接采用。"""
        if self.encryption in ("ssl", "starttls", "none"):
            return self.encryption
        if self.port in _IMPLICIT_TLS_PORTS:
            return "ssl"
        if self.port in _STARTTLS_PORTS:
            return "starttls"
        return "none"

    def _sender(self):
        addr = self.from_addr or self.username
        return formataddr((addr, addr))

    def _build_message(self, title, content):
        msg = EmailMessage()
        msg["Subject"] = title
        msg["From"] = self._sender()
        msg["To"] = ", ".join(self._addresses("to_addrs"))
        cc = self._addresses("cc_addrs")
        if cc:
            msg["Cc"] = ", ".join(cc)

        text = f"{title}\n\n{content}" if title else content
        msg.set_content(text)
        try:
            html_body = markdown.markdown(content or "")
            msg.add_alternative(
                f"<h3>{title}</h3>\n{html_body}" if title else html_body,
                subtype="html")
        except Exception:
            pass                    # HTML 版失败不影响纯文本版

        to_all = self._addresses("to_addrs") + cc
        return msg, to_all

    def _connect(self):
        mode = self._resolve_encryption()
        ctx = ssl.create_default_context()
        if mode == "ssl":
            server = smtplib.SMTP_SSL(self.host, self.port, timeout=20, context=ctx)
        else:
            server = smtplib.SMTP(self.host, self.port, timeout=20)
            server.ehlo()
            if mode == "starttls":
                server.starttls(context=ctx)
                server.ehlo()
        return server, mode

    @staticmethod
    def _auth_error_hint(e):
        code = getattr(e, "smtp_code", "")
        detail = getattr(e, "smtp_error", b"")
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", "replace")
        return ("SMTP 认证失败"
                + (f"（{code} {detail}）" if code or detail else "")
                + "。多数邮箱**不接受登录密码**，请在邮箱设置里开启 SMTP 服务并"
                  "生成「授权码」填到密码栏（Gmail 叫「应用专用密码」）；"
                  "同时确认登录账号是完整邮箱地址。")

    # ── 对外 ──

    def send(self, title: str, content: str) -> tuple:
        err = self._validate()
        if err:
            return False, err

        try:
            msg, recipients = self._build_message(title, content)
            server, _mode = self._connect()
            try:
                server.login(self.username, self.password)
                server.send_message(msg, from_addr=self._sender(), to_addrs=recipients)
            finally:
                try:
                    server.quit()
                except Exception:
                    pass
            log.logger.info(f"SMTP sent to {len(recipients)} recipient(s) via {self.host}")
            return True, ""
        except smtplib.SMTPAuthenticationError as e:
            log.logger.error(f"SMTP auth failed: {e}")
            return False, self._auth_error_hint(e)
        except smtplib.SMTPRecipientsRefused as e:
            return False, f"收件人被拒：{e.recipients}"
        except smtplib.SMTPException as e:
            return False, f"SMTP 错误：{e}"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

    def test(self):
        """连通性 + 认证测试。返回 (ok, error)，便于把真实原因显示给用户。"""
        err = self._validate()
        if err:
            return False, err
        try:
            server, mode = self._connect()
            try:
                server.login(self.username, self.password)
                server.noop()
            finally:
                try:
                    server.quit()
                except Exception:
                    pass
            return True, ""
        except smtplib.SMTPAuthenticationError as e:
            return False, self._auth_error_hint(e)
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
web_ui.py — 兼容层：保留旧入口，内部转发到 api/ 蓝图。
重构完成后此文件将被删除。

端口模型（v1.2.2）：
- HTTP  (WEB_PORT,     默认 5000)：webhook 接收(/in/)、/api/health 等机器间流量。
- HTTPS (WEB_SSL_PORT, 默认 5001)：管理页面。仅在证书存在时启用。
管理页面被 HTTP 访问时由 api 层 301 跳转到 HTTPS（见 api/__init__.py）。
"""

import os
import ssl
import threading
from werkzeug.serving import run_simple
from api import create_app

# 创建 app（兼容旧入口）
app = create_app(source_mgr=None)


def ssl_cert_paths():
    """返回 (cert_file, key_file)，支持环境变量覆盖。
    
    优先级：EGO_SSL_CERT/EGO_SSL_KEY > EGO_SSL_DIR > 默认 ./certs/
    """
    root = os.path.dirname(os.path.abspath(__file__))
    cert_dir = os.getenv("EGO_SSL_DIR", os.path.join(root, "certs"))
    cert_file = os.getenv("EGO_SSL_CERT", os.path.join(cert_dir, "ego.crt"))
    key_file = os.getenv("EGO_SSL_KEY", os.path.join(cert_dir, "ego.key"))
    return cert_file, key_file


def ssl_enabled_by_env():
    """EGO_SSL_ENABLED=0/false/no/off 时返回 False（显式关闭 SSL）。默认开启。"""
    return os.getenv("EGO_SSL_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")


def _certs_exist():
    cert_file, key_file = ssl_cert_paths()
    return os.path.isfile(cert_file) and os.path.isfile(key_file)


def has_ssl():
    """SSL 已启用且证书齐备则返回 True。"""
    return ssl_enabled_by_env() and _certs_exist()


def _ssl_context():
    """构建服务端 SSL 上下文；SSL 关闭或证书缺失返回 None。"""
    if not ssl_enabled_by_env():
        return None
    cert_file, key_file = ssl_cert_paths()
    if os.path.isfile(cert_file) and os.path.isfile(key_file):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert_file, key_file)
        return ctx
    return None


def run_web_ui(port: int = 5000, ssl_port: int = 5001):
    """由 main.py 调用，启动 Flask 开发服务器。

    - 有证书：HTTP 跑在 `port`（webhook/健康检查），HTTPS 跑在 `ssl_port`（管理页面）。
    - 无证书：仅 HTTP 跑在 `port`，并告警。
    """
    ssl_ctx = _ssl_context()
    if ssl_ctx is None:
        import log
        log.logger.warning("SSL cert not found, serving HTTP only on port %s "
                           "(admin UI will not be encrypted).", port)
        run_simple("0.0.0.0", port, app, threaded=True, use_reloader=False)
        return

    # HTTPS（管理页面）放后台线程
    https_thread = threading.Thread(
        target=run_simple,
        args=("0.0.0.0", ssl_port, app),
        kwargs={"ssl_context": ssl_ctx, "threaded": True, "use_reloader": False},
        daemon=True,
        name="https-server",
    )
    https_thread.start()

    # HTTP（webhook/健康检查）放主线程
    run_simple("0.0.0.0", port, app, threaded=True, use_reloader=False)

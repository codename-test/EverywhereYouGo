#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
web_ui.py — 兼容层：保留旧入口，内部转发到 api/ 蓝图。
重构完成后此文件将被删除。
"""

import os
import ssl
from werkzeug.serving import run_simple
from api import create_app

# 创建 app（兼容旧入口）
app = create_app(source_mgr=None)


def run_web_ui(port: int = 5000):
    """由 main.py 调用，启动 Flask 开发服务器。支持 SSL。"""
    # 支持环境变量配置证书路径
    cert_file = os.getenv("EGO_SSL_CERT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "certs", "ego.crt"))
    key_file = os.getenv("EGO_SSL_KEY", os.path.join(os.path.dirname(os.path.abspath(__file__)), "certs", "ego.key"))
    ssl_ctx = None
    if os.path.isfile(cert_file) and os.path.isfile(key_file):
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(cert_file, key_file)
    run_simple("0.0.0.0", port, app, ssl_context=ssl_ctx, threaded=True, use_reloader=False)

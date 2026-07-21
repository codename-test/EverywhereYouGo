#!/usr/bin/python3
"""生成自签名 SSL 证书。支持环境变量指定证书路径。"""
import os, subprocess

# 支持环境变量配置证书路径
CERT_DIR = os.getenv("EGO_SSL_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "certs"))
KEY = os.getenv("EGO_SSL_KEY", os.path.join(CERT_DIR, "ego.key"))
CERT = os.getenv("EGO_SSL_CERT", os.path.join(CERT_DIR, "ego.crt"))

os.makedirs(os.path.dirname(CERT) or ".", exist_ok=True)

if os.path.isfile(KEY) and os.path.isfile(CERT):
    print("✓ 证书已存在，跳过生成")
else:
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048",
        "-keyout", KEY, "-out", CERT, "-days", "3650",
        "-nodes", "-subj", "/CN=EGo"
    ], check=True)
    print("✓ 自签名证书已生成")
    print(f"  证书: {CERT}")
    print(f"  密钥: {KEY}")

#!/usr/bin/python3
"""生成自签名 SSL 证书。"""
import os, subprocess

CERT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "certs")
os.makedirs(CERT_DIR, exist_ok=True)

KEY = os.path.join(CERT_DIR, "ego.key")
CERT = os.path.join(CERT_DIR, "ego.crt")

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

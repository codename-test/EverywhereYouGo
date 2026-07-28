#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
EGo 构建脚本 — 打包 Docker 镜像。
用法:
  python3 build.py                  # 构建镜像
  python3 build.py --push           # 构建并推送
  python3 build.py --tag v1.2.4     # 指定版本标签
"""

import os
import sys
import subprocess
import argparse

VERSION = "1.2.4"
IMAGE_NAME = "codenametest/everywhereyougo"
DOCKERFILE = "Dockerfile"


def run(cmd: str, cwd: str | None = None):
    print(f"  ▶ {cmd}")
    subprocess.check_call(cmd, shell=True, cwd=cwd or ROOT)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build EGo Docker image")
    parser.add_argument("--push", action="store_true", help="Push to registry")
    parser.add_argument("--tag", default=VERSION, help=f"Image tag (default: {VERSION})")
    parser.add_argument("--registry", default="", help="Registry URL (e.g. docker.io/user)")
    args = parser.parse_args()

    ROOT = os.path.dirname(os.path.abspath(__file__))
    tag = args.tag
    registry = args.registry.rstrip("/")

    # 镜像标签
    tags = [f"{IMAGE_NAME}:{tag}", f"{IMAGE_NAME}:latest"]
    if registry:
        tags = [f"{registry}/{t}" for t in tags]

    # 构建
    print(f"\n🚀 Building EGo v{tag}...")
    run(f"docker build -t {tags[0]} -f {DOCKERFILE} .", ROOT)

    # 打 latest 标签
    if len(tags) > 1:
        run(f"docker tag {tags[0]} {tags[1]}")

    print(f"✅ Built: {tags[0]}")

    # 推送
    if args.push:
        for t in tags:
            print(f"  📤 Pushing {t}...")
            run(f"docker push {t}")
        print("✅ Pushed all tags")

    print("\n📦 使用方式（推荐直接用 deploy/ 下的 compose 配置）:")
    print(f"  docker run -d --name ego -p 5000:5000 -p 5001:5001 "
          f"-v ego_data:/app/data -v ego_config:/app/config -v ego_certs:/app/certs {tags[0]}")

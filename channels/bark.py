#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""Bark iOS 推送"""

import requests
import log
from . import BaseChannel


class Bark(BaseChannel):
    CHANNEL_TYPE = "bark"
    CHANNEL_NAME = "Bark"

    def __init__(self, config: dict):
        super().__init__(config)
        self.server_url = config.get("server_url", "https://api.day.app")
        self.device_key = config.get("device_key", "")

    def send(self, title: str, content: str) -> bool:
        if not self.device_key:
            return False
        try:
            resp = requests.post(
                f"{self.server_url}/{self.device_key}",
                json={"title": title, "body": content, "group": "EverywhereYouGo"},
                timeout=15
            )
            ok = resp.json().get("code") == 200
            if not ok:
                log.logger.error(f"Bark send: {resp.json().get('message')}")
            return ok
        except Exception as e:
            log.logger.error(f"Bark send: {e}")
            return False

    def test(self) -> bool:
        if not self.device_key:
            return False
        try:
            resp = requests.post(
                f"{self.server_url}/{self.device_key}",
                json={"title": "EverywhereYouGo 测试", "body": "通道测试成功！", "group": "EverywhereYouGo"},
                timeout=15
            )
            return resp.json().get("code") == 200
        except:
            return False

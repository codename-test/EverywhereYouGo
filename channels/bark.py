#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""Bark iOS 推送"""

import requests
import log
class Channel(BaseChannel):
    CHANNEL_TYPE = "bark"
    CHANNEL_NAME = "Bark"
    CONFIG_FIELDS = [
    {
        "name": "server_url",
        "type": "text",
        "label": "Server URL",
        "label_zh": "服务器地址",
        "desc": "Bark server address",
        "desc_zh": "Bark 服务器地址，不填则用官方公共服务",
        "placeholder": "https://api.day.app",
        "required": False,
        "default": "https://api.day.app"
    },
    {
        "name": "device_key",
        "type": "text",
        "label": "Device Key",
        "label_zh": "设备密钥",
        "desc": "Your Bark device key",
        "desc_zh": "Bark App 中复制的设备密钥",
        "placeholder": "",
        "required": True,
        "default": ""
    }
]

    def __init__(self, config: dict):
        super().__init__(config)
        self.server_url = config.get("server_url", "https://api.day.app")
        self.device_key = config.get("device_key", "")

    def send(self, title: str, content: str) -> tuple:
        if not self.device_key:
            return False, "device_key is empty"
        try:
            resp = requests.post(
                f"{self.server_url}/{self.device_key}",
                json={"title": title, "body": content, "group": "EverywhereYouGo"},
                timeout=15
            )
            body = resp.json()
            if body.get("code") == 200:
                return True, ""
            errmsg = body.get("message", "unknown error")
            log.logger.error(f"Bark send: {errmsg}")
            return False, errmsg
        except Exception as e:
            log.logger.error(f"Bark send: {e}")
            return False, str(e)

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
        except Exception:
            return False

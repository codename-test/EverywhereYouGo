#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""飞书机器人"""

import requests
import log
from . import BaseChannel


class Feishu(BaseChannel):
    CHANNEL_TYPE = "feishu"
    CHANNEL_NAME = "飞书"

    def __init__(self, config: dict):
        super().__init__(config)
        self.webhook_url = config.get("webhook_url", "")

    def send(self, title: str, content: str) -> bool:
        if not self.webhook_url:
            return False
        try:
            resp = requests.post(self.webhook_url, json={
                "msg_type": "interactive",
                "card": {
                    "header": {
                        "title": {"tag": "plain_text", "content": title},
                        "template": "blue",
                    },
                    "elements": [{
                        "tag": "markdown",
                        "content": content,
                    }],
                }
            }, timeout=15)
            data = resp.json()
            ok = data.get("code") == 0 or data.get("StatusCode") == 0
            if not ok:
                log.logger.error(f"Feishu send: {data}")
            return ok
        except Exception as e:
            log.logger.error(f"Feishu send: {e}")
            return False

    def test(self) -> bool:
        if not self.webhook_url:
            return False
        try:
            resp = requests.post(self.webhook_url, json={
                "msg_type": "text",
                "content": {"text": "EverywhereYouGo 通道测试成功！"}
            }, timeout=15)
            data = resp.json()
            return data.get("code") == 0 or data.get("StatusCode") == 0
        except:
            return False

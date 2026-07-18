#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""钉钉机器人"""

import requests
import log
class Channel(BaseChannel):
    CHANNEL_TYPE = "dingtalk"
    CHANNEL_NAME = "钉钉"
    CONFIG_FIELDS = [
    {
        "name": "webhook_url",
        "type": "text",
        "label": "Webhook URL",
        "label_zh": "Webhook 地址",
        "desc": "DingTalk robot webhook URL",
        "desc_zh": "钉钉机器人的 Webhook 地址",
        "placeholder": "https://oapi.dingtalk.com/robot/send?access_token=xxx",
        "required": True,
        "default": ""
    }
]

    def __init__(self, config: dict):
        super().__init__(config)
        self.webhook_url = config.get("webhook_url", "")

    def send(self, title: str, content: str) -> tuple:
        if not self.webhook_url:
            return False, "webhook_url is empty"
        text = f"## {title}\n\n{content}"
        try:
            resp = requests.post(self.webhook_url, json={
                "msgtype": "markdown",
                "markdown": {"title": title, "text": text}
            }, timeout=15)
            body = resp.json()
            errcode = body.get("errcode")
            if errcode == 0:
                return True, ""
            errmsg = body.get("errmsg", f"errcode={errcode}")
            log.logger.error(f"DingTalk send: {errmsg}")
            return False, errmsg
        except Exception as e:
            log.logger.error(f"DingTalk send: {e}")
            return False, str(e)

    def test(self) -> bool:
        if not self.webhook_url:
            return False
        try:
            resp = requests.post(self.webhook_url, json={
                "msgtype": "text",
                "text": {"content": "EverywhereYouGo 通道测试成功！"}
            }, timeout=15)
            return resp.json().get("errcode") == 0
        except Exception:
            return False

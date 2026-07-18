#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""飞书机器人"""

import requests
import log
class Channel(BaseChannel):
    CHANNEL_TYPE = "feishu"
    CHANNEL_NAME = "飞书"
    CONFIG_FIELDS = [
    {
        "name": "webhook_url",
        "type": "text",
        "label": "Webhook URL",
        "label_zh": "Webhook 地址",
        "desc": "Feishu robot webhook URL",
        "desc_zh": "飞书机器人的 Webhook 地址",
        "placeholder": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
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
            code = data.get("code", -1)
            status = data.get("StatusCode", -1)
            if code == 0 or status == 0:
                return True, ""
            errmsg = data.get("msg", data.get("errmsg", f"code={code} status={status}"))
            log.logger.error(f"Feishu send: {data}")
            return False, errmsg
        except Exception as e:
            log.logger.error(f"Feishu send: {e}")
            return False, str(e)

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
        except Exception:
            return False

#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""企业微信应用号"""

import requests
import log
from . import BaseChannel


class WechatWorkAPI(BaseChannel):
    CHANNEL_TYPE = "wechat_work_api"
    CHANNEL_NAME = "企业微信应用"

    def __init__(self, config: dict):
        super().__init__(config)
        self.corp_id     = config.get("corp_id", "")
        self.corp_secret = config.get("corp_secret", "")
        self.agent_id    = config.get("agent_id", "")
        self.user_id     = config.get("user_id", "")
        self.party_id    = config.get("party_id", "")
        self.tag_id      = config.get("tag_id", "")
        self._token      = None

    def _get_token(self):
        if self._token:
            return self._token
        try:
            resp = requests.get("https://qyapi.weixin.qq.com/cgi-bin/gettoken", params={
                "corpid": self.corp_id, "corpsecret": self.corp_secret
            }, timeout=10)
            data = resp.json()
            if data.get("errcode") == 0:
                self._token = data["access_token"]
                return self._token
            log.logger.error(f"WechatWorkAPI token: {data.get('errmsg')}")
        except Exception as e:
            log.logger.error(f"WechatWorkAPI token failed: {e}")
        return ""

    def send(self, title: str, content: str) -> tuple:
        token = self._get_token()
        if not token:
            return False, "get_token failed"

        payload = {
            "touser":   self.user_id,
            "toparty":  self.party_id,
            "totag":    self.tag_id,
            "msgtype":  "text",
            "agentid":  self.agent_id,
            "text":     {"content": title + "\n\n" + content},
        }
        try:
            resp = requests.post(
                f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}",
                json=payload, timeout=15
            )
            data = resp.json()
            errcode = data.get("errcode")
            if errcode == 0:
                return True, ""
            errmsg = data.get("errmsg", f"errcode={errcode}")
            log.logger.error(f"WechatWorkAPI send: {errmsg}")
            return False, errmsg
        except Exception as e:
            log.logger.error(f"WechatWorkAPI send: {e}")
            return False, str(e)

    def test(self) -> bool:
        token = self._get_token()
        if not token:
            return False
        try:
            resp = requests.post(
                f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}",
                json={"touser": self.user_id, "toparty": self.party_id, "totag": self.tag_id,
                      "msgtype": "text", "agentid": self.agent_id,
                      "text": {"content": "EverywhereYouGo 通道测试成功！"}},
                timeout=15
            )
            return resp.json().get("errcode") == 0
        except Exception:
            return False

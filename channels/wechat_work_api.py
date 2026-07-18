#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""企业微信应用号"""

import requests
import log
class Channel(BaseChannel):
    CHANNEL_TYPE = "wechat_work_api"
    CHANNEL_NAME = "企业微信应用"
    CONFIG_FIELDS = [
    {
        "name": "corp_id",
        "type": "text",
        "label": "Corp ID",
        "label_zh": "企业 ID",
        "desc": "WeCom Corp ID",
        "desc_zh": "企业微信的企业 ID",
        "placeholder": "",
        "required": True,
        "default": ""
    },
    {
        "name": "corp_secret",
        "type": "password",
        "label": "App Secret",
        "label_zh": "应用密钥",
        "desc": "WeCom App Secret",
        "desc_zh": "应用的 Secret",
        "placeholder": "",
        "required": True,
        "default": ""
    },
    {
        "name": "agent_id",
        "type": "text",
        "label": "Agent ID",
        "label_zh": "应用 ID",
        "desc": "WeCom App Agent ID",
        "desc_zh": "应用的 AgentId",
        "placeholder": "",
        "required": True,
        "default": ""
    },
    {
        "name": "user_id",
        "type": "textarea",
        "label": "Recipients",
        "label_zh": "接收人",
        "desc": "User IDs, one per line. @all for everyone",
        "desc_zh": "接收用户 ID，每行一个。填 @all 表示全部",
        "placeholder": "@all",
        "required": False,
        "default": "@all"
    },
    {
        "name": "party_id",
        "type": "text",
        "label": "Department ID",
        "label_zh": "部门 ID",
        "desc": "Department IDs separated by |",
        "desc_zh": "部门 ID，多个用 | 分隔",
        "placeholder": "1|2",
        "required": False,
        "default": ""
    },
    {
        "name": "tag_id",
        "type": "text",
        "label": "Tag ID",
        "label_zh": "标签 ID",
        "desc": "Tag IDs separated by |",
        "desc_zh": "标签 ID，多个用 | 分隔",
        "placeholder": "1|2",
        "required": False,
        "default": ""
    }
]

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

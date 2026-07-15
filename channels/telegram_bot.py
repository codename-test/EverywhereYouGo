#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""Telegram Bot"""

import requests
import log
from . import BaseChannel


class TelegramBot(BaseChannel):
    CHANNEL_TYPE = "telegram_bot"
    CHANNEL_NAME = "Telegram"

    def __init__(self, config: dict):
        super().__init__(config)
        self.bot_token = config.get("bot_token", "")
        self.chat_id   = config.get("chat_id", "")

    def _api(self, method):
        return f"https://api.telegram.org/bot{self.bot_token}/{method}"

    def send(self, title: str, content: str) -> tuple:
        if not self.bot_token or not self.chat_id:
            return False, "bot_token or chat_id is empty"
        text = f"<b>{title}</b>\n\n{content}"
        try:
            resp = requests.post(self._api("sendMessage"), json={
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
            }, timeout=15)
            body = resp.json()
            if body.get("ok", False):
                return True, ""
            errmsg = body.get("description", "unknown error")
            log.logger.error(f"Telegram send: {errmsg}")
            return False, errmsg
        except Exception as e:
            log.logger.error(f"Telegram send: {e}")
            return False, str(e)

    def test(self) -> bool:
        if not self.bot_token or not self.chat_id:
            return False
        try:
            resp = requests.post(self._api("sendMessage"), json={
                "chat_id": self.chat_id,
                "text": "EverywhereYouGo 通道测试成功！",
                "parse_mode": "HTML",
            }, timeout=15)
            return resp.json().get("ok", False)
        except Exception:
            return False

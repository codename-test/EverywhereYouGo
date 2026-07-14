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

    def send(self, title: str, content: str) -> bool:
        if not self.bot_token or not self.chat_id:
            return False
        text = f"<b>{title}</b>\n\n{content}"
        try:
            resp = requests.post(self._api("sendMessage"), json={
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
            }, timeout=15)
            ok = resp.json().get("ok", False)
            if not ok:
                log.logger.error(f"Telegram send: {resp.json().get('description')}")
            return ok
        except Exception as e:
            log.logger.error(f"Telegram send: {e}")
            return False

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
        except:
            return False

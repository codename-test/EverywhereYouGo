#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""Channel base class and factory."""

import log


class BaseChannel:
    CHANNEL_TYPE = "base"
    CHANNEL_NAME = "Base"

    def __init__(self, config: dict):
        self.config = config

    def send(self, title: str, content: str) -> bool:
        """发送消息。子类实现。"""
        raise NotImplementedError

    def test(self) -> bool:
        """连通性测试。子类实现。"""
        raise NotImplementedError


def create_channel(channel_type: str, config: dict) -> BaseChannel:
    from .wechat_work_api import WechatWorkAPI
    from .wechat_work_bot import WechatWorkBot
    from .dingtalk import DingTalk
    from .feishu import Feishu
    from .telegram_bot import TelegramBot
    from .bark import Bark

    registry = {
        "wechat_work_api": WechatWorkAPI,
        "wechat_work_bot": WechatWorkBot,
        "dingtalk":        DingTalk,
        "feishu":          Feishu,
        "telegram_bot":    TelegramBot,
        "bark":            Bark,
    }
    cls = registry.get(channel_type)
    if not cls:
        raise ValueError(f"Unknown channel type: {channel_type}")
    return cls(config)

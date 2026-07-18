#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""Channel base class. Plugin loading is handled by channel_loader."""


class BaseChannel:
    CHANNEL_TYPE = "base"
    CHANNEL_NAME = "Base"

    def __init__(self, config: dict):
        self.config = config

    def send(self, title: str, content: str) -> tuple:
        """Send message. Subclass must implement.
        Returns (ok: bool, error: str).
        """
        raise NotImplementedError

    def test(self) -> bool:
        """Connectivity test. Subclass must implement."""
        raise NotImplementedError

#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
EGo 事件总线 — 基于 blinker 的同步信号系统。
所有模块通过总线通信，降低耦合度。

消息生命周期（单向、按序触发）：

    message.received   source_manager.process_message 发出，进入全链路
        ↓
    message.parsed     parser_engine 解析成功
        ↓
    message.routed     router_engine 路由匹配完成（携带 matched_channels）
        ↓
    message.sending    sender_engine 进入发送阶段（已入队 / 开始直发）
        ↓
    message.sent       全部通道发送成功（终态）
    message.failed     任一环节失败：解析失败（stage="parse"）/ 发送失败（stage="send"）

注意：**只有事件链一处驱动**，`process_message` 不再重复 emit 下游事件
（历史上曾因此导致每条消息被投递 3 次，见 tests/test_event_chain.py）。
"""

from blinker import Namespace

_signals = Namespace()

# ── 消息生命周期 ──
message_received = _signals.signal("message.received")
message_parsed = _signals.signal("message.parsed")
message_routed = _signals.signal("message.routed")
message_sending = _signals.signal("message.sending")
message_sent = _signals.signal("message.sent")
message_failed = _signals.signal("message.failed")

# ── 配置变更 ──
config_changed = _signals.signal("config.changed")

# ── 数据源 ──
source_started = _signals.signal("source.started")
source_stopped = _signals.signal("source.stopped")


def emit(signal, **kwargs):
    """
    触发事件。所有已注册的 handler 会被同步调用。
    返回 handler 结果列表 [(receiver, return_value), ...]。
    """
    return signal.send(None, **kwargs)


def on(signal, fn, **kwargs):
    """注册事件处理器。"""
    signal.connect(fn, **kwargs)


def off(signal, fn):
    """注销事件处理器。"""
    signal.disconnect(fn)

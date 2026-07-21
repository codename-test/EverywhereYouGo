#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
路由引擎。
监听 message.parsed 事件，执行路由匹配 + DND 检测，触发 message.routed 事件。
"""

import datetime
import log
import db
import bus
import router

# DND 队列上限
DND_QUEUE_LIMIT = 10000


def _on_message_parsed(sender, *, trace_id, source_id, msg):
    """
    message.parsed 事件处理器。
    路由匹配 + DND 检测，返回 (matched, msg) 或 None。
    """
    bindings = db.get_source_channels(source_id)
    matched = router.match_rules(bindings, msg)

    if not matched:
        log.logger.info(f"[{trace_id}] No matching channels")
        db.update_message(trace_id, status="NO_MATCH")
        return None

    # 检查是否有紧急路由（无视 DND）
    has_urgent = any(sc.get("urgent") for sc in matched)

    # DND 检测（紧急路由跳过）
    if not has_urgent:
        dnd = db.get_dnd()
        if dnd["enabled"] and _is_in_dnd(dnd["start_time"], dnd["end_time"]):
            # 检查队列上限
            pending_count = db.get_message_count(status="PENDING")
            if pending_count >= DND_QUEUE_LIMIT:
                log.logger.warning(
                    f"[{trace_id}] DND queue full ({pending_count}/{DND_QUEUE_LIMIT}), discarding message"
                )
                db.update_message(trace_id, status="DISCARDED",
                                  error=f"DND queue full ({DND_QUEUE_LIMIT})")
                return None
            log.logger.info(f"[{trace_id}] DND active, queuing ({pending_count + 1}/{DND_QUEUE_LIMIT})")
            db.update_message(trace_id, status="PENDING")
            return None

    # 触发 message.routed 事件
    bus.emit(bus.message_routed, trace_id=trace_id, source_id=source_id,
             msg=msg, matched_channels=matched)

    return matched, msg


def _is_in_dnd(start_time_str, end_time_str):
    """判断当前时间是否在勿扰时段内（支持跨午夜）。"""
    now = datetime.datetime.now().time()
    start = datetime.datetime.strptime(start_time_str, "%H:%M").time()
    end = datetime.datetime.strptime(end_time_str, "%H:%M").time()
    if start <= end:
        return start <= now <= end
    else:
        return now >= start or now <= end


def match_for_source(source_id, msg):
    """
    直接调用路由匹配（供 flush_queue / retry 使用，跳过解析直接路由）。
    返回 matched 列表，无匹配返回空列表。
    """
    bindings = db.get_source_channels(source_id)
    matched = router.match_rules(bindings, msg)
    if not matched:
        return []

    has_urgent = any(sc.get("urgent") for sc in matched)
    if not has_urgent:
        dnd = db.get_dnd()
        if dnd["enabled"] and _is_in_dnd(dnd["start_time"], dnd["end_time"]):
            return []  # DND 期间不发送

    return matched


# 注册事件处理器
bus.on(bus.message_parsed, _on_message_parsed)

#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
数据源管理器（编排层）。
v1.1 重构完成：
  - HTTP 监听 → source_listener/
  - 解析引擎 → parser_engine/
  - 路由引擎 → router_engine/
  - 发送引擎 → sender_engine/
本模块仅保留全链路编排（process_message）、队列刷新、消息重发。
"""

import json
import uuid
import hashlib
import log
import db
import bus
import router_engine
import sender_engine

# ── 从 source_listener 重导出（向后兼容） ──────
from source_listener import (
    ListenerManager,
    SourceManager,
    get_samples,
    clear_samples,
)


def _calc_parser_hash(filename):
    """计算解析器文件内容的哈希值。"""
    try:
        import os
        parser_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "parsers")
        path = os.path.join(parser_dir, filename)
        if os.path.isfile(path):
            with open(path, "rb") as f:
                return hashlib.md5(f.read()).hexdigest()[:12]
    except Exception:
        pass
    return ""


# ── 全链路处理 ──────────────────────────────

def process_message(source_id, raw_body: bytes, headers: dict, query_params: dict) -> tuple:
    """
    处理一条消息的全链路：记录 → 解析 → 路由 → 发送。
    每个步骤通过事件总线委托给对应引擎。

    Returns:
        (overall_ok: bool, msg_body: dict|None)
    """
    trace_id = str(uuid.uuid4())[:8]
    src = db.get_source(source_id)
    src_name = src["name"] if src else f"src#{source_id}"
    raw_str = raw_body.decode("utf-8", errors="replace")[:10000]

    log.logger.debug(f"[{trace_id}] Received from {src_name}: {raw_str[:2000]}")

    # 1. 记录原始消息
    db.create_message_log(trace_id, source_id, src_name, raw_str, "RECEIVED")

    # 2. 解析（parser_engine 监听 message.received）
    results = bus.emit(
        bus.message_received,
        trace_id=trace_id, source_id=source_id,
        raw_body=raw_body, headers=headers, query_params=query_params,
    )
    parse_ok, msg = _extract_result(results)
    if not parse_ok:
        return False, None

    # 3. 路由（router_engine 监听 message.parsed）
    results = bus.emit(
        bus.message_parsed,
        trace_id=trace_id, source_id=source_id, msg=msg,
    )
    route_result = _extract_result(results)
    if route_result is None:
        return True, msg

    matched, msg = route_result

    # 4. 发送（sender_engine 监听 message.routed）
    results = bus.emit(
        bus.message_routed,
        trace_id=trace_id, source_id=source_id,
        msg=msg, matched_channels=matched,
    )
    send_result = _extract_result(results)
    return (send_result[0] if send_result else True), msg


def _extract_result(results):
    """从 blinker 事件结果列表中提取第一个非 None 的返回值。"""
    if results:
        for _receiver, result in results:
            if result is not None:
                return result
    return None


# ── 队列刷新 ──────────────────────────────────

def flush_queue_for_source(source_id):
    """
    刷新某个数据源的 PENDING 队列消息。
    跳过解析，直接对存好的 msg JSON 走路由→发送。
    """
    messages = db.get_pending_messages(source_id)
    if not messages:
        return 0

    sent_count = 0
    for mq in messages:
        try:
            msg = json.loads(mq["msg_json"])

            matched = router_engine.match_for_source(source_id, msg)
            if not matched:
                db.update_message_by_id(mq["id"], status="FAILED", error="No matching channels")
                log.logger.warning(f"[Flush #{mq['id']}] Source {source_id}: no matching channels")
                continue

            ok, _ = sender_engine.send_to_channels(mq["trace_id"], source_id, msg, matched)
            if ok:
                sent_count += 1
        except Exception as e:
            db.update_message_by_id(mq["id"], status="FAILED", error=str(e)[:500])
            log.logger.error(f"[Flush #{mq['id']}] Exception: {e}")

    return sent_count


# ── 重发 ──────────────────────────────────────

def retry_message(msg_id, mode="original"):
    """
    重发一条失败消息。
    mode: "original" = 用存好的 msg_json 重发; "rerender" = 重新解析 raw_body。
    """
    rec = db.get_message_by_id(msg_id)
    if not rec:
        return False, "Message not found"
    if rec["status"] != "FAILED":
        return False, f"Status is {rec['status']}, not FAILED"

    if mode == "rerender" and rec.get("raw_body"):
        import parser_loader
        raw_body = rec["raw_body"].encode("utf-8")
        src = db.get_source(rec["source_id"])
        parser = db.get_parser(src["parser_id"]) if src and src.get("parser_id") else None
        if not parser:
            return False, "Parser not found"

        # 检查解析器版本是否变化
        old_hash = rec.get("parser_hash", "")
        new_hash = _calc_parser_hash(parser["filename"])
        if old_hash and new_hash and old_hash != new_hash:
            log.logger.warning(
                f"[Retry #{msg_id}] Parser changed: {old_hash} → {new_hash}, "
                f"re-parsing with new version"
            )

        try:
            msg = parser_loader.run_parser(parser["filename"], raw_body, {}, {})
            db.update_message_by_id(msg_id, msg_json=json.dumps(msg, ensure_ascii=False),
                                    parser_hash=new_hash)
        except Exception as e:
            return False, f"Reparse error: {e}"
    elif rec.get("msg_json"):
        try:
            msg = json.loads(rec["msg_json"])
        except Exception:
            return False, "msg_json is corrupted"
    else:
        return False, "No msg_json available"

    matched = router_engine.match_for_source(rec["source_id"], msg)
    if not matched:
        return False, "No matching channels"

    ok, _ = sender_engine.send_to_channels(rec["trace_id"], rec["source_id"], msg, matched)
    return ok, None

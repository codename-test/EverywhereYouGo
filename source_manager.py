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
    """委托 parser_loader（走 plugin_paths 解析，兼容内置/用户两个目录）。"""
    import parser_loader
    return parser_loader.calc_parser_hash(filename)


# ── 全链路处理 ──────────────────────────────

def process_message(source_id, raw_body: bytes, headers: dict, query_params: dict,
                    extra_fields: dict = None) -> tuple:
    """
    处理一条消息的全链路：记录 → 解析 → 路由 → 发送。
    每个步骤通过事件总线委托给对应引擎。

    Args:
        extra_fields: 额外字段，解析后合并到 msg 中（如 sub_path），供路由/模板使用。

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

    # 2. 事件总线驱动全链路：解析 → 路由 → 入队
    #    message.received → parser_engine → message.parsed → router_engine
    #                     → message.routed  → sender_engine
    #
    #    ⚠️ 修复记录（2026-07-28）：原实现除事件链外，自己又重复 emit 了
    #    message.parsed 与 message.routed，导致**同一条消息被投递 3 次**
    #    （解析链内各发一次 + 这里再发两次）。该 bug 自 v1.1.0（commit 47ac7f9a）
    #    起一直存在，回归测试见 tests/test_event_chain.py。
    #    extra_fields（如 sub_path）改由 parser_engine 在路由之前合并。
    results = bus.emit(
        bus.message_received,
        trace_id=trace_id, source_id=source_id,
        raw_body=raw_body, headers=headers, query_params=query_params,
        extra_fields=extra_fields,
    )
    parse_ok, msg = _extract_result(results) or (False, None)
    if not parse_ok:
        return False, None

    # 3. 路由与入队已在事件链内完成，此处不再重复触发
    return True, msg


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

def _failed_channel_ids(rec):
    """从 channel_results 取上次**失败**的 channel_id 集合。

    返回 None 表示无法判定（旧记录没有 channel_id、或没有结果），
    调用方应回退到"整条重发"的旧行为。
    """
    try:
        results = json.loads(rec.get("channel_results") or "[]")
    except Exception:
        return None
    if not results:
        return None
    ids, has_id = set(), False
    for r in results:
        if "channel_id" in r:
            has_id = True
            if not r.get("ok"):
                ids.add(r["channel_id"])
    return ids if has_id else None


def retry_message(msg_id, mode="original", scope="failed"):
    """
    重发一条失败消息。

    mode:  "original" = 用存好的 msg_json 重发; "rerender" = 重新解析 raw_body
    scope: "failed"（默认）= **只重发上次失败的渠道**，避免把已成功的渠道重复推送；
           "all" = 重发全部匹配渠道（旧行为，用于确实想整体重推的场景）
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

    # ── 渠道级重发：只挑上次失败的渠道 ──
    if scope != "all":
        failed_ids = _failed_channel_ids(rec)
        if failed_ids is None:
            log.logger.info(
                f"[Retry #{msg_id}] channel_results 缺少 channel_id，回退为整条重发"
            )
        elif not failed_ids:
            return False, "上次所有渠道均已成功，无需重发（如需整体重推请用 scope=all）"
        else:
            targets = [sc for sc in matched if sc["channel_id"] in failed_ids]
            if not targets:
                return False, ("上次失败的渠道已不在当前匹配规则中"
                               "（绑定可能已改动），无法只重发失败渠道")
            log.logger.info(
                f"[Retry #{msg_id}] Channel-level retry: "
                f"{[sc['channel_id'] for sc in targets]} of "
                f"{[sc['channel_id'] for sc in matched]} matched"
            )
            matched = targets

    ok, _ = sender_engine.send_to_channels(rec["trace_id"], rec["source_id"], msg, matched)
    return ok, None

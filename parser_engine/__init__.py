#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
解析引擎。
监听 message.received 事件，执行解析器，触发 message.parsed 事件。
从 source_manager.py 的 process_message 中拆分出来。
"""

import json
import hashlib
import log
import db
import bus
import parser_loader


def _calc_parser_hash(filename):
    """计算解析器文件内容的哈希值。"""
    try:
        import os
        parser_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "parsers")
        path = os.path.join(parser_dir, filename)
        if os.path.isfile(path):
            with open(path, "rb") as f:
                return hashlib.md5(f.read()).hexdigest()[:12]
    except Exception:
        pass
    return ""


def _on_message_received(sender, *, trace_id, source_id, raw_body, headers, query_params,
                         extra_fields=None):
    """
    message.received 事件处理器。
    执行解析器，成功时触发 message.parsed，返回 (ok, msg) 元组。

    extra_fields 在这里合并（而非调用方），因为路由发生在 message.parsed 之后，
    而这些字段（如路径路由的 sub_path）必须赶在路由之前进入 msg。
    """
    src = db.get_source(source_id)
    parser = db.get_parser(src["parser_id"]) if src and src.get("parser_id") else None

    if not parser:
        db.update_message(trace_id, status="FAILED", error="No parser assigned")
        log.logger.error(f"Source {source_id}: no parser assigned")
        return False, None

    # 计算解析器版本哈希
    parser_hash = _calc_parser_hash(parser["filename"])

    try:
        msg = parser_loader.run_parser(
            parser["filename"], raw_body, headers, query_params
        )
        log.logger.debug(
            f"[{trace_id}] Parsed by {parser['filename']} (hash={parser_hash}): title={msg.get('title', '')[:40]}"
        )
    except Exception as e:
        db.update_message(trace_id, status="FAILED", error=f"Parse error: {str(e)[:500]}")
        log.logger.error(f"[{trace_id}] Parse error: {e}")
        # 触发 message.failed 事件
        bus.emit(bus.message_failed, trace_id=trace_id, stage="parse", error=str(e)[:500])
        return False, None

    # 合并额外字段（如 sub_path）；必须在触发 message.parsed 之前
    if extra_fields and isinstance(msg, dict):
        msg.update(extra_fields)
    if isinstance(msg, dict):
        msg["_trace_id"] = trace_id

    # 存储解析结果 + 解析器版本哈希
    msg_json = json.dumps(msg, ensure_ascii=False)
    db.update_message(trace_id, status="PARSED", msg_json=msg_json, parser_hash=parser_hash)

    # 触发 message.parsed 事件（→ router_engine → message.routed → sender_engine）
    bus.emit(bus.message_parsed, trace_id=trace_id, source_id=source_id, msg=msg)

    return True, msg


# 注册事件处理器
bus.on(bus.message_received, _on_message_received)

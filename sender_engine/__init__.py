#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
发送引擎。
监听 message.routed 事件，执行去重 → 渲染 → 并行发送 → 记录结果。
"""

import threading
import json
import datetime
import concurrent.futures

import log
import db
import bus
import renderer
from channel_loader import create_channel


def _on_message_routed(sender, *, trace_id, source_id, msg, matched_channels):
    """
    message.routed 事件处理器。
    执行去重 → 渲染 → 并行发送，返回 (ok, msg) 元组。
    """
    return _do_send(trace_id, source_id, msg, matched_channels)


def _do_send(trace_id, source_id, msg, matched):
    """
    执行实际发送：去重检查 → 渲染 → 发送 → 记录 channel_results。
    """
    db.update_message(trace_id, status="SENDING")

    # 去重检查（取第一个配了 dedup_key_expr 的 binding）
    dedup_result = None
    for sc in matched:
        dedup_expr = (sc.get("dedup_key_expr") or "").strip()
        if dedup_expr:
            dedup_key = _eval_dedup_key(dedup_expr, msg)
            if dedup_key:
                window = sc.get("dedup_window", 3600) or 3600
                if db.check_dedup(dedup_key, window):
                    log.logger.info(f"[{trace_id}] Dedup hit: key={dedup_key}")
                    db.update_message(trace_id, status="DISCARDED", dedup_key=dedup_key,
                                      error=f"Dedup hit: {dedup_key} within {window}s")
                    return True, msg
                dedup_result = dedup_key
                break

    # 并行渲染 + 发送
    channel_results = []
    all_ok = True
    result_lock = threading.Lock()

    def _send_one(sc):
        nonlocal all_ok
        tmpl = db.get_template(sc["template_id"])
        ch   = db.get_channel(sc["channel_id"])
        if not tmpl or not ch or not ch["enabled"]:
            return None

        ch_name = ch["name"]
        ch_type = ch["type"]
        result = {"ch_name": ch_name, "ch_type": ch_type, "ok": False, "error": None}

        # 渲染
        try:
            rendered = renderer.render_template(
                engine=tmpl.get("engine", "jinja2"),
                title_tpl=tmpl.get("title_tpl", ""),
                content_tpl=tmpl.get("content_tpl", ""),
                msg=msg
            )
        except Exception as e:
            log.logger.error(f"[{trace_id}] Render error ({ch_name}): {e}")
            result["error"] = f"Render: {str(e)[:200]}"
            with result_lock:
                all_ok = False
            return result

        # 发送
        try:
            ch_config = json.loads(ch["config"]) if isinstance(ch["config"], str) else ch["config"]
            channel = create_channel(ch_type, ch_config)
            ok, err = channel.send(rendered["title"], rendered["content"])
            result["ok"] = ok
            if ok:
                log.logger.info(f"[{trace_id}] Sent via {ch_name}")
            else:
                result["error"] = err or "Send returned False"
                log.logger.error(f"[{trace_id}] Failed: {ch_name} — {err}")
                with result_lock:
                    all_ok = False
        except Exception as e:
            result["error"] = str(e)[:500]
            log.logger.error(f"[{trace_id}] Send error ({ch_name}): {e}")
            with result_lock:
                all_ok = False

        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(_send_one, sc) for sc in matched]
        for f in concurrent.futures.as_completed(futures):
            r = f.result()
            if r is not None:
                channel_results.append(r)

    # 记录结果
    cr_json = json.dumps(channel_results, ensure_ascii=False)
    if all_ok:
        db.update_message(trace_id, status="SUCCESS", channel_results=cr_json,
                          dedup_key=dedup_result, sent_at=dt_now_str())
    else:
        db.update_message(trace_id, status="FAILED", channel_results=cr_json,
                          dedup_key=dedup_result,
                          error=f"Some channels failed: {_summarize_failures(channel_results)}")

    return all_ok, msg


def send_to_channels(trace_id, source_id, msg, matched):
    """
    直接调用发送流程（供 flush_queue / retry 使用）。
    返回 (ok, msg) 元组。
    """
    return _do_send(trace_id, source_id, msg, matched)


def dt_now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _summarize_failures(channel_results):
    failed = [r["ch_name"] for r in channel_results if not r["ok"]]
    return ", ".join(failed) if failed else "unknown"


def _eval_dedup_key(expr, msg):
    """求值去重键表达式。支持 + 拼接多个字段，如 event+Item.Type。"""
    try:
        parts = [p.strip() for p in expr.split("+")]
        vals = []
        for part in parts:
            path = [p.strip() for p in part.split(".")]
            val = msg
            for p in path:
                if isinstance(val, dict):
                    val = val.get(p)
                else:
                    val = None
                    break
            if val is not None:
                vals.append(str(val))
            else:
                return None
        if vals:
            return "|".join(vals)
        return None
    except Exception:
        return None


# 注册事件处理器
bus.on(bus.message_routed, _on_message_routed)

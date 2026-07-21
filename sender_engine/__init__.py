#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
发送引擎。
监听 message.routed 事件：
  - Webhook 路径：入队（异步，HTTP 立即返回 200）
  - 直接路径：同步发送（供 flush_queue / retry 使用）
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
from queue_backend import get_backend


def _on_message_routed(sender, *, trace_id, source_id, msg, matched_channels):
    """
    message.routed 事件处理器（Webhook 路径）。
    去重检查后，将每个通道的发送任务入队，HTTP 立即返回。
    """
    # 去重检查
    dedup_key = None
    for sc in matched_channels:
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
            break

    # 更新消息状态
    msg_json = json.dumps(msg, ensure_ascii=False)
    db.update_message(trace_id, status="SENDING", msg_json=msg_json, dedup_key=dedup_key or "")

    # 每个通道入队一条任务
    queue = get_backend()
    for sc in matched_channels:
        queue.enqueue(
            trace_id=trace_id,
            source_id=source_id,
            msg_json=msg_json,
            channel_id=sc["channel_id"],
            template_id=sc["template_id"],
            dedup_key=dedup_key or "",
            max_retries=3,
        )

    log.logger.info(
        f"[{trace_id}] Enqueued {len(matched_channels)} channel(s) for async send"
    )
    return True, msg


def process_queue_item(item):
    """
    处理一条队列任务（被 worker 调用）。
    渲染 + 发送单个通道，返回 (ok, channel_result_dict)。
    """
    trace_id = item["trace_id"]
    channel_id = item["channel_id"]
    template_id = item["template_id"]
    msg_json = item["msg_json"]

    # 加载通道和模板
    tmpl = db.get_template(template_id)
    ch = db.get_channel(channel_id)
    if not tmpl or not ch or not ch["enabled"]:
        return False, {
            "ch_name": f"#{channel_id}",
            "ch_type": "unknown",
            "ok": False,
            "error": "Channel or template not found / disabled"
        }

    ch_name = ch["name"]
    ch_type = ch["type"]

    # 解析消息
    try:
        msg = json.loads(msg_json) if isinstance(msg_json, str) else msg_json
    except Exception as e:
        return False, {
            "ch_name": ch_name, "ch_type": ch_type,
            "ok": False, "error": f"Invalid msg_json: {e}"
        }

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
        return False, {
            "ch_name": ch_name, "ch_type": ch_type,
            "ok": False, "error": f"Render: {str(e)[:200]}"
        }

    # 发送
    try:
        ch_config = json.loads(ch["config"]) if isinstance(ch["config"], str) else ch["config"]
        channel = create_channel(ch_type, ch_config)
        ok, err = channel.send(rendered["title"], rendered["content"])
        if ok:
            log.logger.info(f"[{trace_id}] Sent via {ch_name}")
            return True, {
                "ch_name": ch_name, "ch_type": ch_type, "ok": True, "error": None
            }
        else:
            log.logger.error(f"[{trace_id}] Failed: {ch_name} — {err}")
            return False, {
                "ch_name": ch_name, "ch_type": ch_type,
                "ok": False, "error": err or "Send returned False"
            }
    except Exception as e:
        log.logger.error(f"[{trace_id}] Send error ({ch_name}): {e}")
        return False, {
            "ch_name": ch_name, "ch_type": ch_type,
            "ok": False, "error": str(e)[:500]
        }


def update_message_results(trace_id, channel_result):
    """
    追加一个通道的发送结果到 message_log，并判断是否所有通道都完成了。
    所有通道完成后更新整体状态。
    """
    rec = db.get_message(trace_id)
    if not rec:
        return

    # 解析已有结果
    try:
        results = json.loads(rec.get("channel_results") or "[]")
    except Exception:
        results = []

    # 追加新结果
    results.append(channel_result)

    # 检查是否还有队列中的任务
    queue_stats = get_backend().get_stats()
    pending_for_trace = _count_pending_for_trace(trace_id)

    cr_json = json.dumps(results, ensure_ascii=False)

    if pending_for_trace == 0:
        # 所有通道都完成了
        all_ok = all(r.get("ok") for r in results)
        if all_ok:
            db.update_message(trace_id, status="SUCCESS",
                              channel_results=cr_json, sent_at=dt_now_str())
        else:
            failed_names = _summarize_failures(results)
            db.update_message(trace_id, status="FAILED",
                              channel_results=cr_json,
                              error=f"Failed: {failed_names}")
    else:
        # 还有任务在处理中，只更新 channel_results
        db.update_message(trace_id, channel_results=cr_json)


def _count_pending_for_trace(trace_id):
    """查询队列中指定 trace_id 还有多少待处理任务。"""
    try:
        from db.connection import _conn
        r = _conn().execute(
            "SELECT COUNT(*) FROM message_queue WHERE trace_id=? AND status IN ('pending','processing')",
            (trace_id,)
        ).fetchone()
        return r[0] if r else 0
    except Exception:
        return 0


# ── 直接发送（供 flush_queue / retry 使用） ──

def send_to_channels(trace_id, source_id, msg, matched):
    """
    同步发送所有匹配通道（直接路径，不入队）。
    返回 (ok, msg) 元组。
    """
    return _do_send_direct(trace_id, source_id, msg, matched)


def _do_send_direct(trace_id, source_id, msg, matched):
    """直接并行发送所有通道（绕过队列）。"""
    db.update_message(trace_id, status="SENDING")

    # 去重
    dedup_key = None
    for sc in matched:
        dedup_expr = (sc.get("dedup_key_expr") or "").strip()
        if dedup_expr:
            dedup_key = _eval_dedup_key(dedup_expr, msg)
            if dedup_key:
                window = sc.get("dedup_window", 3600) or 3600
                if db.check_dedup(dedup_key, window):
                    db.update_message(trace_id, status="DISCARDED", dedup_key=dedup_key,
                                      error=f"Dedup hit: {dedup_key} within {window}s")
                    return True, msg
            break

    channel_results = []
    all_ok = True
    result_lock = threading.Lock()

    def _send_one(sc):
        nonlocal all_ok
        tmpl = db.get_template(sc["template_id"])
        ch = db.get_channel(sc["channel_id"])
        if not tmpl or not ch or not ch["enabled"]:
            return None

        ch_name = ch["name"]
        ch_type = ch["type"]
        result = {"ch_name": ch_name, "ch_type": ch_type, "ok": False, "error": None}

        try:
            rendered = renderer.render_template(
                engine=tmpl.get("engine", "jinja2"),
                title_tpl=tmpl.get("title_tpl", ""),
                content_tpl=tmpl.get("content_tpl", ""),
                msg=msg
            )
        except Exception as e:
            result["error"] = f"Render: {str(e)[:200]}"
            with result_lock:
                all_ok = False
            return result

        try:
            ch_config = json.loads(ch["config"]) if isinstance(ch["config"], str) else ch["config"]
            channel = create_channel(ch_type, ch_config)
            ok, err = channel.send(rendered["title"], rendered["content"])
            result["ok"] = ok
            if not ok:
                result["error"] = err or "Send returned False"
                with result_lock:
                    all_ok = False
        except Exception as e:
            result["error"] = str(e)[:500]
            with result_lock:
                all_ok = False

        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(_send_one, sc) for sc in matched]
        for f in concurrent.futures.as_completed(futures):
            r = f.result()
            if r is not None:
                channel_results.append(r)

    cr_json = json.dumps(channel_results, ensure_ascii=False)
    if all_ok:
        db.update_message(trace_id, status="SUCCESS", channel_results=cr_json,
                          dedup_key=dedup_key, sent_at=dt_now_str())
    else:
        db.update_message(trace_id, status="FAILED", channel_results=cr_json,
                          dedup_key=dedup_key,
                          error=f"Some channels failed: {_summarize_failures(channel_results)}")

    return all_ok, msg


def dt_now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _summarize_failures(channel_results):
    failed = [r["ch_name"] for r in channel_results if not r.get("ok")]
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

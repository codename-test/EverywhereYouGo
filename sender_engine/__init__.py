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
import circuit_breaker
import rate_limiter
from channel_loader import create_channel
from queue_backend import get_backend

# 熔断/限流命中时的延迟重排间隔（秒）——不消耗重试次数，见 queue_backend.defer()
CIRCUIT_DEFER_SECONDS = 10
RATE_DEFER_SECONDS = 5


def _binding_dedup_key(sc, msg):
    """算某条绑定的去重键（未配置去重则返回空串）。"""
    expr = (sc.get("dedup_key_expr") or "").strip()
    if not expr:
        return ""
    try:
        return _eval_dedup_key(expr, msg) or ""
    except Exception as e:
        log.logger.warning(f"Dedup key eval failed ({expr}): {e}")
        return ""


def _record_dedup(channel_id, key):
    """发送成功后记录去重键（供后续窗口判定）。"""
    if not key:
        return
    try:
        db.dedup_record(channel_id, key)
    except Exception as e:
        log.logger.warning(f"Dedup record failed (ch={channel_id}): {e}")


def _plan_dedup(matched, msg, trace_id):
    """逐绑定做**渠道级去重**判定，返回 (plan, skipped, first_key)。

    - plan:      [(binding, dedup_key)] —— 需要发送的渠道
    - skipped:   被去重拦下的渠道数
    - first_key: 首个去重键（写入 message_log.dedup_key，仅供展示）

    异步入队路径与直接发送路径**共用**本函数，避免同一套判定逻辑写两遍
    （improvement #31）。
    """
    plan = []
    skipped = 0
    first_key = ""
    for sc in matched:
        key = _binding_dedup_key(sc, msg)
        if key:
            first_key = first_key or key
            window = sc.get("dedup_window", 3600) or 3600
            if db.dedup_hit(sc["channel_id"], key, window):
                skipped += 1
                log.logger.info(
                    f"[{trace_id}] Dedup hit (ch={sc['channel_id']}): {key} within {window}s"
                )
                continue
        plan.append((sc, key))
    return plan, skipped, first_key


def _on_message_routed(sender, *, trace_id, source_id, msg, matched_channels):
    """
    message.routed 事件处理器（Webhook 路径）。

    **渠道级去重**：逐绑定计算并判定各自的去重键，命中的只跳过该渠道，
    其余渠道照常发送；只有全部渠道都被命中时，整条消息才标记 DISCARDED。

    （旧实现取「第一个配了去重表达式的绑定」算出一个键用于所有渠道，
      一旦命中就把整条消息丢掉，且其余绑定的去重表达式被完全忽略。）
    """
    plan, skipped, first_key = _plan_dedup(matched_channels, msg, trace_id)

    msg_json = json.dumps(msg, ensure_ascii=False)

    if not plan:
        db.update_message(trace_id, status="DISCARDED", dedup_key=first_key,
                          error=f"Dedup hit on all {skipped} channel(s)")
        log.logger.info(f"[{trace_id}] All {skipped} channel(s) deduped, discarded")
        return True, msg

    db.update_message(trace_id, status="SENDING", msg_json=msg_json, dedup_key=first_key)
    bus.emit(bus.message_sending, trace_id=trace_id, source_id=source_id, msg=msg,
             channels=[sc["channel_id"] for sc, _ in plan])

    queue = get_backend()
    for sc, key in plan:
        queue.enqueue(
            trace_id=trace_id,
            source_id=source_id,
            msg_json=msg_json,
            channel_id=sc["channel_id"],
            template_id=sc["template_id"],
            dedup_key=key,
            max_retries=3,
        )

    log.logger.info(
        f"[{trace_id}] Enqueued {len(plan)} channel(s) for async send"
        + (f", {skipped} deduped" if skipped else "")
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

    # 熔断闸门：通道处于 OPEN 时直接延迟重排，不消耗重试次数
    # （故障期内让消息留在队列里等恢复，而不是被重试耗尽跌进死信队列）
    breaker = circuit_breaker.get_breaker()
    allowed, reason = breaker.should_allow(channel_id)
    if not allowed:
        log.logger.warning(f"[{trace_id}] Circuit open for {ch_name} ({reason}), deferring")
        return False, {
            "ch_name": ch_name, "ch_type": ch_type, "ok": False,
            "error": f"Circuit open: {reason}",
            "deferred": True, "defer_seconds": CIRCUIT_DEFER_SECONDS,
        }

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

    # 出站限流：拿不到令牌就延迟重排（令牌在"即将真正发送"时才取）
    limiter = rate_limiter.get_limiter()
    if not limiter.acquire(channel_id):
        log.logger.info(f"[{trace_id}] Rate limited on {ch_name}, deferring")
        return False, {
            "ch_name": ch_name, "ch_type": ch_type, "ok": False,
            "error": f"Rate limited (limit {limiter.get_rate(channel_id)}/min)",
            "deferred": True, "defer_seconds": RATE_DEFER_SECONDS,
        }

    # 发送
    try:
        ch_config = json.loads(ch["config"]) if isinstance(ch["config"], str) else ch["config"]
        channel = create_channel(ch_type, ch_config)
        ok, err = channel.send(rendered["title"], rendered["content"])
        breaker.record(channel_id, ok, err or "")
        if ok:
            # 渠道级去重：发送成功才记录本渠道自己的去重键
            _record_dedup(channel_id, item.get("dedup_key"))
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
        breaker.record(channel_id, False, str(e))
        log.logger.error(f"[{trace_id}] Send error ({ch_name}): {e}")
        return False, {
            "ch_name": ch_name, "ch_type": ch_type,
            "ok": False, "error": str(e)[:500]
        }


# ── 结果回写的并发保护（P1-3）──
# channel_results 是「读 → 改 → 写」的 JSON 列，多 worker 并发回写会丢失更新
# （lost update）；「查 pending → 置终态」也存在 check-then-act 竞争。
# 用条带锁（固定 64 把，按 trace_id 取模）把同一 trace 的回写串行化：
# 不增长、无需清理；不同 trace 偶有共享锁只影响少量并行度，不影响正确性。
_RESULT_LOCKS = [threading.Lock() for _ in range(64)]


def _result_lock(trace_id):
    return _RESULT_LOCKS[hash(str(trace_id)) % len(_RESULT_LOCKS)]


def update_message_results(trace_id, channel_result):
    """
    追加一个通道的发送结果到 message_log，并判断是否所有通道都完成了。
    所有通道完成后更新整体状态。

    同一 trace 的回写用条带锁串行化，避免多 worker 下丢失更新。
    终态事件（message.sent / message.failed）在**释放锁之后**再 emit，
    避免订阅者在同一条 trace 上回调本函数造成自锁。
    """
    with _result_lock(trace_id):
        terminal = _update_message_results_locked(trace_id, channel_result)

    if terminal == "sent":
        bus.emit(bus.message_sent, trace_id=trace_id)
    elif terminal == "failed":
        rec = db.get_message(trace_id)
        bus.emit(bus.message_failed, trace_id=trace_id, stage="send",
                 error=(rec or {}).get("error", ""))


def _update_message_results_locked(trace_id, channel_result):
    """返回终态字符串 'sent' / 'failed'，未到终态返回 None。"""
    rec = db.get_message(trace_id)
    if not rec:
        return None

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
        # 所有通道都完成了 —— 消息到达终态，在总线生命周期里收口
        all_ok = all(r.get("ok") for r in results)
        if all_ok:
            db.update_message(trace_id, status="SUCCESS",
                              channel_results=cr_json, sent_at=dt_now_str())
            return "sent"
        else:
            failed_names = _summarize_failures(results)
            db.update_message(trace_id, status="FAILED",
                              channel_results=cr_json,
                              error=f"Failed: {failed_names}")
            return "failed"
    else:
        # 还有任务在处理中，只更新 channel_results
        db.update_message(trace_id, channel_results=cr_json)
        return None


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
    """直接并行发送所有通道（绕过队列）。

    渠道级去重：逐绑定判定，命中的只跳过该渠道；全部命中才整条 DISCARDED。
    发送成功时记录该渠道自己的去重键。
    """
    plan, skipped, first_key = _plan_dedup(matched, msg, trace_id)

    if not plan:
        db.update_message(trace_id, status="DISCARDED", dedup_key=first_key,
                          error=f"Dedup hit on all {skipped} channel(s)")
        return True, msg

    db.update_message(trace_id, status="SENDING", dedup_key=first_key)
    bus.emit(bus.message_sending, trace_id=trace_id, source_id=source_id, msg=msg,
             channels=[sc["channel_id"] for sc, _ in plan])

    channel_results = []
    all_ok = True
    result_lock = threading.Lock()

    def _send_one(sc, dedup_key):
        nonlocal all_ok
        tmpl = db.get_template(sc["template_id"])
        ch = db.get_channel(sc["channel_id"])
        if not tmpl or not ch or not ch["enabled"]:
            return None

        ch_name = ch["name"]
        ch_type = ch["type"]
        # channel_id 是渠道级重发/去重的定位依据，必须随结果落库
        result = {"ch_name": ch_name, "ch_type": ch_type, "ok": False, "error": None,
                  "channel_id": sc["channel_id"]}

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
            else:
                _record_dedup(sc["channel_id"], dedup_key)
        except Exception as e:
            result["error"] = str(e)[:500]
            with result_lock:
                all_ok = False

        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(_send_one, sc, key) for sc, key in plan]
        for f in concurrent.futures.as_completed(futures):
            r = f.result()
            if r is not None:
                channel_results.append(r)

    cr_json = json.dumps(channel_results, ensure_ascii=False)
    if all_ok:
        db.update_message(trace_id, status="SUCCESS", channel_results=cr_json,
                          dedup_key=first_key, sent_at=dt_now_str())
        bus.emit(bus.message_sent, trace_id=trace_id, source_id=source_id)
    else:
        failed = _summarize_failures(channel_results)
        db.update_message(trace_id, status="FAILED", channel_results=cr_json,
                          dedup_key=first_key, error=f"Some channels failed: {failed}")
        bus.emit(bus.message_failed, trace_id=trace_id, stage="send", error=failed)

    return all_ok, msg


def dt_now_str():
    """返回 **UTC** 时间字符串，与 SQLite 的 `CURRENT_TIMESTAMP` 保持一致。

    原先用 `datetime.now()`（本地时间）写入 sent_at，而 created_at 由
    `CURRENT_TIMESTAMP` 生成（UTC），两者相差一个时区偏移——
    消息列表里"创建时间/发送时间"会对不上，按二者计算的延迟也会错好几小时。
    """
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


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

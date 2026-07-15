#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
数据源管理器。
负责启动/停止各数据源的 HTTP 监听端口，接收原始数据并触发解析→路由→发送全链路。
v1.1 — 统一 message_log，新增去重、channel_results 记录。
"""

import threading
import json
import uuid
import datetime
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import log
import db
import parser_loader
import router
import renderer
from channels import create_channel

# 存储样本数据：{source_id: [{"body":..., "headers":..., "query":...}, ...]}
_sample_store = {}
_sample_lock = threading.Lock()
MAX_SAMPLES = 20


class _HookHandler(BaseHTTPRequestHandler):
    """Webhook HTTP 处理器"""

    def do_POST(self):
        source_id = getattr(self.server, "source_id", None)
        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length) if content_length > 0 else b""

        # 解析 query params
        parsed = urlparse(self.path)
        query_params = {k: v[0] if len(v) == 1 else v for k, v in parse_qs(parsed.query).items()}

        # 提取 headers（去掉 host 等）
        headers = dict(self.headers)

        # 保存样本数据
        _save_sample(source_id, raw_body, headers, query_params)

        # 触发全链路
        ok, msg_body = process_message(source_id, raw_body, headers, query_params)

        if ok:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        else:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b'{"status":"error"}')

    def log_message(self, format, *args):
        pass


class SourceManager:
    """管理所有数据源的 HTTP 服务"""

    def __init__(self):
        self._servers = {}
        self._threads = {}

    def start_all(self):
        for s in db.get_sources():
            if s["enabled"]:
                self.start_source(s["id"])

    def start_source(self, source_id):
        src = db.get_source(source_id)
        if not src or not src["enabled"]:
            return

        if source_id in self._servers:
            self.stop_source(source_id)

        try:
            server = HTTPServer(("0.0.0.0", src["port"]), _HookHandler)
            server.source_id = source_id
            self._servers[source_id] = server

            t = threading.Thread(
                target=server.serve_forever,
                daemon=True,
                name=f"source-{source_id}"
            )
            t.start()
            self._threads[source_id] = t
            log.logger.info(f"Source [{src['name']}] listening on port {src['port']}")
        except Exception as e:
            log.logger.error(f"Failed to start source {source_id}: {e}")

    def stop_source(self, source_id):
        if source_id in self._servers:
            self._servers[source_id].shutdown()
            del self._servers[source_id]
        if source_id in self._threads:
            del self._threads[source_id]
        log.logger.info(f"Source {source_id} stopped")

    def stop_all(self):
        for sid in list(self._servers.keys()):
            self.stop_source(sid)

    def restart_source(self, source_id):
        self.stop_source(source_id)
        self.start_source(source_id)


# ── 全链路处理 ──────────────────────────────

def process_message(source_id, raw_body: bytes, headers: dict, query_params: dict) -> tuple:
    """
    处理一条消息的全链路：记录 → 解析 → DND 检测(含紧急路由) → 去重 → 路由 → 渲染 → 发送。

    Returns:
        (overall_ok: bool, msg_body: dict|None)
    """
    trace_id = str(uuid.uuid4())[:8]
    src = db.get_source(source_id)
    src_name = src["name"] if src else f"src#{source_id}"
    raw_str = raw_body.decode("utf-8", errors="replace")[:10000]

    # 1. 记录原始消息
    db.create_message_log(trace_id, source_id, src_name, raw_str, "RECEIVED")

    # 2. 解析
    parser = db.get_parser(src["parser_id"]) if src and src.get("parser_id") else None
    if not parser:
        db.update_message(trace_id, status="FAILED", error="No parser assigned")
        log.logger.error(f"Source {source_id}: no parser assigned")
        return False, None

    try:
        msg = parser_loader.run_parser(
            parser["filename"], raw_body, headers, query_params
        )
        log.logger.debug(f"[{trace_id}] Parsed by {parser['filename']}: title={msg.get('title','')[:40]}")
    except Exception as e:
        db.update_message(trace_id, status="FAILED", error=f"Parse error: {str(e)[:500]}")
        log.logger.error(f"[{trace_id}] Parse error: {e}")
        return False, None

    # 2.5 存解析结果
    msg_json = json.dumps(msg, ensure_ascii=False)
    db.update_message(trace_id, status="PARSED", msg_json=msg_json)

    # 3. 路由匹配
    bindings = db.get_source_channels(source_id)
    matched = router.match_rules(bindings, msg)
    if not matched:
        log.logger.info(f"[{trace_id}] No matching channels")
        db.update_message(trace_id, status="NO_MATCH")
        return True, msg

    # 4. 检查是否有紧急路由（无视 DND）
    has_urgent = any(sc.get("urgent") for sc in matched)
    
    # 5. DND 检测（紧急路由跳过）
    if not has_urgent:
        dnd = db.get_dnd()
        if dnd["enabled"] and _is_in_dnd(dnd["start_time"], dnd["end_time"]):
            log.logger.info(f"[{trace_id}] DND active, queuing")
            db.update_message(trace_id, status="PENDING")
            return True, msg

    # 6. 去重 + 发送
    return _do_send(trace_id, source_id, src_name, msg, matched)


def _do_send(trace_id, source_id, src_name, msg, matched):
    """
    执行实际发送：去重检查 → 渲染 → 发送 → 记录 channel_results。
    matched 是已经匹配好的 source_channel 列表。
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
                    return True, msg  # 不算失败
                dedup_result = dedup_key  # 记住 dedup key 供后续写入
                break  # 只检查第一个配置了 dedup 的 binding

    # 并行渲染 + 发送
    import concurrent.futures
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

    # 用线程池并行发送
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


def dt_now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _summarize_failures(channel_results):
    failed = [r["ch_name"] for r in channel_results if not r["ok"]]
    return ", ".join(failed) if failed else "unknown"


def _eval_dedup_key(expr, msg):
    """求值去重键表达式。简单模式：直接取 msg dict 中的路径。"""
    try:
        # 安全：只用 json 路径，不用 eval
        parts = [p.strip() for p in expr.split(".")]
        val = msg
        for p in parts:
            if isinstance(val, dict):
                val = val.get(p)
            else:
                return None
        if val is not None:
            return str(val)
        return None
    except Exception:
        return None


# ── DND & Queue ───────────────────────────────

def _is_in_dnd(start_time_str, end_time_str):
    """判断当前时间是否在勿扰时段内（支持跨午夜）。"""
    now = datetime.datetime.now().time()
    start = datetime.datetime.strptime(start_time_str, "%H:%M").time()
    end = datetime.datetime.strptime(end_time_str, "%H:%M").time()
    if start <= end:
        return start <= now <= end
    else:
        return now >= start or now <= end


def flush_queue_for_source(source_id):
    """
    刷新某个数据源的 PENDING 队列消息。
    跳过解析，直接对存好的 msg JSON 走路由→渲染→发送。
    """
    messages = db.get_pending_messages(source_id)
    if not messages:
        return 0

    src = db.get_source(source_id)
    src_name = src["name"] if src else f"src#{source_id}"
    sent_count = 0

    for mq in messages:
        try:
            msg = json.loads(mq["msg_json"])
            bindings = db.get_source_channels(source_id)
            matched = router.match_rules(bindings, msg)

            if not matched:
                db.update_message_by_id(mq["id"], status="FAILED", error="No matching channels")
                log.logger.warning(f"[Flush #{mq['id']}] Source {source_id}: no matching channels")
                continue

            # 复用 _do_send（会做去重检查）
            ok, _ = _do_send(mq["trace_id"], source_id, src_name, msg, matched)
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

    src_name = rec.get("source_name", "") or f"src#{rec['source_id']}"

    if mode == "rerender" and rec.get("raw_body"):
        # 重新解析
        raw_body = rec["raw_body"].encode("utf-8")
        src = db.get_source(rec["source_id"])
        parser = db.get_parser(src["parser_id"]) if src and src.get("parser_id") else None
        if not parser:
            return False, "Parser not found"
        try:
            msg = parser_loader.run_parser(parser["filename"], raw_body, {}, {})
            db.update_message_by_id(msg_id, msg_json=json.dumps(msg, ensure_ascii=False))
        except Exception as e:
            return False, f"Reparse error: {e}"
    elif rec.get("msg_json"):
        try:
            msg = json.loads(rec["msg_json"])
        except Exception:
            return False, "msg_json is corrupted"
    else:
        return False, "No msg_json available"

    bindings = db.get_source_channels(rec["source_id"])
    matched = router.match_rules(bindings, msg)
    if not matched:
        return False, "No matching channels"

    ok, _ = _do_send(rec["trace_id"], rec["source_id"], src_name, msg, matched)
    return ok, None


# ── 样本数据管理 ──────────────────────────────

def _save_sample(source_id, raw_body, headers, query_params):
    with _sample_lock:
        if source_id not in _sample_store:
            _sample_store[source_id] = []
        body_str = raw_body.decode("utf-8", errors="replace")[:50000]
        try:
            body_obj = json.loads(body_str)
            body_str = json.dumps(body_obj, ensure_ascii=False, indent=2)
        except Exception:
            pass

        _sample_store[source_id].insert(0, {
            "body":         body_str,
            "headers":      dict(headers),
            "query_params": query_params,
        })
        if len(_sample_store[source_id]) > MAX_SAMPLES:
            _sample_store[source_id] = _sample_store[source_id][:MAX_SAMPLES]


def get_samples(source_id, count=10):
    with _sample_lock:
        samples = _sample_store.get(source_id, [])
        return samples[:count]


def clear_samples(source_id):
    with _sample_lock:
        _sample_store.pop(source_id, None)

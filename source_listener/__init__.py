#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
数据源 HTTP 监听器。
从 source_manager.py 拆分：负责启动/停止 HTTP 服务器、接收 Webhook、管理样本数据。
收到请求后通过事件总线触发后续处理链路。
"""

import threading
import json
import log
import db
import bus
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# ── 安全限制 ──────────────────────────────
MAX_BODY_SIZE = 5 * 1024 * 1024  # 5 MB

# ── 样本数据存储 ──────────────────────────────
_sample_store = {}
_sample_lock = threading.Lock()
MAX_SAMPLES = 20


def _save_sample(source_id, raw_body, headers, query_params):
    """保存一条样本数据。"""
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
    """获取某个数据源的样本数据。"""
    with _sample_lock:
        samples = _sample_store.get(source_id, [])
        return samples[:count]


def clear_samples(source_id):
    """清空某个数据源的样本数据。"""
    with _sample_lock:
        _sample_store.pop(source_id, None)


# ── HTTP Handler ──────────────────────────────

class _HookHandler(BaseHTTPRequestHandler):
    """Webhook HTTP 处理器。收到 POST 后通过事件总线触发处理。"""

    # 超时设置
    timeout = 60  # 整体超时 60 秒

    def do_POST(self):
        source_id = getattr(self.server, "source_id", None)
        content_length = int(self.headers.get("Content-Length", 0))

        # Body 大小限制
        if content_length > MAX_BODY_SIZE:
            log.logger.warning(f"Source [{source_id}] Body too large: {content_length} bytes (limit {MAX_BODY_SIZE})")
            self.send_response(413)
            self.end_headers()
            self.wfile.write(b'{"status":"error","error":"Payload too large"}')
            return

        raw_body = self.rfile.read(content_length) if content_length > 0 else b""

        # 解析 query params
        parsed = urlparse(self.path)
        query_params = {k: v[0] if len(v) == 1 else v for k, v in parse_qs(parsed.query).items()}

        # 提取 headers
        headers = dict(self.headers)

        # 保存样本数据
        _save_sample(source_id, raw_body, headers, query_params)

        # 通过 process_message 触发全链路（兼容层）
        from source_manager import process_message
        ok, msg_body = process_message(source_id, raw_body, headers, query_params)

        if ok:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        else:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b'{"status":"error"}')

    def handle_one_request(self):
        """覆盖以捕获超时异常。"""
        try:
            super().handle_one_request()
        except TimeoutError:
            log.logger.warning(f"Source [{getattr(self.server, 'source_id', '?')}] Request timed out")
        except Exception:
            pass

    def log_message(self, format, *args):
        pass


# ── ListenerManager ──────────────────────────

class ListenerManager:
    """管理所有数据源的 HTTP 服务（原 SourceManager）。"""

    def __init__(self):
        self._servers = {}
        self._threads = {}

    def start_all(self):
        """启动所有已启用的数据源监听。"""
        for s in db.get_sources():
            if s["enabled"]:
                self.start_source(s["id"])

    def start_source(self, source_id):
        """启动单个数据源的 HTTP 监听。"""
        src = db.get_source(source_id)
        if not src or not src["enabled"]:
            return

        if source_id in self._servers:
            self.stop_source(source_id)

        try:
            server = HTTPServer(("0.0.0.0", src["port"]), _HookHandler)
            server.source_id = source_id
            server.timeout = 10  # 读取超时 10 秒
            self._servers[source_id] = server

            t = threading.Thread(
                target=server.serve_forever,
                daemon=True,
                name=f"source-{source_id}"
            )
            t.start()
            self._threads[source_id] = t
            log.logger.info(f"Source [{src['name']}] listening on port {src['port']}")

            # 触发 source.started 事件
            bus.emit(bus.source_started, source_id=source_id)
        except Exception as e:
            log.logger.error(f"Failed to start source {source_id}: {e}")

    def stop_source(self, source_id):
        """停止单个数据源的 HTTP 监听。"""
        if source_id in self._servers:
            self._servers[source_id].shutdown()
            del self._servers[source_id]
        if source_id in self._threads:
            del self._threads[source_id]
        log.logger.info(f"Source {source_id} stopped")

        # 触发 source.stopped 事件
        bus.emit(bus.source_stopped, source_id=source_id)

    def stop_all(self):
        """停止所有数据源监听。"""
        for sid in list(self._servers.keys()):
            self.stop_source(sid)

    def restart_source(self, source_id):
        """重启单个数据源。"""
        self.stop_source(source_id)
        self.start_source(source_id)


# ── 向后兼容：旧名 SourceManager ─────────────

SourceManager = ListenerManager

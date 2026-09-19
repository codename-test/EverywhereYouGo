#!/usr/bin/python3
# -*- coding: UTF-8 -*-
"""
数据源 HTTP 监听器。
从 source_manager.py 拆分：负责启动/停止 HTTP 服务器、接收 Webhook、管理样本数据。
收到请求后通过事件总线触发后续处理链路。
"""

import os
import threading
import collections
import json
import log
import db
import bus
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# ── 安全限制 ──────────────────────────────
MAX_BODY_SIZE = 5 * 1024 * 1024  # 5 MB

# ── 入口并发 ──────────────────────────────
# 每个数据源一个固定大小的工作线程池。线程复用使 db/connection.py 的
# threading.local() 连接随之复用，而不是「每请求新建一个 SQLite 连接」。
#
# 关于默认值 8（实测依据，2026-07-28，测试机 4 核）：
#   吞吐在并发 1 时最高（≈48 req/s），并发升高不再提升（受单进程串行段限制）；
#   而 p50 延迟随并发线性增长（1→21ms, 8→131ms, 16→218ms）。
#   线程池的意义是「慢请求不阻塞其它请求」，而非提升吞吐，故取较小值以压低延迟。
INGRESS_WORKERS = int(os.getenv("EGO_INGRESS_WORKERS", "8"))
# 等待队列上限，超过则直接回 503（背压），防止请求无限堆积
INGRESS_MAX_QUEUE = int(os.getenv("EGO_INGRESS_MAX_QUEUE", "200"))

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
        except (BrokenPipeError, ConnectionResetError):
            # 客户端提前断开连接，属正常现象，仅记调试日志
            log.logger.debug(f"Source [{getattr(self.server, 'source_id', '?')}] Client disconnected")
        except Exception as e:
            log.logger.warning(f"Source [{getattr(self.server, 'source_id', '?')}] Request handling error: {e}")

    def log_message(self, format, *args):
        pass


# ── 入口工作线程池 ──────────────────────────

class _IngressPool:
    """固定大小的工作线程池（守护线程 + 有界队列）。

    相比 socketserver 的「每请求开一个新线程」：
      - 线程复用 → db/connection.py 的 threading.local() 连接被复用；
      - 守护线程 → 进程退出不被在途请求阻塞（不会引入 atexit join 延迟）；
      - 队列有界 → submit() 在满载时返回 False，由调用方回 503 做背压。
    """

    def __init__(self, max_workers, max_queue):
        self._max_queue = max_queue
        self._queue = collections.deque()
        self._cond = threading.Condition()
        self._stopped = False
        for i in range(max_workers):
            threading.Thread(
                target=self._worker, daemon=True, name=f"ingress-{i}"
            ).start()

    def submit(self, fn, *args):
        """入队。队列已满返回 False（调用方据此拒绝请求）。"""
        with self._cond:
            if self._stopped or len(self._queue) >= self._max_queue:
                return False
            self._queue.append((fn, args))
            self._cond.notify()
            return True

    def _worker(self):
        while True:
            with self._cond:
                while not self._queue and not self._stopped:
                    self._cond.wait()
                if self._stopped:
                    return
                fn, args = self._queue.popleft()
            try:
                fn(*args)
            except Exception as e:
                log.logger.warning(f"Ingress worker error: {e}")

    def shutdown(self):
        with self._cond:
            self._stopped = True
            self._cond.notify_all()


# ── 入口 HTTP 服务（固定线程池） ──────────────

class _ThreadPoolHTTPServer(HTTPServer):
    """固定大小线程池的 HTTP 服务。

    原实现用单线程 HTTPServer：同一数据源上的请求被**串行**处理，
    只要有一个慢解析器，该数据源上所有后续请求都要排队等待。
    改为线程池后，慢请求不再阻塞其它请求。

    （路径路由走 Flask，`run_simple(threaded=True)` 本就并发，不受此影响。）
    """

    # HTTPServer 默认 allow_reuse_address=1；此处显式声明以免误改
    allow_reuse_address = True
    # 监听队列（accept backlog）。默认值仅 5，突发流量下内核会拒掉多余连接，
    # 客户端只能等 TCP SYN 重传（约 1s）——实测并发 16/32 时出现 ~1.1s 长尾。
    request_queue_size = 128

    def __init__(self, server_address, handler_cls,
                 max_workers=INGRESS_WORKERS, max_queue=INGRESS_MAX_QUEUE):
        super().__init__(server_address, handler_cls)
        self._pool = _IngressPool(max_workers, max_queue)

    def process_request(self, request, client_address):
        if not self._pool.submit(self._process_request_thread, request, client_address):
            log.logger.warning(
                f"Source [{getattr(self, 'source_id', '?')}] ingress queue full "
                f"({INGRESS_MAX_QUEUE}), rejecting connection from {client_address}"
            )
            try:
                request.sendall(
                    b"HTTP/1.0 503 Service Unavailable\r\n"
                    b"Content-Length: 0\r\n"
                    b"Connection: close\r\n\r\n"
                )
            except Exception:
                pass
            self.shutdown_request(request)
            return

    def _process_request_thread(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)

    def handle_error(self, request, client_address):
        """覆盖默认实现：异常写入应用日志，而非直接打到 stderr。"""
        import traceback
        log.logger.warning(
            f"Source [{getattr(self, 'source_id', '?')}] unhandled error in request "
            f"from {client_address}: {traceback.format_exc()}"
        )

    def server_close(self):
        try:
            super().server_close()
        finally:
            self._pool.shutdown()


# ── ListenerManager ──────────────────────────

class ListenerManager:
    """管理所有数据源的 HTTP 服务（原 SourceManager）。"""

    def __init__(self):
        self._servers = {}
        self._threads = {}

    def start_all(self):
        """启动所有已启用的数据源监听。"""
        for s in db.get_sources():
            if s["enabled"] and s.get("port") and not s.get("parent_id"):
                self.start_source(s["id"])

    def start_source(self, source_id):
        """启动单个数据源的 HTTP 监听。"""
        src = db.get_source(source_id)
        if not src or not src["enabled"] or not src.get("port"):
            return

        if source_id in self._servers:
            self.stop_source(source_id)

        try:
            server = _ThreadPoolHTTPServer(("0.0.0.0", src["port"]), _HookHandler)
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
            server = self._servers.pop(source_id)
            server.shutdown()       # 停止 serve_forever 循环
            server.server_close()   # 释放监听 socket 与工作线程池
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

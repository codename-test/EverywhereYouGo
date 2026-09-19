# tests/test_ingress_concurrency.py
"""入口并发测试：端口数据源必须并发处理请求。

原实现用单线程 HTTPServer，同一数据源上的请求被串行处理——
一个慢解析器会阻塞该数据源上所有后续请求。
"""
import sys
import os
import time
import threading
import tempfile
import shutil
import http.client
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from http.server import BaseHTTPRequestHandler

# 使用临时数据库（source_listener 模块级 import db）
_test_db_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_test_db_dir, "test_ego.db")

import source_listener


SLEEP = 0.4
CONCURRENCY = 4


class _SlowHandler(BaseHTTPRequestHandler):
    """每次请求固定耗时 SLEEP 秒，用于暴露串行行为。"""

    protocol_version = "HTTP/1.0"

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        if n:
            self.rfile.read(n)
        time.sleep(SLEEP)
        body = b'{"status":"ok"}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _fire(port, results, lock):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    try:
        conn.request("POST", "/", body=b"{}",
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        resp.read()
        with lock:
            results.append(resp.status)
    except Exception as e:
        with lock:
            results.append(f"ERR:{e}")
    finally:
        conn.close()


class TestIngressConcurrency:
    @classmethod
    def setup_class(cls):
        cls.server = source_listener._ThreadPoolHTTPServer(
            ("127.0.0.1", 0), _SlowHandler, max_workers=8, max_queue=50
        )
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def teardown_class(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(_test_db_dir, ignore_errors=True)

    def test_concurrent_requests_not_serialized(self):
        """4 个并发请求的总耗时应远小于串行所需的 4*SLEEP。"""
        results = []
        lock = threading.Lock()
        threads = [threading.Thread(target=_fire, args=(self.port, results, lock))
                   for _ in range(CONCURRENCY)]

        start = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.time() - start

        assert results == [200] * CONCURRENCY, f"非全部 200: {results}"
        serial = SLEEP * CONCURRENCY
        assert elapsed < serial * 0.6, (
            f"耗时 {elapsed:.2f}s 接近串行基线 {serial:.2f}s，说明请求被串行处理"
        )

    def test_worker_threads_are_reused(self):
        """线程池应固定大小且被复用（不是每请求开新线程）。"""
        before = {t.name for t in threading.enumerate() if t.name.startswith("ingress-")}
        assert len(before) == 8, f"期望 8 个 ingress 线程，实际 {before}"

        results = []
        lock = threading.Lock()
        threads = [threading.Thread(target=_fire, args=(self.port, results, lock))
                   for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        after = {t.name for t in threading.enumerate() if t.name.startswith("ingress-")}
        assert after == before, f"线程集合发生变化（未复用）：{before} → {after}"


class TestIngressBackpressure:
    def test_queue_full_returns_503(self):
        """队列打满时应回 503，而不是无限堆积。"""
        server = source_listener._ThreadPoolHTTPServer(
            ("127.0.0.1", 0), _SlowHandler, max_workers=1, max_queue=1
        )
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            results = []
            lock = threading.Lock()
            threads = [threading.Thread(target=_fire, args=(port, results, lock))
                       for _ in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert 503 in results, f"期望出现 503 背压，实际: {results}"
        finally:
            server.shutdown()
            server.server_close()

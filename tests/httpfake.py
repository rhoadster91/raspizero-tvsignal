"""A tiny threaded HTTP(S) server for tests. Handler: fn(handler) -> (status, headers, body)."""

from __future__ import annotations

import contextlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable


class _Handler(BaseHTTPRequestHandler):
    route: Callable  # set per server

    def do_GET(self):  # noqa: N802
        status, headers, body = self.server.route(self)
        if not isinstance(body, (bytes, str)):
            body = json.dumps(body)
        if isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@contextlib.contextmanager
def serve(route, host="127.0.0.1", port=0, ssl_context=None):
    server = ThreadingHTTPServer((host, port), _Handler)
    server.daemon_threads = True
    server.route = route
    if ssl_context is not None:
        server.socket = ssl_context.wrap_socket(server.socket, server_side=True)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()

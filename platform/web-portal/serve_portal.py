#!/usr/bin/env python3
"""Локальный веб-портал (dev): статика webapp/ + прокси /v1 → бэкенд.

Повторяет схему nginx.conf на одном origin, чтобы SYM_CONFIG был не нужен
(мост по умолчанию ходит на тот же origin, live=true) и CORS не участвовал.

    python serve_portal.py [PORT] [BACKEND]
    python serve_portal.py 8080 http://127.0.0.1:8600
"""
import sys
import os
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
WEBAPP = os.path.abspath(os.path.join(HERE, "..", "..", "webapp"))
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
BACKEND = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8600"

TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".png": "image/png", ".svg": "image/svg+xml", ".woff2": "font/woff2",
    ".woff": "font/woff", ".ttf": "font/ttf",
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _proxy(self, body=None):
        url = BACKEND + self.path
        req = urllib.request.Request(url, data=body, method=self.command)
        for h in ("Content-Type", "Authorization"):
            if h in self.headers:
                req.add_header(h, self.headers[h])
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
                self.send_response(r.status)
                self.send_header("Content-Type", r.headers.get("Content-Type", "application/json"))
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as e:
            data = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", e.headers.get("Content-Type", "application/json"))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self.send_response(502)
            self.end_headers()
            self.wfile.write(str(e).encode())

    def _static(self):
        path = self.path.split("?", 1)[0]
        path = urllib.parse.unquote(path)
        if path in ("/", ""):
            path = "/Симбионт.dc.html"
        full = os.path.normpath(os.path.join(WEBAPP, path.lstrip("/")))
        if not full.startswith(WEBAPP) or not os.path.isfile(full):
            self.send_response(404)
            self.end_headers()
            return
        ext = os.path.splitext(full)[1].lower()
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", TYPES.get(ext, "application/octet-stream"))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/v1/"):
            self._proxy()
        else:
            self._static()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        if self.path.startswith("/v1/"):
            self._proxy(body)
        else:
            self.send_response(405)
            self.end_headers()

    do_PATCH = do_POST


import urllib.parse  # noqa: E402  (после класса — чтобы шапка читалась сверху)

if __name__ == "__main__":
    print("портал → http://127.0.0.1:%d/  (бэкенд: %s)" % (PORT, BACKEND))
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()

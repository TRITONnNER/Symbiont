#!/usr/bin/env python3
# Локальный статический сервер для webapp (Design-Components рантаму нужны конкурентные fetch).
import http.server, socketserver, sys, os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
class H(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
print(f"webapp → http://127.0.0.1:{port}/Симбионт.dc.html")
socketserver.ThreadingTCPServer(("127.0.0.1", port), H).serve_forever()

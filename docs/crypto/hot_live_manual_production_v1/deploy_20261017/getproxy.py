"""GET-only reverse proxy to the tunnelled upstreams. Anything else gets 405 and is counted."""
import http.server, socketserver, urllib.request, sys, json
UP = [("/crypto-api/", "http://127.0.0.1:18100/"), ("/btc-context-api/", "http://127.0.0.1:18014/"),
      ("/api/", "http://127.0.0.1:18000/api/"), ("/health", "http://127.0.0.1:18000/health"), ("/", "http://127.0.0.1:13000/")]
STATS = {"get": 0, "refused": 0, "status": {}}
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def refuse(self):
        STATS["refused"] += 1; self.send_response(405); self.end_headers()
    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = refuse
    def do_GET(self):
        if self.path == "/__stats": 
            b = json.dumps(STATS).encode(); self.send_response(200); self.end_headers(); self.wfile.write(b); return
        for pre, up in UP:
            if self.path.startswith(pre):
                url = up + self.path[len(pre):]; break
        STATS["get"] += 1
        req = urllib.request.Request(url, headers={k: v for k, v in self.headers.items() if k.lower() in ("accept", "user-agent", "rsc", "next-router-state-tree", "next-url", "accept-encoding")})
        try:
            r = urllib.request.urlopen(req, timeout=60); code, body, hdrs = r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            code, body, hdrs = e.code, e.read(), e.headers
        STATS["status"][str(code)] = STATS["status"].get(str(code), 0) + 1
        self.send_response(code)
        for k, v in hdrs.items():
            if k.lower() not in ("transfer-encoding", "connection", "content-length"): self.send_header(k, v)
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
class S(socketserver.ThreadingMixIn, http.server.HTTPServer): daemon_threads = True
S(("127.0.0.1", 18080), H).serve_forever()

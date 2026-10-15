"""nginx-equivalent GET-only proxy for the production UI smoke.

/ -> 13000 (Next), /crypto-api/ -> 18100 (paper 8100), /btc-context-api/ -> 18014.
Every non-GET is refused (no order can leave this proxy). /api/crypto/binance/* is fetched at
most once per path and capped, because each one is a signed Binance call on the server.
"""
import http.server, socketserver, threading, urllib.request, urllib.error, json, time, sys

LOG = sys.argv[1]
ROUTES = [("/crypto-api/", "http://127.0.0.1:18100/"), ("/btc-context-api/", "http://127.0.0.1:18014/")]
binance_cache, binance_upstream = {}, [0]
lock = threading.Lock()


def log(rec):
    with lock, open(LOG, "a") as fh:
        fh.write(json.dumps(rec) + "\n")


class H(http.server.BaseHTTPRequestHandler):
    def _refuse(self):
        log({"t": time.time(), "m": self.command, "p": self.path, "status": 405})
        self.send_response(405); self.end_headers()

    do_POST = do_PUT = do_DELETE = do_PATCH = _refuse

    def do_GET(self):
        target = "http://127.0.0.1:13000" + self.path
        for prefix, base in ROUTES:
            if self.path.startswith(prefix):
                target = base + self.path[len(prefix):]
        key = self.path.split("?")[0]
        if "/api/crypto/binance/" in key:
            with lock:
                hit = binance_cache.get(key)
                if hit is None and binance_upstream[0] >= 25:
                    hit = (503, b'{"error":{"code":"SMOKE_CAP"}}', "application/json")
            if hit is not None:
                log({"t": time.time(), "m": "GET", "p": self.path, "status": hit[0], "cached": True})
                return self._send(*hit)
        try:
            with urllib.request.urlopen(urllib.request.Request(target, headers={"Accept": self.headers.get("Accept", "*/*")}), timeout=30) as r:
                body, status, ctype = r.read(), r.status, r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            body, status, ctype = e.read(), e.code, e.headers.get("Content-Type", "")
        except Exception as e:
            body, status, ctype = str(e).encode(), 502, "text/plain"
        if "/api/crypto/binance/" in key:
            with lock:
                binance_upstream[0] += 1
                binance_cache[key] = (status, body, ctype)
        log({"t": time.time(), "m": "GET", "p": self.path, "status": status})
        self._send(status, body, ctype)

    def _send(self, status, body, ctype):
        self.send_response(status)
        self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def log_message(self, *a):
        pass


class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

S(("127.0.0.1", 18080), H).serve_forever()

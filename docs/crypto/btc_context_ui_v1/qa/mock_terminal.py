"""Fake 8100 for browser QA: answers the terminal's GETs from the recorded paper fixture and
counts (and refuses with 405) every other method. Writes its tally to tally.json."""
import json, sys, threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
paper = json.load(open(sys.argv[1]))
tally_path = sys.argv[2]
tally = {"get": 0, "non_get": [], "options": 0}
lock = threading.Lock()
HOT = {"main": "BTCUSDT", "hot": {"symbol": "ETHUSDT", "selected_at_ms": 1, "scanned_at_ms": 1, "metrics": {}, "top": []},
       "lock": {"state": "ALLOWED", "code": "HOT_SYMBOL_FLAT", "symbol": "ETHUSDT", "message": ""},
       "scanning": False, "held": [], "permitted": [], "paper": {"available": True, "reason": None}, "server_time_ms": 0}
def save():
    with open(tally_path, "w") as f: json.dump(tally, f)
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*"); self.send_header("Content-Length", str(len(data)))
        self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        with lock: tally["get"] += 1; save()
        u = urlparse(self.path); q = parse_qs(u.query); sym = (q.get("symbol") or ["BTCUSDT"])[0]
        p = u.path
        if p.startswith("/api/crypto/hot"): return self._send(200, HOT)
        rows = paper.get(sym)
        if not rows: return self._send(503, {"error": {"code": "OFFLINE"}})
        for key, name in (("/api/crypto/chart-history", "chart_history"), ("/api/crypto/state", "state"),
                          ("/api/crypto/sizing", "sizing"), ("/api/crypto/performance", "performance"),
                          ("/api/crypto/ledger", "ledger"), ("/api/crypto/trades", "trades"),
                          ("/api/crypto/pnl-breakdown", "pnl_breakdown")):
            if p.startswith(key): return self._send(200, rows[name])
        if p.startswith("/api/crypto/chart"): return self._send(200, {"symbol": sym, "bars": rows["chart_history"]["bars"]})
        return self._send(503, {"error": {"code": "OFFLINE"}})
    def _refuse(self):
        with lock: tally["non_get"].append({"method": self.command, "path": self.path}); save()
        self._send(405, {"error": {"code": "QA_REFUSES_WRITES"}})
    do_POST = do_PUT = do_PATCH = do_DELETE = _refuse
    def do_OPTIONS(self):
        with lock: tally["options"] += 1; save()
        self.send_response(204); self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET"); self.send_header("Access-Control-Allow-Headers", "*"); self.end_headers()
save()
ThreadingHTTPServer(("127.0.0.1", 8100), H).serve_forever()

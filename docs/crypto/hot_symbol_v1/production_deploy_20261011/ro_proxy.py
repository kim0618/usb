"""GET-only proxy for post-deploy UI QA.

/crypto-api/* -> production crypto API through an ssh tunnel (127.0.0.1:18100); everything else
-> the deployed frontend artifact served locally (127.0.0.1:3298). Any non-GET is refused here
and logged, so the browser cannot mutate production.
"""
import json
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = open(sys.argv[1], "a")


class H(BaseHTTPRequestHandler):
    def _refuse(self):
        LOG.write(json.dumps({"m": self.command, "p": self.path, "refused": True}) + "\n"); LOG.flush()
        self.send_response(405); self.end_headers()

    do_POST = do_PUT = do_DELETE = do_PATCH = _refuse

    def do_GET(self):
        if self.path.startswith("/crypto-api/"):
            url = "http://127.0.0.1:18100/" + self.path[len("/crypto-api/"):]
        else:
            url = "http://127.0.0.1:3298" + self.path
        LOG.write(json.dumps({"m": "GET", "p": self.path.split("?")[0]}) + "\n"); LOG.flush()
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                body, status, ctype = r.read(), r.status, r.headers.get("content-type", "")
        except urllib.error.HTTPError as e:
            body, status, ctype = e.read(), e.code, e.headers.get("content-type", "")
        except Exception:
            body, status, ctype = b"", 502, "text/plain"
        self.send_response(status)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


ThreadingHTTPServer(("127.0.0.1", 3299), H).serve_forever()

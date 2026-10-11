import hashlib
import json
import sys
from pathlib import Path

from app.crypto.terminal.api import Runtime

Runtime.feed_for = lambda self, symbol=None: None
root, cfg = Path(sys.argv[1]), Path(sys.argv[2])
rt = Runtime()
try:
    rt.build(cfg, root)
except Exception as exc:
    print(json.dumps({"build_error": f"{type(exc).__name__}: {str(exc)[:160]}"}))
    sys.exit()
out = {"error": rt.error}
for name, s in {**rt.sessions, **{k + "(retired)": v for k, v in rt.retired.items()}}.items():
    a = s.engine.account
    out[name] = {"source": s.recovery.source, "ckpt": s.recovery.checkpoint_segment,
                 "qty": str(a.position.signed_qty), "realized": str(a.realized_pnl),
                 "fees": str(a.cumulative_fees),
                 "ledger_sha": hashlib.sha256(s.engine.ledger.bytes()).hexdigest()[:16],
                 "disk_sha": hashlib.sha256((s.run_dir / "ledger.jsonl").read_bytes()).hexdigest()[:16]}
out["wallet"] = str(rt.cash.wallet_balance)
out["available"] = str(rt.cash.available_balance)
print(json.dumps(out, indent=1))

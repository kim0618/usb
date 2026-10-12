"""Builds frontend/components/__fixtures__/btc-context-snapshot-v3.json from the backend's own view."""
import json, sys, tempfile
from pathlib import Path
from app.crypto.btc_context_ui_v1 import entry_context as EC, view as V
from app.crypto.context_collector_v1 import api as CTX
from tests.crypto.test_btc_context_ui_v3 import _event, _snapshot, _store, T_OPEN

F = Path("tests/crypto/fixtures/btc_context_ui_v1")
latest = json.loads((F / "collector_latest_20261012.json").read_text())
structure = json.loads((F / "structure_layer.json").read_text())["layer"]
derivatives = json.loads((F / "derivatives_layer.json").read_text())["layer"]
tmp = Path(tempfile.mkdtemp())
state = tmp / "ctx" / "state"; state.mkdir(parents=True)
(state / "context_latest.json").write_text(json.dumps(latest))

def judged(age):
    return CTX.judged(tmp / "ctx", now_ms=latest["written_ms"] + age)

def store(name, etype, direction, qty, vwap):
    return EC.open_entry(_store(tmp / name, [_event("900", etype, direction, T_OPEN, qty, vwap, "ep-900")],
                                [_snapshot("900")]))

out = {}
for key, entry in (("live", None),
                   ("short", store("s", "OPEN_SHORT", "SHORT", "-0.010", "83612.9")),
                   ("long", store("l", "OPEN_LONG", "LONG", "0.010", "83612.9"))):
    out[key] = V.display(collector_view=judged(1_000), structure_layer=structure,
                         derivatives_layer=derivatives, now_ms=latest["written_ms"] + 1_000, entry=entry)
out["stale"] = V.display(collector_view=judged(3_600_000), structure_layer={"state": "STALE"},
                         derivatives_layer={"state": "STALE"}, now_ms=latest["written_ms"] + 3_600_000)
Path(sys.argv[1]).write_text(json.dumps(out, indent=1) + "\n")
print({k: (len(v["walls"]["ask"]), len(v["walls"]["bid"]), v["entry"].get("available")) for k, v in out.items()})
print(out["live"]["walls"]["mid"], [w["price"] for w in out["live"]["walls"]["ask"]], [w["price"] for w in out["live"]["walls"]["bid"]])

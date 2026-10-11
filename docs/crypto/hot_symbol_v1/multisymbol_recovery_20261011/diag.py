"""Run the shared-wallet replay without its final check and diff each member's ledger."""
import json
import sys
from pathlib import Path

from app.crypto.paper import wallet_recovery as wr
from app.crypto.terminal.api import Runtime

Runtime.feed_for = lambda self, symbol=None: None
root, cfg = Path(sys.argv[1]), Path(sys.argv[2])
captured = {}
original = wr.RecoveryRefused


class Capture(Exception):
    pass


real_join = wr.SharedCash.join


def spy_join(self, symbol, account):
    captured.setdefault("accounts", {})[symbol] = account
    return real_join(self, symbol, account)


wr.SharedCash.join = spy_join
rt = Runtime()
from app.crypto.terminal import api
config, tiers = api.load_run_config(cfg)
rt.root, rt.base_config = root, config
members, _ = rt._wallet_members(config)
owner = wr.WalletMember(config.instrument.symbol, root / config.run_id, config, tiers)

# Re-run the merge by hand so the engines survive a refusal.
import heapq
from app.crypto.paper.engine import PaperEngine, apply_tape_record
from app.crypto.paper.ledger import Ledger

allm = [owner, *sorted(members, key=lambda m: m.symbol)]
engines = {m.symbol: PaperEngine(m.config, m.tiers, ledger=Ledger()) for m in allm}
cash = wr.SharedCash(owner=owner.symbol)
for m in allm:
    cash.join(m.symbol, engines[m.symbol].account)
streams = {m.symbol: wr._records_after(m.run_dir, 0) for m in allm}
rank = {m.symbol: i for i, m in enumerate(allm)}
heap, n = [], 0
order = []


def push(sym):
    global n
    rec = next(streams[sym], None)
    if rec is not None:
        n += 1
        heapq.heappush(heap, (int(rec["payload"]["ts_ms"]), rank[sym], n, sym, rec))


for m in allm:
    push(m.symbol)
while heap:
    ts, _, _, sym, rec = heapq.heappop(heap)
    before = len(engines[sym].ledger.events)
    apply_tape_record(engines[sym], rec)
    if rec["kind"] == "COMMAND" or len(engines[sym].ledger.events) != before:
        order.append((ts, sym, rec["kind"], rec["payload"].get("command"),
                      [e["event_type"] for e in engines[sym].ledger.events[before:]]))
    push(sym)

for m in allm:
    stored = (m.run_dir / "ledger.jsonl").read_text().splitlines()
    rebuilt = engines[m.symbol].ledger.bytes().decode().splitlines()
    print("==", m.symbol, "stored", len(stored), "rebuilt", len(rebuilt))
    for i, (a, b) in enumerate(zip(stored, rebuilt)):
        if a != b:
            da, db = json.loads(a), json.loads(b)
            print("  first diff line", i, da.get("event_type"), db.get("event_type"), "ts", da.get("ts_ms"))
            for k in sorted(set(da) | set(db)):
                if da.get(k) != db.get(k):
                    print("   ", k, "stored=", da.get(k), "rebuilt=", db.get(k))
            break
    else:
        if len(rebuilt) > len(stored):
            print("  rebuilt has extra:", [json.loads(x)["event_type"] for x in rebuilt[len(stored):]])
print("wallet-touching sequence:")
for row in order:
    print("  ", row)

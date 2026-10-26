"""PAPER run retention V1: what may leave the server, and proof that a restart does not notice.

A real paper history is built with the real `Runtime` and real sessions (segments cut every few
records so there is history to keep or drop): BTC holding a position, a HOT run that traded and
was retired, a HOT run that never traded, ETH and SOL that only ever recorded quotes, the current
HOT symbol, and a directory the terminal never discovers carrying C1 state. The retention then
plans, writes manifests, pulls to a local COLD directory with rsync, receipts, and prunes; a
second `Runtime` boots on what is left and must hold the same money and positions.
"""
from __future__ import annotations

import asyncio
import json
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from app.crypto import symbols as symbols_module
from app.crypto.hot_coins import market as hot_market
from app.crypto.paper import analytics
from app.crypto.paper import retention as R
from tests.crypto.test_hot_symbol_v1 import exchange_row, hot_risk_payload
from tests.crypto.test_paper_boot_active_symbols import QTY, T0, _order, _q, boot  # noqa: F401
from tests.crypto.test_paper_wallet_recovery import account_view, hot_quote

HOT_TRADED, HOT_IDLE, HOT_NOW = "LUMIAUSDT", "KAIAUSDT", "RLCUSDT"
RUN_ID = "boot-test"
EVERY = 4                                               # records per segment in these runs

pytestmark = pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is not installed")


class History:
    """One paper process's life, driven tick by tick on a single clock."""

    def __init__(self, runtime, root: Path) -> None:
        self.runtime, self.root, self.clock = runtime, root, T0

    def session(self, symbol: str, *, hot: bool = False):
        if hot:
            hot_market.write_reference(self.root, symbol, exchange_row=exchange_row(symbol),
                                       funding_minutes=480, risk_payload=hot_risk_payload(symbol))
            symbols_module.admit_dynamic(symbol)
        session = self.runtime.session_for(symbol) if symbol != "BTCUSDT" else self.runtime.session
        session.segments.segment_records = EVERY
        if not session.is_started:
            self.clock += 1_000
            session.start(self.clock)
        return session

    def quote(self, symbol: str, mid: str | None = None):
        if symbol in QTY:
            return _q(symbol, self.clock, mid)
        return hot_quote(self.clock, mid or "0.12000")

    def ticks(self, session, symbol: str, n: int) -> None:
        for _ in range(n):
            self.clock += 1_000
            session.observe(self.quote(symbol))

    def order(self, session, symbol: str, side: str, qty: str, intent: str = "OPEN",
              mid: str | None = None) -> None:
        self.clock += 1_000
        _order(session, self.clock, side, qty, intent=intent, q=self.quote(symbol, mid))


def build_world(make, root: Path):
    runtime = make()
    world = History(runtime, root)
    btc = world.session("BTCUSDT")
    world.ticks(btc, "BTCUSDT", 2 * EVERY + 1)

    traded = world.session(HOT_TRADED, hot=True)
    world.ticks(traded, HOT_TRADED, EVERY + 1)
    world.order(traded, HOT_TRADED, "LONG", "600.0")
    world.ticks(traded, HOT_TRADED, 2 * EVERY)
    world.order(traded, HOT_TRADED, "LONG", "600.0", intent="CLOSE", mid="0.12100")
    world.ticks(traded, HOT_TRADED, EVERY + 1)
    asyncio.run(runtime.retire(HOT_TRADED))

    idle = world.session(HOT_IDLE, hot=True)
    world.ticks(idle, HOT_IDLE, 2 * EVERY + 1)
    asyncio.run(runtime.retire(HOT_IDLE))                 # never traded: the session is dropped

    for symbol in ("ETHUSDT", "SOLUSDT"):
        session = world.session(symbol)
        world.ticks(session, symbol, 2 * EVERY + 1)

    world.order(btc, "BTCUSDT", "SHORT", QTY["BTCUSDT"])
    world.ticks(btc, "BTCUSDT", 3 * EVERY + 1)

    now = world.session(HOT_NOW, hot=True)
    world.ticks(now, HOT_NOW, 3 * EVERY + 1)
    (root / "hot_slot.json").write_text(json.dumps({"metrics": {"symbol": HOT_NOW}}))

    c1 = root / f"{RUN_ID}-c1-auto"
    c1.mkdir()
    first = (root / f"{RUN_ID}-ETHUSDT" / "ledger.jsonl").read_text().splitlines()[0]
    (c1 / "ledger.jsonl").write_text(first + "\n")
    (c1 / "input.jsonl").write_text('{"kind":"COMMAND","payload":{"command":"START","ts_ms":1}}\n')
    (c1 / "c1_auto_state.json").write_text("{}\n")
    return runtime


def views(runtime) -> dict:
    cash = runtime.cash
    return {"members": {s: account_view(a) for s, a in sorted(cash.members.items())},
            "wallet": cash.wallet_balance, "available": cash.available_balance,
            "used_margin": cash.used_margin}


def money(runtime) -> dict:
    """The members that hold or ever held money: what retention must leave untouched."""
    out = views(runtime)
    out["members"] = {s: v for s, v in out["members"].items()
                      if v["realized"] or v["fees"] or v["funding"] or v["qty"]}
    return out


def by_run(plans) -> dict[str, R.RunPlan]:
    return {p.facts.name: p for p in plans}


def plan(root: Path):
    return by_run(R.build_plan(root, run_id=RUN_ID))


def archive_and_receipt(root: Path, tmp_path: Path) -> tuple[Path, Path]:
    archive, cold = tmp_path / "archive", tmp_path / "cold"
    R.write_manifests(root, archive, R.build_plan(root, run_id=RUN_ID))
    report = R.pull(host="", remote_root=str(root), remote_archive=str(archive), dest=cold)
    assert report["errors"] == []
    assert all(m["result"] == R.RESULT_OK and m["uploaded"] for m in report["manifests"]), report
    return archive, cold


def name(symbol: str) -> str:
    return RUN_ID if symbol == "BTCUSDT" else f"{RUN_ID}-{symbol}"


@pytest.fixture
def world(boot, tmp_path):  # noqa: F811
    make, root, _feeds = boot
    runtime = build_world(make, root)
    return make, root, runtime


# --------------------------------------------------------------------------- classification


def test_runs_are_classified_from_ledger_position_and_hot_slot(world) -> None:
    _make, root, _rt = world
    plans = plan(root)
    assert plans[name("BTCUSDT")].classification == R.ACTIVE_OPEN_POSITION
    assert plans[name(HOT_TRADED)].classification == R.CLOSED_TRADED
    assert plans[name(HOT_IDLE)].classification == R.CLOSED_NO_TRADE
    assert plans[name("ETHUSDT")].classification == R.CLOSED_NO_TRADE
    assert plans[name("SOLUSDT")].classification == R.CLOSED_NO_TRADE
    assert plans[name(HOT_NOW)].classification == R.ACTIVE_CURRENT_HOT
    assert plans[f"{RUN_ID}-c1-auto"].classification == R.RETIRED_C1


def test_ledger_with_money_is_never_a_candidate(world) -> None:
    _make, root, _rt = world
    for p in plan(root).values():
        for entry in p.files:
            if entry["path"].endswith("/ledger.jsonl") and p.facts.traded:
                assert not entry["candidate"], entry


def test_active_run_keeps_newest_checkpoint_one_fallback_and_the_tail(world) -> None:
    _make, root, _rt = world
    btc = plan(root)[name("BTCUSDT")]
    usable = R._usable_desc(btc.facts)
    assert len(usable) >= 3, "the fixture must leave history to drop"
    assert btc.keep_from == usable[1] and btc.fallback_verified
    for entry in btc.files:
        index = int(Path(entry["path"]).name[:6]) if entry["role"] in (
            "SEGMENT", "SEGMENT_MANIFEST", "CHECKPOINT") else None
        if entry["role"] in ("LEDGER", "ACTIVE_TAPE", "OTHER"):
            assert not entry["candidate"], entry
        elif index is not None:
            assert entry["candidate"] == (index < btc.keep_from), entry
    assert any(e["candidate"] and e["role"] == "SEGMENT" for e in btc.files)   # old BTC tape


def test_open_position_run_is_active_whatever_its_symbol(world) -> None:
    make, root, runtime = world
    eth = runtime.session_for("ETHUSDT")
    world_clock = max(e.get("ts_ms", 0) for e in eth.engine.ledger.events) + 10_000_000
    _order(eth, world_clock, "LONG", QTY["ETHUSDT"], q=_q("ETHUSDT", world_clock))
    assert plan(root)[name("ETHUSDT")].classification == R.ACTIVE_OPEN_POSITION


def test_closed_traded_run_keeps_final_checkpoint_and_last_segment(world) -> None:
    _make, root, _rt = world
    hot = plan(root)[name(HOT_TRADED)]
    last_segment = max(hot.facts.segments)
    assert hot.keep_from == max(R._usable_desc(hot.facts))
    kept = {e["path"] for e in hot.files if not e["candidate"]}
    assert any(p.endswith(f"segments/{last_segment:06d}.input.jsonl") for p in kept)
    assert any(p.endswith(f"checkpoints/{hot.keep_from:06d}.checkpoint.json") for p in kept)
    assert any(e["candidate"] and e["role"] == "SEGMENT" for e in hot.files)   # old HOT tape


@pytest.mark.parametrize("symbol", ["ETHUSDT", "SOLUSDT", HOT_IDLE])
def test_no_trade_runs_may_leave_whole(world, symbol) -> None:
    _make, root, _rt = world
    p = plan(root)[name(symbol)]
    assert p.files and all(e["candidate"] for e in p.files)


def test_c1_auto_directory_may_leave_whole(world) -> None:
    _make, root, _rt = world
    p = plan(root)[f"{RUN_ID}-c1-auto"]
    assert p.files and all(e["candidate"] for e in p.files)


def test_planning_writes_nothing_into_the_run_root(world, tmp_path) -> None:
    _make, root, _rt = world
    before = {p: p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()}
    plans = R.build_plan(root, run_id=RUN_ID)
    R.write_manifests(root, tmp_path / "archive", plans)
    R.decide(root, tmp_path / "archive", plans, paper_running=False)
    assert {p: p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()} == before


# --------------------------------------------------------------------------- fail closed


def test_unknown_directory_is_blocked(world) -> None:
    _make, root, _rt = world
    odd = root / f"{RUN_ID}-not-a-symbol"
    odd.mkdir()
    (odd / "ledger.jsonl").write_text("")
    p = plan(root)[odd.name]
    assert p.classification == R.BLOCKED_RUN and not any(e["candidate"] for e in p.files)


def test_segment_without_manifest_blocks_the_run(world) -> None:
    _make, root, _rt = world
    target = root / name(HOT_TRADED) / "segments"
    next(target.glob("000001.manifest.json")).unlink()
    p = plan(root)[name(HOT_TRADED)]
    assert p.classification == R.BLOCKED_RUN and p.reason == "SEGMENT_WITHOUT_MANIFEST"
    assert not any(e["candidate"] for e in p.files)


def test_unreadable_hot_slot_blocks_every_dynamic_run(world) -> None:
    _make, root, _rt = world
    (root / "hot_slot.json").write_text("{torn")
    plans = plan(root)
    for symbol in (HOT_TRADED, HOT_IDLE, HOT_NOW):
        assert plans[name(symbol)].classification == R.BLOCKED_RUN
    assert plans[name("ETHUSDT")].classification == R.CLOSED_NO_TRADE


def test_a_command_the_ledger_has_not_recorded_blocks_the_run(world) -> None:
    _make, root, _rt = world
    tape = root / name("ETHUSDT") / "input.jsonl"
    with tape.open("a") as handle:
        handle.write(json.dumps({"kind": "COMMAND", "payload": {
            "command": "ORDER", "ts_ms": T0 + 99_000_000, "side": "LONG", "qty": "0.10"}}) + "\n")
    p = plan(root)[name("ETHUSDT")]
    assert p.classification == R.BLOCKED_RUN and p.reason == "PENDING_COMMAND"


# --------------------------------------------------------------------------- receipt gate


def test_without_a_receipt_nothing_is_deleted(world, tmp_path) -> None:
    _make, root, _rt = world
    archive = tmp_path / "archive"
    R.write_manifests(root, archive, R.build_plan(root, run_id=RUN_ID))
    files = sorted(p for p in root.rglob("*") if p.is_file())
    report = R.prune(root, archive, apply=True, paper_running=False, run_id=RUN_ID)
    assert report["deleted"]["files"] == 0
    assert R.PRUNE_ELIGIBLE not in report["totals"]
    assert sorted(p for p in root.rglob("*") if p.is_file()) == files


def test_a_changed_file_after_receipt_is_blocked(world, tmp_path) -> None:
    _make, root, _rt = world
    archive, _cold = archive_and_receipt(root, tmp_path)
    btc = plan(root)[name("BTCUSDT")]
    victim = next(e for e in btc.files if e["candidate"] and e["role"] == "CHECKPOINT")
    path = root / victim["path"]
    path.write_text(path.read_text().replace("{", "{ ", 1))
    report = R.prune(root, archive, apply=True, paper_running=False, run_id=RUN_ID)
    assert path.exists()
    blocked = [r for r in report["runs"] if r["run"] == name("BTCUSDT")][0]["decisions"]
    assert any(k.endswith(":SHA_MISMATCH") for k in blocked), blocked


def test_a_tampered_receipt_is_rejected(world, tmp_path) -> None:
    _make, root, _rt = world
    archive, _cold = archive_and_receipt(root, tmp_path)
    receipt = next((archive / "receipts").glob(f"{name('ETHUSDT')}-*.json"))
    body = json.loads(receipt.read_text())
    body["verified"] = body["verified"][1:]
    receipt.write_text(json.dumps(body))
    report = R.prune(root, archive, apply=True, paper_running=False, run_id=RUN_ID)
    assert receipt.name in report["rejected_receipts"]
    assert (root / name("ETHUSDT")).is_dir()


def test_whole_run_removal_waits_for_the_service_but_segments_do_not(world, tmp_path) -> None:
    _make, root, _rt = world
    archive, _cold = archive_and_receipt(root, tmp_path)
    for running in (True, None):
        report = R.prune(root, archive, apply=False, paper_running=running, run_id=RUN_ID)
        eth = [r for r in report["runs"] if r["run"] == name("ETHUSDT")][0]["decisions"]
        assert not any(k.startswith(R.PRUNE_ELIGIBLE) for k in eth), eth
    report = R.prune(root, archive, apply=True, paper_running=True, run_id=RUN_ID)
    assert (root / name("ETHUSDT")).is_dir()
    assert report["deleted"]["files"] > 0                 # old BTC / HOT tape still went


def test_cold_copy_is_complete_and_readable(world, tmp_path) -> None:
    _make, root, _rt = world
    archive, cold = archive_and_receipt(root, tmp_path)
    manifests, invalid = R.load_manifests(archive)
    assert not invalid
    for manifest in manifests.values():
        for entry in manifest["files"]:
            assert R.verify_file(cold / "run" / entry["path"], entry) is None


# --------------------------------------------------------------------------- restart proof


def test_restart_after_prune_holds_the_same_money_positions_and_ledgers(world, tmp_path) -> None:
    make, root, runtime = world
    before = make()
    expected = money(before)
    ledgers = {n: (root / n / "ledger.jsonl").read_bytes()
               for n in (name("BTCUSDT"), name(HOT_TRADED))}
    trades_before = {n: [t.view()
                         for t in analytics.build_trades(
                             [json.loads(line) for line in ledgers[n].decode().splitlines()])]
                     for n in ledgers}

    archive, _cold = archive_and_receipt(root, tmp_path)
    report = R.prune(root, archive, apply=True, paper_running=False, run_id=RUN_ID)
    assert report["failed"] == [] and report["deleted"]["files"] > 0
    for gone in (name("ETHUSDT"), name("SOLUSDT"), name(HOT_IDLE), f"{RUN_ID}-c1-auto"):
        assert not (root / gone).exists()

    after = make()
    assert money(after) == expected                        # wallet, available, used margin
    assert after.session.engine.account.position.signed_qty == Decimal("-" + QTY["BTCUSDT"])
    assert after.session.engine.account.position.avg_entry == \
        before.session.engine.account.position.avg_entry
    for n, data in ledgers.items():
        assert (root / n / "ledger.jsonl").read_bytes() == data
        events = [json.loads(line) for line in data.decode().splitlines()]
        rebuilt = [t.view()
                   for t in analytics.build_trades(events)]
        assert rebuilt == trades_before[n]                  # episodes, MAE/MFE from the ledger
    assert make().cash.wallet_balance == expected["wallet"]  # and again


def test_restart_falls_back_to_the_previous_checkpoint_after_prune(world, tmp_path) -> None:
    make, root, _rt = world
    expected = money(make())
    archive, _cold = archive_and_receipt(root, tmp_path)
    R.prune(root, archive, apply=True, paper_running=False, run_id=RUN_ID)
    newest = max((root / name("BTCUSDT") / "checkpoints").glob("*.checkpoint.json"))
    newest.write_text(newest.read_text()[:200])           # torn: unusable
    after = make()
    assert money(after) == expected
    assert after.session.recovery.checkpoint_segment < int(newest.name[:6])


def test_a_pruned_no_trade_symbol_starts_a_fresh_run_when_used_again(world, tmp_path) -> None:
    make, root, _rt = world
    archive, _cold = archive_and_receipt(root, tmp_path)
    R.prune(root, archive, apply=True, paper_running=False, run_id=RUN_ID)
    runtime = make()
    assert "ETHUSDT" not in runtime.retired
    eth = runtime.session_for("ETHUSDT")
    assert (root / name("ETHUSDT")).is_dir() and eth.engine.account.position.is_flat
    assert runtime.cash.members["ETHUSDT"] is eth.engine.account


def test_hot_symbol_switch_still_works_after_prune(world, tmp_path) -> None:
    make, root, _rt = world
    archive, _cold = archive_and_receipt(root, tmp_path)
    R.prune(root, archive, apply=True, paper_running=False, run_id=RUN_ID)
    runtime = make()
    symbols_module.admit_dynamic(HOT_TRADED)
    revived = runtime.session_for(HOT_TRADED)              # the traded former HOT comes back
    assert revived.engine.account.position.is_flat
    asyncio.run(runtime.retire(HOT_TRADED))
    assert HOT_TRADED in runtime.retired

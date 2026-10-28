"""Strategy I oneshot start-time guard V3.1 and the I-owned grouped history merge (no market data needed)."""
from __future__ import annotations

import gzip
import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.strategies.i_paper import guard as G
from app.strategies.i_paper import view as V

ROOMY = {"MemAvailable": 500, "SwapTotal": 2048, "SwapFree": 1400}


def quiet_pressure():
    return {r: {k: {a: 0.0 for a in ('avg10', 'avg60', 'avg300')} for k in ('some', 'full')}
            for r in ('memory', 'io')}


def run_at(utc: str, active=(), mem=ROOMY, pressure=None):
    return G.decide(datetime.fromisoformat(utc).replace(tzinfo=timezone.utc), is_active=lambda u: u in active,
                    meminfo=lambda: mem, pressure=lambda: pressure if pressure is not None else quiet_pressure())


def test_v3_policy_defaults_follow_the_run_evidence():
    p = G.Policy()
    assert (p.min_available_mib, p.memory_full, p.io_full) == (350, 1, 15)
    assert not hasattr(p, "io_some") and not hasattr(p, "memory_some")


def test_runs_at_0215_et_in_both_dst_regimes():
    assert run_at("2026-10-21T06:15:00")["decision"] == "RUN"      # EDT: 02:15 ET = 06:15 UTC
    assert run_at("2026-11-04T07:15:00")["decision"] == "RUN"      # EST: 02:15 ET = 07:15 UTC


@pytest.mark.parametrize("unit", G.HEAVY_UNITS + G.E_UNITS)
def test_any_heavy_or_e_unit_active_skips(unit):
    r = run_at("2026-10-21T06:15:00", active=(unit,))
    assert r["decision"] == "SKIP" and unit in r["reasons"][0]


def test_e_window_skips_on_weekdays_only():
    assert run_at("2026-10-21T08:00:00")["reasons"] == ["E_ACTIVE_WINDOW:Wed 04:00 ET"]
    assert run_at("2026-10-24T08:00:00")["decision"] == "RUN"      # Saturday


def test_memory_floor_is_350_and_swap_is_never_credited():
    assert run_at("2026-10-21T06:15:00", mem={"MemAvailable": 100, "SwapTotal": 200000, "SwapFree": 100000})["reasons"] == ["MEM_AVAILABLE_MIB:100.00"]
    assert run_at("2026-10-21T06:15:00", mem={"MemAvailable": 350, "SwapTotal": 2048, "SwapFree": 512})["decision"] == 'RUN'
    assert run_at("2026-10-21T06:15:00", mem={"MemAvailable": 349.9, "SwapTotal": 2048, "SwapFree": 2000})["reasons"] == ["MEM_AVAILABLE_MIB:349.90"]


def test_swap_floor_is_a_separate_condition():
    r = run_at("2026-10-21T06:15:00", mem={"MemAvailable": 900, "SwapTotal": 2048, "SwapFree": 511})
    assert r["reasons"] == ["SWAP_FREE_MIB:511.00"] and r["swap_credit_mib"] == 0 and r["swap_used_mib"] == 1537
    both = run_at("2026-10-21T06:15:00", mem={"MemAvailable": 100, "SwapTotal": 2048, "SwapFree": 100})
    assert both["reasons"] == ["MEM_AVAILABLE_MIB:100.00", "SWAP_FREE_MIB:100.00"]
    # a host without swap is judged on RAM alone
    assert run_at("2026-10-21T06:15:00", mem={"MemAvailable": 350, "SwapTotal": 0, "SwapFree": 0})["decision"] == "RUN"


def test_missing_swap_fields_fail_closed():
    with pytest.raises(G.SourceError):
        run_at("2026-10-21T06:15:00", mem={"MemAvailable": 900, "SwapFree": 900})


@pytest.mark.parametrize('resource,limit', [('memory', 1), ('io', 15)])
def test_each_current_full_psi_blocks_at_boundary(resource, limit):
    p = quiet_pressure()
    p[resource]['full']['avg10'] = limit
    r = run_at('2026-10-21T06:15:00', pressure=p)
    assert r['decision'] == 'SKIP' and r['exit_code'] == 10
    assert r['reasons'] == [f'{resource.upper()}_PSI_full_avg10:{limit:.2f}']
    p[resource]['full']['avg10'] = limit - 0.01
    assert run_at('2026-10-21T06:15:00', pressure=p)['decision'] == 'RUN'


def test_some_avg60_and_avg300_never_block_but_are_logged():
    p = quiet_pressure()
    for r in ('memory', 'io'):
        p[r]['some'] = {'avg10': 60.0, 'avg60': 60.0, 'avg300': 60.0}
        p[r]['full']['avg60'] = 99.0
        p[r]['full']['avg300'] = 99.0
    rec = run_at('2026-10-21T06:15:00', pressure=p)
    assert rec['decision'] == 'RUN' and rec['reasons'] == [] and rec['guard'] == 'V3.1'
    assert rec['observed'] == {'memory_full_avg60': 99.0, 'io_full_avg60': 99.0, 'memory_some_avg10': 60.0}
    p['io']['some']['avg60'] = float('nan')      # observation-only values are still validated
    with pytest.raises(G.SourceError):
        run_at('2026-10-21T06:15:00', pressure=p)


# ------------------------------------------------------------------ 2026-10-28 first timer firing (06:15:04 UTC)
# Pressure exactly as logged by the V3 guard on the server (store guard.jsonl, MemAvailable 411.96, SwapFree 1813.54).
FIRST_FIRING = {"io": {"full": {"avg10": 25.52, "avg60": 23.11, "avg300": 7.36},
                       "some": {"avg10": 27.67, "avg60": 25.24, "avg300": 8.08}},
                "memory": {"full": {"avg10": 6.92, "avg60": 7.5, "avg300": 2.25},
                           "some": {"avg10": 7.16, "avg60": 7.94, "avg300": 2.39}}}
FIRST_FIRING_V3_REASONS = ["MEMORY_PSI_some_avg10:7.16", "MEMORY_PSI_some_avg60:7.94", "MEMORY_PSI_full_avg10:6.92",
                           "MEMORY_PSI_full_avg60:7.50", "IO_PSI_full_avg10:25.52", "IO_PSI_full_avg60:23.11"]
FIRST_FIRING_MEM = {"MemAvailable": 411.96, "SwapTotal": 2048, "SwapFree": 1813.54}


def _v3_rule(p):
    """The retired V3 PSI gates (memory some >= 2, memory full >= 1, IO full >= 15 on avg10 and avg60)."""
    limits = {("memory", "some"): 2, ("memory", "full"): 1, ("io", "full"): 15}
    return ["%s_PSI_%s_%s:%.2f" % (r.upper(), k, a, p[r][k][a]) for r in ("memory", "io") for k in ("some", "full")
            for a in ("avg10", "avg60") if (r, k) in limits and p[r][k][a] >= limits[(r, k)]]


def test_first_firing_still_skips_on_its_current_pressure():
    assert _v3_rule(FIRST_FIRING) == FIRST_FIRING_V3_REASONS        # reproduces what V3 logged in production
    rec = run_at("2026-10-28T06:15:04", mem=FIRST_FIRING_MEM, pressure=FIRST_FIRING)
    # avg10 itself was over both limits at decision time, so V3.1 skips too, only on the current values
    assert rec["decision"] == "SKIP" and rec["reasons"] == ["MEMORY_PSI_full_avg10:6.92", "IO_PSI_full_avg10:25.52"]


def test_first_firing_burst_after_recovery_runs_under_v31():
    # Same burst a minute later (~15:16 KST: memory ~0, IO full ~2): avg10 recovered, avg60/some still carry the trace.
    recovered = {"io": {"full": {"avg10": 2.0, "avg60": 16.0, "avg300": 7.0},
                        "some": {"avg10": 3.0, "avg60": 18.0, "avg300": 8.0}},
                 "memory": {"full": {"avg10": 0.0, "avg60": 4.5, "avg300": 2.0},
                            "some": {"avg10": 2.5, "avg60": 5.0, "avg300": 2.1}}}
    assert _v3_rule(recovered) == ["MEMORY_PSI_some_avg10:2.50", "MEMORY_PSI_some_avg60:5.00",
                                   "MEMORY_PSI_full_avg60:4.50", "IO_PSI_full_avg60:16.00"]   # old V3 = SKIP
    rec = run_at("2026-10-28T06:16:04", mem=FIRST_FIRING_MEM, pressure=recovered)
    assert rec["decision"] == "RUN" and rec["exit_code"] == 0 and rec["observed"]["io_full_avg60"] == 16.0


@pytest.mark.parametrize('bad', [{}, {'memory': {}}, {'memory': {'some': {'avg10': float('nan')}}}])
def test_missing_or_bad_source_fails_closed(bad):
    with pytest.raises(G.SourceError):
        run_at('2026-10-21T06:15:00', pressure=bad)


def test_missing_memavailable_is_error():
    with pytest.raises(G.SourceError):
        run_at('2026-10-21T06:15:00', mem={'SwapFree': 999999})


def test_systemctl_errors_are_not_idle(monkeypatch):
    monkeypatch.setattr(G.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1, stdout=''))
    with pytest.raises(G.SourceError):
        G._is_active('usb-h-forward.service')
    monkeypatch.setattr(G.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stdout='activating\n'))
    assert G._is_active('usb-h-forward.service')


def test_main_exit_codes_and_log(tmp_path, monkeypatch):
    args = ['--store', str(tmp_path), '--min-available-mib', '350']
    monkeypatch.setattr(G, "decide", lambda now, **kw: {"decision": "SKIP", "exit_code": 10, "reasons": ["X"]})
    assert G.main(args) == 10
    assert G.main(args + ['--systemd-condition']) == 75
    monkeypatch.setattr(G, "decide", lambda now, **kw: {"decision": "RUN", "exit_code": 0, "reasons": []})
    assert G.main(args) == 0
    assert [json.loads(l)["decision"] for l in (tmp_path / "guard.jsonl").read_text().splitlines()] == ["SKIP", "SKIP", "RUN"]


@pytest.mark.parametrize('exc,code', [(RuntimeError(), 20), (ValueError(), 21), (G.SourceError(), 22), (OSError(), 22)])
def test_internal_errors_have_distinct_native_and_systemd_exits(tmp_path, monkeypatch, exc, code):
    def fail(*a, **kw):
        raise exc
    monkeypatch.setattr(G, 'decide', fail)
    args = ['--store', str(tmp_path), '--min-available-mib', '350']
    assert G.main(args) == code
    assert G.main(args + ['--systemd-condition']) == 255


def test_missing_store_and_bad_arguments_are_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(G, 'decide', lambda *a, **kw: {'decision': 'RUN', 'exit_code': 0})
    assert G.main(['--store', str(tmp_path / 'absent'), '--min-available-mib', '350']) == 22
    assert G.main(['--store', str(tmp_path), '--min-available-mib', 'nan']) == 21
    assert G.main(['--store', str(tmp_path), '--min-available-mib', '400', '--min-swap-free-mib', '0']) == 21
    assert G.main(['--systemd-condition']) == 255


def test_guard_replay_is_deterministic():
    assert run_at('2026-10-21T06:15:00') == run_at('2026-10-21T06:15:00')


# ------------------------------------------------------------------ V3 end to end on a fake /proc and fake systemctl
def _psi(some10=0.0, full10=0.0, some60=0.0, full60=0.0):
    return ("some avg10=%.2f avg60=%.2f avg300=0.00 total=1\nfull avg10=%.2f avg60=%.2f avg300=0.00 total=1\n"
            % (some10, some60, full10, full60))


class _FixedNow(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 10, 28, 6, 15, tzinfo=timezone.utc)      # 02:15 ET (Wed), outside the E window


def _world(tmp_path, monkeypatch, avail_mib=380, swap_total_mib=2048, swap_free_mib=1810, mem_psi=None, io_psi=None,
           active=(), fail_unit=None, drop_memory_psi=False):
    proc = tmp_path / "proc"
    (proc / "pressure").mkdir(parents=True)
    (proc / "meminfo").write_text("MemAvailable: %d kB\nSwapTotal: %d kB\nSwapFree: %d kB\n"
                                  % (avail_mib * 1024, swap_total_mib * 1024, swap_free_mib * 1024))
    if not drop_memory_psi:
        (proc / "pressure" / "memory").write_text(mem_psi or _psi())
    (proc / "pressure" / "io").write_text(io_psi or _psi())
    store = tmp_path / "store"
    store.mkdir()

    def fake_run(cmd, **kw):
        unit = cmd[2]
        if unit == fail_unit:
            return SimpleNamespace(returncode=1, stdout="")
        return SimpleNamespace(returncode=0, stdout="active\n" if unit in active else "inactive\n")
    monkeypatch.setattr(G, "PROC", proc)
    monkeypatch.setattr(G.subprocess, "run", fake_run)
    monkeypatch.setattr(G, "datetime", _FixedNow)
    return store


def _main(store, capsys, systemd=False):
    args = ["--store", str(store), "--min-available-mib", "350"] + (["--systemd-condition"] if systemd else [])
    code = G.main(args)
    rec = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    log = store / "guard.jsonl"
    return code, rec, (log.read_text().splitlines() if log.exists() else [])


def test_v3_380_mib_and_quiet_psi_runs(tmp_path, monkeypatch, capsys):
    store = _world(tmp_path, monkeypatch)
    assert _main(store, capsys, systemd=True)[0] == 0
    code, rec, log = _main(store, capsys)
    assert (code, rec["decision"], rec["guard"], rec["swap_credit_mib"]) == (0, "RUN", "V3.1", 0) and len(log) == 2


def test_v31_stale_avg60_and_memory_some_do_not_block_current_quiet_host(tmp_path, monkeypatch, capsys):
    store = _world(tmp_path, monkeypatch, avail_mib=380, mem_psi=_psi(some10=30, full10=0, some60=30, full60=20),
                   io_psi=_psi(some10=6, full10=5, some60=50, full60=50))
    code, rec, log = _main(store, capsys, systemd=True)
    assert (code, rec["decision"], rec["reasons"]) == (0, "RUN", []) and len(log) == 1
    assert rec["observed"] == {"memory_full_avg60": 20.0, "io_full_avg60": 50.0, "memory_some_avg10": 30.0}


def test_v31_swap_free_below_floor_skips(tmp_path, monkeypatch, capsys):
    store = _world(tmp_path, monkeypatch, avail_mib=380, swap_free_mib=511)
    code, rec, _ = _main(store, capsys, systemd=True)
    assert code == 75 and rec["reasons"] == ["SWAP_FREE_MIB:511.00"]


def test_v3_349_mib_skips(tmp_path, monkeypatch, capsys):
    store = _world(tmp_path, monkeypatch, avail_mib=349)
    code, rec, _ = _main(store, capsys, systemd=True)
    assert code == 75 and rec["decision"] == "SKIP" and rec["exit_code"] == 10 and rec["reasons"] == ["MEM_AVAILABLE_MIB:349.00"]


def test_v3_memory_psi_full_one_skips(tmp_path, monkeypatch, capsys):
    store = _world(tmp_path, monkeypatch, mem_psi=_psi(some10=0.5, full10=1.0))
    code, rec, _ = _main(store, capsys, systemd=True)
    assert code == 75 and rec["reasons"] == ["MEMORY_PSI_full_avg10:1.00"]


def test_v3_io_psi_full_fifteen_skips_and_below_runs(tmp_path, monkeypatch, capsys):
    store = _world(tmp_path, monkeypatch, io_psi=_psi(some10=20, full10=15.0, some60=3, full60=3))
    code, rec, _ = _main(store, capsys, systemd=True)
    assert code == 75 and rec["reasons"] == ["IO_PSI_full_avg10:15.00"]
    store = _world(tmp_path / "b", monkeypatch, io_psi=_psi(some10=20, full10=14.25, some60=3, full60=3)) if (tmp_path / "b").mkdir() is None else None
    assert _main(store, capsys, systemd=True)[0] == 0      # the 14.25 seen on the clean pre-start host


@pytest.mark.parametrize("unit", ["usb-e-universe.service", "usb-ae-producer.service", "usb-h-forward.service", "usb-grouped-daily.service"])
def test_v3_heavy_unit_active_skips(tmp_path, monkeypatch, capsys, unit):
    store = _world(tmp_path, monkeypatch, active=(unit,))
    code, rec, _ = _main(store, capsys, systemd=True)
    assert code == 75 and rec["decision"] == "SKIP" and unit in rec["reasons"][0]


def test_v3_memory_psi_read_error_is_guard_error_not_skip(tmp_path, monkeypatch, capsys):
    store = _world(tmp_path, monkeypatch, drop_memory_psi=True)
    code, rec, log = _main(store, capsys, systemd=True)
    assert (code, rec["decision"], rec["error"], rec["exit_code"]) == (255, "ERROR", "SOURCE_OR_LOG_UNAVAILABLE", 22) and log == []
    assert _main(store, capsys)[0] == 22


def test_v3_unit_lookup_error_is_guard_error(tmp_path, monkeypatch, capsys):
    store = _world(tmp_path, monkeypatch, fail_unit="usb-h-forward.service")
    code, rec, log = _main(store, capsys, systemd=True)
    assert (code, rec["decision"], rec["exit_code"]) == (255, "ERROR", 22) and log == []


def test_v3_lots_of_swap_does_not_rescue_low_ram(tmp_path, monkeypatch, capsys):
    store = _world(tmp_path, monkeypatch, avail_mib=200, swap_total_mib=8192, swap_free_mib=8000)
    code, rec, _ = _main(store, capsys, systemd=True)
    assert code == 75 and rec["reasons"] == ["MEM_AVAILABLE_MIB:200.00"] and rec["swap_credit_mib"] == 0


def test_v3_swap_in_use_with_enough_ram_and_quiet_psi_runs(tmp_path, monkeypatch, capsys):
    store = _world(tmp_path, monkeypatch, avail_mib=360, swap_total_mib=2048, swap_free_mib=1600)     # 448 MiB swapped
    code, rec, _ = _main(store, capsys, systemd=True)
    assert code == 0 and rec["swap_used_mib"] == 448


def test_v3_skip_and_error_are_distinguishable_in_code_and_log(tmp_path, monkeypatch, capsys):
    store = _world(tmp_path, monkeypatch, avail_mib=300)
    skip = _main(store, capsys, systemd=True)
    store2 = _world(tmp_path / "e", monkeypatch, drop_memory_psi=True) if (tmp_path / "e").mkdir() is None else None
    err = _main(store2, capsys, systemd=True)
    assert skip[0] == 75 and err[0] == 255 and skip[0] not in (0, 1, 2) and err[0] != skip[0]
    assert skip[1]["decision"] == "SKIP" and err[1]["decision"] == "ERROR" and len(skip[2]) == 1 and err[2] == []


def test_v3_unhandled_baseexception_is_not_a_skip(monkeypatch, capsys):
    def boom(*a, **k):
        raise KeyboardInterrupt()
    monkeypatch.setattr(G, "main", boom)
    monkeypatch.setattr("sys.argv", ["guard", "--systemd-condition"])
    assert G._entry() == 255 and json.loads(capsys.readouterr().out)["error"] == "UNHANDLED_KeyboardInterrupt"
    monkeypatch.setattr("sys.argv", ["guard"])
    assert G._entry() == 20


# ------------------------------------------------------------------ I-owned history merged into the view
def _g(d, session, body=b"x"):
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{session}.json.gz").write_bytes(gzip.compress(body, mtime=0))


def _runtime(tmp_path):
    rt = tmp_path / "rt"
    for sub in ("strategy_c/raw/tickers", "strategy_c/raw/splits", "research_universe_u1/reference/tickers"):
        (rt / sub).mkdir(parents=True)
    return rt


def test_history_fills_older_sessions_and_conflicts_refuse(tmp_path):
    rt = _runtime(tmp_path); hist = tmp_path / "hist"
    for s in ("2026-10-12", "2026-10-13"): _g(hist, s)
    for s in ("2026-10-13", "2026-10-14"): _g(rt / "strategy_c/raw/grouped", s)
    vw = V.build_view(V.Stores(rt, evidence=(), history=(hist,)), date(2026, 10, 14), tmp_path / "v")
    assert vw.sessions == ["2026-10-12", "2026-10-13", "2026-10-14"] and vw.manifest["grouped"]["from_history"] == 1
    _g(hist, "2026-10-14", b"different")
    with pytest.raises(V.ViewError, match="differs"):
        V.build_view(V.Stores(rt, evidence=(), history=(hist,)), date(2026, 10, 14), tmp_path / "v2")

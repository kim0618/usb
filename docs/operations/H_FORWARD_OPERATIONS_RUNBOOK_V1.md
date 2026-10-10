# H Forward Shadow Operations Runbook V1

Procedures only. Current numbers and state are in
`docs/backtest/strategy_h_v2/H_V2_CURRENT_AUTHORITATIVE_STATE.md`.

Every server command runs as `ssh traderj 'bash -s'` with a `hostname` guard (`trader-j`), from
`/root/usb` with `PYTHONPATH=backend`. Prefix: `.venv/bin/python -m app.dev.run_h_v2_d7`.

## 1. Daily job

`usb-h-forward.timer`: `Mon..Fri 01:10 America/New_York`, `Persistent=true`. It runs
`usb-h-forward.service` (oneshot, `WorkingDirectory=/root/usb`, flock `/run/usb-h-forward.lock`), which
runs `update`:

1. **Price / outcome.** Collect every missing Massive grouped-daily session into H's own store.
   Mature 1D/5D/21D/63D, TP1/TP2 and Bear monitoring, then `verify`. A stored session is never
   rewritten. A gap stays a gap. `verify` FAIL → exit 2 → unit failed.
2. **SEC submissions refresh.** One primary submissions page per launch-cohort CIK (8 requests,
   `sec_store` limiter and the D1.1 User-Agent) into
   `data/runtime/strategy_h_v2/d7/sec_submissions/<ET date>/`. A same-day rerun makes 0 requests.
3. **Material refresh scan.** Runs only if every CIK is in one root fetched at most 4 days ago and
   today's fetch was complete. The result is validated and then atomically replaces
   `refresh_queue.json`.

**SEC never stops price/outcome.** Steps 2-3 run after step 1 is written and cannot raise into it.

## 2. Degraded behaviour

If steps 2-3 fail (SEC unreachable, partial fetch, stale cache, refused scan), the unit still
succeeds, and:

* stderr / journal shows `REFRESH_DEGRADED: <reasons> (prices and outcomes were updated; refresh queue left unchanged)`;
* `d7/refresh_status.json` holds `refresh_run=REFRESH_DEGRADED`, `refresh_data`, the reasons and the per-CIK fetch;
* `refresh_queue.json` stays byte-identical (`queue_sha256` in the status equals the file).

Verified locally against fixtures from the real 2026-10-04 store and queue
(`test_d7_refresh_independence.py`). With SEC down, price/outcome continues, the queue is preserved,
AEYE stays REFRESH_DUE and REFRESH_DEGRADED is recorded.

Check:

```bash
journalctl -u usb-h-forward.service -n 80 --no-pager | grep -E "REFRESH_DEGRADED|INTEGRITY|refresh_run"
cat data/runtime/strategy_h_v2/d7/refresh_status.json
```

## 3. Manual recovery

| Situation | Command |
|---|---|
| Missed sessions | `update` (collects every gap from the decision session) |
| SEC refresh only | `sec-refresh`, then `refresh-scan --dry-run`, then `refresh-scan` |
| Inspect | `status`, `verify` |
| Hand run while the timer may fire | `flock -w 300 /run/usb-h-forward.lock <prefix> update` |

Never run `launch`. It is refused once launched, and the timer never calls it.

## 4. Health check

```bash
systemctl status usb-h-forward.timer usb-h-forward.service --no-pager
systemctl list-timers usb-h-forward.timer --no-pager
<prefix> verify          # problems = []
<prefix> status          # decision_counts, maturity
```

## 5. AEYE / any REFRESH_DUE

The queue only queues. Clearing a REFRESH_DUE needs a D3→D4→D5→D6 re-evaluation, which costs model
calls and needs operator approval. A scan that would clear it, or drop a known filing, is refused
(`REFRESH_DUE_CLEARED_BY_SCAN`, `KNOWN_FILING_LOST`).

## 6. Sizing limitation

`sizing = NOT_DEFINED`. An APPROVE is recorded as an entry candidate. Position creation is refused
with `SIZING_CONTRACT_REQUIRED`. That is expected and is not an incident.

## 7. Rollback

```bash
systemctl disable --now usb-h-forward.timer
rm /etc/systemd/system/usb-h-forward.{service,timer} && systemctl daemon-reload
tar xzf /root/h_d7_backup_pre_repair_<ts>.tar.gz -C /root/usb   # code + d7 runtime as before
rm -f backend/app/strategies/h_forward/sec_refresh.py           # if the backup predates it
```

The rollback touches no A or E unit, file or database.

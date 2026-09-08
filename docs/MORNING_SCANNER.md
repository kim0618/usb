# Morning Scanner operations

`usb-morning-scan.timer` is the sole recurring owner. The Backend does not run
an embedded Scanner scheduler, and tmux remains manual-only.

## Schedule and target

The timer fires daily at **07:00 Asia/Seoul**. At that instant it is 17:00 ET
during US standard time and 18:00 ET during US daylight time, respectively one
and two hours after a normal 16:00 ET XNYS close. Early closes have more margin.
The job does not infer its target from the Korean date: it converts the actual
instant to `America/New_York`, selects that ET date, and requires the XNYS
calendar session to be closed. An XNYS holiday or weekend returns successful
`SKIPPED` without provider access or a business-row mutation.

`Persistent=true` permits one catch-up after a powered-off VM. The same calendar
and close gate prevents a catch-up from scanning an open or non-trading session.

## Persistence and idempotency

The command requires `RUNTIME_PROFILE=real_market_operator`, an explicit
`PAPER_DATABASE_URL`, Kiwoom market data, the simulation broker, and
`KIWOOM_MODE=market_data_only`. It uses the established production universe
limit of 10. A completed `KIWOOM_REAL` / `quant_v0` run for the target date is
reused. A per-database advisory file lock serializes timer/manual invocations;
the completed ScannerRun in SQLite remains the durable replay truth across
process and VM restarts.

After a new run commits, the command renders the existing
`ResearchPromptService` prompt from that exact ScannerRun. The prompt is already
served dynamically by `/api/v1/research/prompt?scanner_run_id=<id>`, so no fake
GPTAnalysis row or new prompt table is created. Prompt rendering failure makes
the oneshot fail while leaving the committed Scanner snapshot available for a
safe rerun.

## Installation (only after final review, commit, and push)

```bash
sudo install -m 0644 deploy/systemd/usb-morning-scan.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/usb-morning-scan.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now usb-morning-scan.timer
systemctl list-timers usb-morning-scan.timer
```

Do not activate the timer during implementation review. Inspect a run with
`journalctl -u usb-morning-scan.service` and rerun manually with
`systemctl start usb-morning-scan.service`; reruns are idempotent.

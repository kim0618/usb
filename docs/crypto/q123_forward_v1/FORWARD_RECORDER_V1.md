# US-B CRYPTO Q1/Q2/Q3 Forward Recorder V1 (`q123-forward-recorder.v1`)

    status:   READY FOR PRODUCTION REVIEW. Not deployed.
    contract: docs/crypto/q123_forward_v1/CONTRACT_Q123_FORWARD_V1.md (sha256 9d855ad7..., unchanged)
    code:     backend/app/crypto/research/q123_forward_v1/{stream,store,recorder,analyze}.py
    tests:    backend/tests/crypto/test_q123_forward_recorder.py
    deploy:   deploy/crypto_q123_forward/ (unit, env, release list, stage script; not installed)

The forward detector (`detect.py`, `outcome.py`, `run.py`) answers "what does the contract say about
this journal". It reads the whole journal into memory (measured 3.9 GB RSS for 15 hours) and the
journal is kept for 7 days. A sample large enough to review takes weeks. This recorder is the same
detector run forward, a little at a time, writing every fact once into a store that is never pruned.

It is a research recorder, not a signal service: it stores events, the state at the event, validity
and outcomes. It stores no LONG / SHORT / WAIT state and nothing reads it to trade.

## 1. What is added and what is not

Added: an incremental journal reader, an append-only store, a loop, an analyzer.
Not added: any condition, threshold, window, horizon, cost, family or invalid reason. The recorder
calls `detect.detect`, `families.minute_episodes`, `families.joint_family`, `outcome.outcome`
(R0 `second_outcome`) and `stats.summarise` unchanged. Detector files are byte-identical to commit
`b0eb7dd`.

## 2. Architecture

    Context Collector journal (read only)        public /fapi/v1/klines (read only)
        context/*.jsonl[.gz|.open]                     |
        wall_r0/*.jsonl[.gz|.open]                     v
               |                               KlineBook (arrays, runtime/klines.jsonl cache)
               v                                       |
    stream.SessionReader (cursor = checkpoint)          v
        R0 merge restated, HOLDBACK 10 s       window_minute_frame: R1 engine over all bars,
               |                               rows published only around unfinished episodes,
               v                               seeded with stored episodes (60-min overlap rule)
    bounded buffer of seconds  ------------------------+
               |                                       |
               v                                       v
          detect.detect / joint_family per episode / outcome.outcome
               |
               v
    store.Store  (append only, fsync, never pruned)  ->  analyze (read only)

The process takes no lock on the collector root, opens no socket other than the kline endpoint and
imports no order, account, leverage, AUTO, ExitGuard or paper module (section Q).

## 3. Store (`/root/usb_runtime/q123_forward/store`)

| file | one line per | final when |
|---|---|---|
| `meta.json` | store (written once; `coverage_start_ms` filled once) | created |
| `approaches.jsonl` | structural episode x question: `ADMITTED` or the R0 reason | newest sample >= minute close + 65 s |
| `events.jsonl` | admitted episode (the detector record + flat schema) | same moment |
| `outcomes.jsonl` | (event, horizon), `MEASURED` or `MISSING` | a sample at or after the target exists |
| `quality.jsonl` | (collector session, UTC hour): state counts | a later sample exists, or the session ended |
| `conflicts.jsonl` | a recomputed line that differed (never applied) | written |

Every line carries `key`. Appends are one `write` of whole lines on `O_APPEND` plus `fsync`; a crash
leaves at most one torn line at the end of one file, and opening the store cuts exactly that (and
reports it in `status.json`). A complete line that does not parse, or a repeated key, is corruption:
the store refuses to open and the unit stops with exit 5 (no restart loop).

**Retention: none.** No code path deletes or truncates store content beyond a torn tail. The store is
kept until the research ends or the user approves its removal.

### Event schema (`events.jsonl`)

`key`=`event_id` (contract section 4), `question`, `direction` (`UP` / `DOWN`: the price direction
the question's pre-registered lean expects; not a state, not a verdict), `direction_sign` (+1 / -1),
`event_ts` (structural minute open), `anchor_ts` (anchor second), `decision_ts`, `decision_known_ts`,
`collector_session_id`, `structure_level_id`, `structure_price`, `structure_side` (RES / SUP),
`wall_bin`, `wall_side`, `wall_state` (`PRESENT_AT_W` / `GONE_AT_W_TRADED_THROUGH` /
`GONE_AT_W_NOT_TRADED_THROUGH`), `wall_persistence_ms_at_anchor`, `wall_coverage_at_anchor`,
`flow_state`, `coverage_state` {band01_bid, band01_ask, flow60, book}, `generation` {anchor,
decision}, `reference_price` (mid at the decision second), `validity`, `invalid_reason`, `fires`,
`diagnostics` {absorption_candidate, oi, funding, basis, c1 (`NOT_COLLECTED`), utc_session,
`used_in_trigger: false`}, and `detector`: the detector record byte for byte (the analyzer and the
parity checks read this; it carries the contract's question constants `lean` and `candidate_label`).

### Outcome schema (`outcomes.jsonl`)

`key`=`<event_id>:<horizon>`, `horizon_min` (5 / 15 / 30 / 60), `role` (`PRIMARY_GRID` for 5 / 15 / 60,
`DIAGNOSTIC` for 30), `status`, `reachable`, `reachable_bp`, `target_ts`, `observed_ts`,
`reference_price`, `exit_price`, `raw_move_bp` (price up positive), `direction_normalized_bp`,
`gross_bp`, `cost_bp` = 11.25 (`est_fee_bp` 11.0 + `est_slippage_bp` 0.25), `net_bp`, `MFE_bp`,
`MAE_bp`, `high_excursion_bp`, `low_excursion_bp`, `missing_reason`, and `detector`: the
`outcome.outcome` dict as the batch runner builds it.

`missing_reason` restates R0's own checks: `SESSION_BOUNDARY` (the last sample at or before the
target is in another session), `GAP_OVER_5S_AT_TARGET`, `NO_SAMPLE_AFTER_DECISION`, `P0_NOT_FINITE`,
`NO_FINITE_PATH`, `EXIT_NOT_FINITE`. A missing horizon is never estimated.

Outcomes are recorded for every VALID admitted episode, firing or not (the complement), exactly as
`run.compute` does. INVALID episodes have no outcomes.

## 4. Incremental reader and cursor

`stream.SessionReader` reads `context` and `wall_r0` segments from a cursor (per kind: segment number
and uncompressed byte offset after the last record consumed) and restates `reader.seconds`'s merge
(wall rows to `pending` in envelope `seq` order; a sample applies pending rows with `v0_seq <
v0_derived_seq`; resting rows set, terminal rows popped; `wallrule.select`).

* **Live files.** A torn last line is never consumed. Context is read before wall rows; a sample is
  consumed only when it is at least 10 s older than the newest context sample read and the wall
  stream has been read past its `seq`. The collector flushes every kind once a second, so a wall
  row below a consumed sample's `seq` is always already on disk.
* **Rotation and compression.** A segment that is renamed `.open -> .jsonl -> .jsonl.gz` while being
  read is re-listed; offsets are in uncompressed bytes, which compression preserves.
* **Why the cursor is a checkpoint.** The resting wall set is cumulative from the session start. A
  checkpoint (`runtime/checkpoints/ckpt-<sample_ms>.json`, sha256-sealed) stores the offsets, the
  resting set and the pending rows. Restart restores the newest valid checkpoint that is not later
  than the earliest unfinished fact; without one the session is read from its first segment.
* **Rebuildable.** Deleting or corrupting `runtime/` costs a replay from the session starts; the
  store's keys make the replay append nothing that exists. If a session's first segment has been
  pruned by the collector's 7-day retention and no checkpoint exists, the session is reported
  `UNRECOVERABLE_START` and skipped, never read with a guessed resting set.

### Finding (not fixed here: the collector is out of scope)

`reader.seconds` rebuilds `walls_r0` from the session's wall rows since its start. The collector's
retention deletes sealed `wall_r0` hours older than 7 days **including those of the running
session**. After a collector session runs longer than 7 days, any reader that starts from the
journal alone (the batch detector included) would rebuild `walls_r0` without the walls that opened
before the cut. The recorder avoids this by carrying the resting set in its checkpoints, which
requires it to be running (or checkpointed) before the session is 7 days old. The batch runner
should only be used on journals that still contain their session starts.

## 5. Bounded structure window

R1 is a single pass from `SERIES_START`. Publishing every minute since `PUBLISH_FROM` costs about
3.3 KB per row (138 MB for 30 days), so the recorder runs the engine over every bar but publishes
rows only from `row_from` (the earliest unfinished episode minus 2 h, at most one day per cycle).
The engine's publish range is an output filter (rows identical, measured 1,642 / 1,642).

R0's overlap control (`minute_episodes`: one episode per (level, side) per 60 minutes) depends on the
previous episode. The window is therefore seeded with one row per (level, side) whose last episode,
already in the store, opened less than 60 minutes before `row_from`. Measured on real klines over 34
windows: rows identical 34 / 34, episodes identical 34 / 34; without seeds 34 / 34 windows differ.

## 6. Session boundaries

Sessions are never joined. The anchor and decision seconds come from `joint_family` (decision in the
same session or `NO_DECISION_SAMPLE`); an approach whose minute close falls between sessions is
`NO_SAMPLE_AT_MINUTE`; an outcome whose target falls after its session's last sample is `MISSING /
SESSION_BOUNDARY`, final as soon as any later sample exists.

## 7. Operations

* `record` loops: poll klines, read a bounded batch of seconds, finalise, trim, checkpoint every
  10 minutes of sample time, write `runtime/status.json`.
* States: `RUNNING`; `WAITING_FOR_DATA` (newest sample older than 120 s: the collector is stopped or
  behind); `DEGRADED` (store conflict, unrecoverable session, or klines stale over 5 minutes).
* SIGTERM: finish the cycle, write a checkpoint, exit 0.
* Backpressure: when the second buffer reaches 8 hours (klines unavailable), reading pauses; the
  journal keeps the data for 7 days.

## 8. Analyzer and gates

`analyze` reads the store only. Per question and horizon: the detector's `stats.summarise` on the
stored detector records (contract section 8: firing n, win rate, mean / median / p25 / p75 net, median
MFE and MAE, worst decile, top 1% share, R0 circular shift p and bootstrap CI vs the complement),
plus the firing events' own mean bootstrap CI and descriptive concentration (top 10% share of the
positive sum, largest UTC day share). Holm over 4 questions x {5, 15, 60}, with step-down adjusted p.
30 minutes is reported and never enters the grid. No horizon is chosen after results.

Stages (firing events with a 60-minute outcome): < 20 `INSUFFICIENT`, 20-49 `EARLY_SIGNAL_ONLY`,
50-99 `ELIGIBLE_FOR_SIGNAL_CONTRACT_REVIEW`, >= 100 `STRONGER_EVIDENCE_AVAILABLE`. A stage is a count.

`signal_review_inputs` lists, per grid cell, the R0 section 14 minimum (lean sign, Holm adjusted
p <= 0.05, own mean net > 0, after cost) and the concentration figures (no frozen threshold: human
review). `automatic_promotion` is always `PROHIBITED`.

## 9. First 24 hours

`deploy/crypto_q123_forward/first_24h_summary.sh <work_dir>` refuses to run before 2026-10-11
21:47:23 KST (first production sample + 24 h). After that it copies the journal read only, fetches
public klines, replays the recorder into a fresh local store and prints, per question, structure
approaches / decision-capable / valid / fired / outcomes completed per horizon and every exclusion
reason, and the quality ledger (collector, book, liquidity, flow states; coverage; generation
changes). It prints events per day and no outcome statistic.

## 10. Verification (2026-10-11, local)

| check | result |
|---|---|
| stream vs `reader.seconds` (2 h production copy, batch 137) | 4,561 / 4,561 samples identical (10 s holdback tail pending) |
| recorder vs batch detector, 15 h production copy | events 48 / 48 byte-identical, MEASURED 148 / 148, MISSING 2 / 2, exclusion census identical |
| two clean replays | 4 store files byte-identical |
| 46-min parallel recording, R0 original path (V0 research journal) vs Q123 path (context journal replayed from the same V0 inputs) | seconds 2,758 / 2,758 (ok, mid, flow60, bands, near walls, bin keys); F1-F4 rows 10 / 10 identical incl. 1 F3 fire, exclusions identical; T2 410 episodes and G1-G6 identical; recorder 10 / 10 |
| windowed R1 + seeded episodes vs full series, real klines | rows 34 / 34 windows, episodes 34 / 34 (unseeded control: 34 / 34 differ) |
| crash matrix, real data | random kill -9 x 8, SIGTERM, 7 write-point faults (incl. torn appends), runtime deleted, checkpoints corrupted, duplicate replay, late crash restored from checkpoint: all stores identical to the clean store, completed lines kept byte for byte |
| tests | `test_q123_forward_recorder.py` 37 passed; with detector, foundations and collector reader/store tests 226 passed |

## 11. Resources (local measurements)

| | value |
|---|---|
| steady RSS (live tail, klines from cache) | 87-92 MB |
| catch-up peak RSS (15 h replay) | 117 MB |
| CPU, live tail | ~1.1-1.6 % of one core (R1 pass over all bars once a minute; grows linearly with bars, est. ~3 % at day 90) |
| catch-up speed | ~1,600 samples / s CPU-bound (15 h in 35 s) |
| journal read rate, live | the collector's write rate: 1 context sample / s plus wall_r0 rows, read once |
| disk, store | ~6.8 KB per admitted event incl. 4 outcomes and its approach row; quality ~13 KB / day |
| disk, runtime | kline cache ~6.4 MB (+~0.2 MB / day), checkpoints ~220 KB, status.json ~1 KB rewritten per cycle |

Storage of the store (no retention):

| admitted events / day | 30 d | 60 d | 90 d |
|---|---|---|---|
| 10 | 2.3 MB | 4.7 MB | 7.0 MB |
| 50 | 10.2 MB | 20.3 MB | 30.5 MB |
| 100 | 20.0 MB | 39.9 MB | 59.9 MB |

The raw context journal is ~46-54 MB per day (7-day window ~370 MB); 90 days of the store at 100
events per day is about one day of raw journal. Measured rate so far: 48 admitted records in 14.6 h
(~79 / day across 4 questions).

## 12. Isolation

Import closure: 55 modules (`deploy/crypto_q123_forward/RELEASE_FILES.txt`), none under
`app.crypto.paper`, `app.crypto.live`, `app.crypto.binance`, `app.services`, terminal, c1, order,
account, leverage, auto or exit guard (test-enforced, and by subprocess `sys.modules`). The only
network call site is `frames._page`, a GET on the public `/fapi/v1/klines`. The unit mounts the
collector journal read only (`ProtectSystem=strict`, `ReadOnlyPaths=`), writes only under
`/root/usb_runtime/q123_forward`, takes no collector lock, and has no systemd dependency on any
other usb unit.

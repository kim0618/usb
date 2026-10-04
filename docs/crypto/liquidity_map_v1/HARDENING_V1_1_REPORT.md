# Liquidity Map V1.1 hardening — report

Date: 2026-10-04 (KST). Preview version `liquidity-map.v1-1-preview.1`.
Wall selection rule `lm-wall.v2`, frozen in `WALL_RULE_V2.md`
(sha256 `deaa9db8f0e148b0deb6297f4423a7b5e999de723cefde5bcbb49634aa6a9d87`).
Data contract `btc-ms.v0.1` **unchanged**, sha256 still `9eed3862…d52f`.

**Status: isolated preview only. Not deployed, not committed, not linked from navigation.**

---

## 0. What changed, and what did not

Every file this release touches is **untracked** in git — the two packages, their tests, their
docs and the three frontend files have never been committed, so no tracked file in the repository
was modified. Nothing outside `app/crypto/market_structure_v0` and `app/crypto/liquidity_map`
imports either package (verified by grep over `backend/app`), so the trading service cannot be
reached from here even in principle.

| Area | Change |
|---|---|
| `market_structure_v0/collector.py` | resnapshot policy; compact state checkpoint; voluntary-refresh handling |
| `market_structure_v0/store.py` | `write_state()` — atomic, never fatal; state counters in `storage_stats` |
| `market_structure_v0/walls.py` | `state_payloads()` / `Candidate.state_row()` — the live active set, compact |
| `liquidity_map/wallrule.py` | **new** — the frozen `lm-wall.v2` rule and its hash check |
| `liquidity_map/checkpoint.py` | **new** — reads the state file and decides whether to trust it |
| `liquidity_map/wallstate.py` | checkpoint-first; the V1 reconstruction becomes the fallback |
| `liquidity_map/journal.py` | `records_after_seq()` — a tail bounded by `seq`, not by a budget |
| `liquidity_map/view.py`, `api.py` | coverage block, resnapshot block, V2 walls, read-cost accounting |
| frontend | `CoveragePanel`, `ResnapshotPanel`, V2 wall rows, three counts, `≥` markers |

One deliberate deviation from V1's "zero edits to the frozen collector package": **the collector
now writes the state file.** It holds the writer lock, so it is the only process entitled to say
what the current state is, and its dictionary *is* the authority for "resting now" rather than a
reconstruction of it. The alternative — a sidecar that rebuilds the same set from the journal —
would have kept the boundary intact and kept the ambiguity. The frozen contract's file list,
constants and journal schema are unchanged and `test_ms_v0_isolation.py` still passes unmodified
except for the two file-list assertions this release had to extend.

---

## 1. Wall V2 contract

Frozen in `WALL_RULE_V2.md` **before** the resulting wall sets were looked at. Applied by the
**viewer** to the V0 candidate superset; the collector keeps recording the loose rule, because a
dataset's superset can be narrowed later and a subset cannot be widened without re-collecting.

| | Rule | Value | Why |
|---|---|---|---|
| R1 | absolute notional | `>= 250,000 USDT` | the primary fix: a wall is first of all big, in money |
| R2 | local relative size | `multiple >= 5` | V0's 3x is below the median candidate, so it selects nothing |
| R3 | distance from mid | `>= 1.0 bp` | the touch and its neighbourhood are where the slicing lives |
| R4 | minimum persistence | `observed span >= 10,000 ms` | one sample is not resting; low because observation cannot cross a session |
| R5 | price bin | `5.0 USDT = 50 ticks`, floor-anchored | adjacent qualifying levels are one structure |

Order is part of the rule: **R1–R4 filter members, then R5 groups.** Binning first and summing
into the thresholds would let nine ordinary levels add up to a wall that no level in the bin is;
that ordering is rejected in the document, before anybody can prefer the number it produces.

The bin is represented by its **largest-notional member**. `bin_candidate_notional_usdt` is a
lower bound over qualifying candidates only and is labelled as such — it is never the bin's
liquidity. Bin edges are absolute price, not bps of mid, so a wall cannot change identity without
changing.

No threshold sweep was run. The thresholds were chosen from the distribution in §2 and frozen.

**Not tunable from the UI.** The operator's display filter (default 500,000 USDT) is a separate,
later narrowing and is allowed to move precisely because the rule is not. V1's adjustable
`min_multiple` was removed: two multiple floors, one frozen and one adjustable, would leave a
screen showing fewer walls unable to say which of them removed something.

---

## 2. Before / after, on the live book

Measured on the collector's own state checkpoint, session age 96 s, mid 84,834.15,
observed interval 84,696.4 – 84,947.3.

### Before — every V0 candidate the collector was holding (n = 277)

| | min | p25 | med | p75 | p90 | max |
|---|---|---|---|---|---|---|
| notional USDT | 4,156 | 72,407 | **168,413** | 252,208 | 424,256 | 3,253,761 |
| local multiple | 3.06 | 4.74 | **7.45** | 13.70 | 28.01 | **446.93** |
| distance bps | 0.01 | 3.18 | 6.62 | 10.59 | 13.08 | 16.17 |
| observed span s | 0.0 | 7.0 | 24.0 | 95.0 | 95.0 | 95.0 |

* **3 candidates sat at or next to the touch** (within 0.5 USDT), with a multiple up to **159x**.
* **21 candidates sat inside 1 bp of mid**, median notional **60,829 USDT** — a third of the
  overall median. This is the defect: the top of the book is finely sliced, the local mean
  collapses, and a small level clears "3x its neighbours" by a huge factor.

### After — `lm-wall.v2`

| side | V0 candidates | passed R1–R4 | after R5 binning | duplicates folded away | drawn at 500k |
|---|---|---|---|---|---|
| ASK | 124 | 24 | **13** | 11 | 5 |
| BID | 153 | 23 | **16** | 7 | 3 |
| total | 277 | 47 | **29** | 18 | 8 |

**29 walls out of 277 candidates — 10.5% of the superset.** The nearest ASK wall became
84,848.8 at **1.73 bp**, 397,686 USDT, 10.4x, observed 22 s — a real block, not the touch level.

What each rule removes on its own, from the full superset:

| rule | keeps | removes |
|---|---|---|
| R1 notional ≥ 250k | 71 / 277 | 206 |
| R2 multiple ≥ 5 | 193 / 277 | 84 |
| R3 distance ≥ 1 bp | 256 / 277 | 21 |
| R4 span ≥ 10 s | 167 / 277 | 110 |

R1 carries the fix, as designed. **R4's 110 is inflated by the sample**: the session was 96 s old,
so most candidates were simply young. On a mature session R4 removes far fewer, and
`session_age_ms` is published next to the figure so an operator can see when the floor is not yet
meaningful.

**Known cost of R3, stated rather than discovered later:** a genuine wall resting within 1 bp of
mid is excluded. The count excluded by R3 is published on every response, so "nothing near the
touch" can never be confused with "nothing is being looked at near the touch".

---

## 3. Active wall state — the checkpoint

The collector writes `<root>/state/collector_state.json` once per sample: latest derived metrics,
the live active candidate dictionary with **current** sizes, freshness, coverage, the resnapshot
policy and counters, session id + `seq`, and the last 12 telemetry events. Written to a
per-process temporary file and `os.replace`d, so a reader sees the previous complete state or the
next one and never a torn one. Not fsynced — it is rebuildable from the journal, so paying a sync
per second to make a cache durable buys nothing. A write failure is counted in `storage_stats` and
otherwise ignored: a cache must not be able to stop the collection it accelerates.

The API reads that file, then a **tail** of wall rows with `seq` greater than the checkpoint's.
The tail's stopping rule is `seq`, not a byte budget: `seq` increases strictly within a session,
so the first row at or below the checkpoint's `seq` proves everything before it is older. In
practice the tail is empty — journal writes are buffered for up to a second while the state file
is replaced immediately, so the file is normally *ahead* of the stream. It is read anyway.

`is_authority: false` is in the payload and the reader **refuses** a file that says otherwise, so
a future writer cannot quietly promote a cache to the replay authority.

Four refusals, each falling back to the V1 reconstruction (or, when stale, to showing the last
published state and saying so):

| refusal | meaning |
|---|---|
| `STATE_FILE_FROM_ANOTHER_SESSION` | a previous collector's file; observation never crosses a session |
| `STATE_FILE_STALE` | nobody is updating it — the collector stopped |
| `STATE_FILE_SHAPE_UNKNOWN` | a version this reader does not understand |
| `STATE_FILE_CLAIMS_AUTHORITY` | `is_authority` is not false |

The active list is bounded at 2,000, ordered by **current notional descending**, with `truncated`
published. Truncation that could hide the largest wall would be worse than no bound at all. At 277
candidates it has never bitten.

**Compactness.** The journal's wall payload carries 22 fields including the trajectory summary and
two standing disclaimers; the state row carries 15 — identity, current size and its evidence,
observation span, coverage, generation. That cut the file from **153,390 B to 92,463 B (−40%)** at
the same candidate count, about 351 B per candidate. The remaining precision (`multiple` is a
full-precision decimal such as `34.24169741697416974169741697`) was left alone deliberately: the
state file's figures must be comparable to the journal's, and a quantization that can move a value
across a frozen threshold is exactly the kind of silent change this project avoids. It costs about
6% of the file.

---

## 4. Snapshot latency and bytes

Measured from outside the process with `curl`, consecutive polls of
`/api/liquidity-map/snapshot`.

**Before — V1.1 reader on a journal with no checkpoint** (55-minute session, 85 MB wall stream;
this is the V1 code path, now the fallback):

| poll | latency | wall stream read |
|---|---|---|
| 1 (cold process) | **690.7 ms** | **50.7 MiB** |
| 2 | 14.5 ms | 16.5 KiB |
| 3–5 | 12.6 – 17.4 ms | 0 – 40.8 KiB |

**After — the same reader on the state checkpoint:**

| poll | latency | total read | journal walked |
|---|---|---|---|
| 1 (cold process) | **21.5 ms** | **92.6 KiB** | no |
| 2–6 | 6.4 – 14.8 ms | 91.3 – 93.0 KiB | no |

The steady state was already cheap in V1. What changed is the **cold** poll — every new preview
process, every restart, every page opened against a collector that has been up all day — and that
is the number that grew with session length. It is now flat. A preview process started fresh
against a 599 s session holding an 18 MB wall stream:

| poll | latency | total read | journal walked |
|---|---|---|---|
| 1 (cold process) | 21.3 ms | 94.4 KiB | no |
| 2–4 | 14.7 – 15.5 ms | 92.5 – 94.0 KiB | no |

Read cost against session age: 92.6 KiB at 96 s, 92.5 KiB at 468 s, 94.4 KiB at 599 s. The
variation is the candidate count moving (271–277), not the session getting longer. The response
body is 31–33 KiB throughout.

The remaining ~0.8 KiB of tail is the one wall row the walk must look at to prove it is already
behind the checkpoint. `read_cost` publishes every component plus `journal_walked`, so a poll that
*did* fall back to a stream walk says so on the screen.

---

## 5. Observed coverage on screen

The panel states the reach of the data **before** anything is read off it:

* **Observed radius ±0.126 %** (the symmetric reach), with the asymmetric interval printed next
  to it: `-0.174 % ~ 0.126 %`, `84,705.2 ~ 84,959.5`.
* **±0.1 % COMPLETE** — canonical values.
* **±0.25 %, ±0.5 %, ±1 % PARTIAL** — every figure printed as `≥ 41,532,197`. The marker is
  attached to the value rather than placed beside it as a separate word, so a number copied or
  read out of context stays a lower bound.
* The three PARTIAL bands print **identical** bounds, and the screen says why: the observed
  interval ends before ±0.25 %, so the wider bands sum the same levels. Without that line the
  repetition reads as a stuck value. The note appears only when the bounds really are identical.
* Unobserved region is `-`, never `0`, and the screen says so in words.

The chart axis remains the observed interval rather than a round ±1 %, so empty space on the
chart cannot be read as empty book.

---

## 6. Resnapshot policy

**No fixed-interval polling.** Three groups of trigger:

1. **Immediate, unchanged from the frozen contract:** gap, queue overflow, reconnect, disconnect,
   depth stale, invalid or crossed book, rejected snapshot.
2. **New — coverage edge.** While the book is synchronized, the margin
   `min(|known_low − mid|, |known_high − mid|) / mid × 10⁴ − 10` is watched, where 10 bp is the
   ±0.1 % band, the only band a `limit=1000` snapshot can ever complete. Below **1.0 bp**, one
   voluntary snapshot is requested, subject to a **300 s cooldown**.
3. **New — safety refresh.** When no snapshot has been installed for **3,600 s**.

**The rationale is not the API.** A `limit=1000` depth read is weight 20 against a 2,400/minute IP
budget, so one an hour is 0.014 % of it and one a minute would still be under 1 %. The governing
cost is that `apply_snapshot` increments the book `generation`, and a new generation ends **every**
wall candidate as UNKNOWN — a resnapshot destroys the entire accumulated observation history. That
is why the voluntary triggers are rate limited and why the safety interval is an hour.

**The measurement that sets the threshold.** The snapshot's bounds are fixed prices, so the margin
erodes one-for-one with mid. Measured over a 27-minute quiet session: bounds at −15.86 and
+14.44 bp of mid at birth, so the margin over ±0.1 % was only **4.44 bp at birth**, and 2.3 bp of
mid movement ate it to **2.09 bp**. There is not much budget, which is why the trigger acts just
before the promise breaks rather than after, and why the cooldown exists — without it a market
oscillating across the trigger would resnapshot continuously and no candidate would ever
accumulate persistence.

A voluntary refresh is the one request that can be **refused on arrival**: if the REST snapshot is
not newer than the live book, installing it would reset `last_update_id` backwards, the next frame
would fail the first-delta rule, and the collector would take a gap, a resync and the loss of every
wall candidate — in order to replace a healthy book. The existing book is kept and
`refresh_rejected` is recorded. A *failed* voluntary refresh does not set `snapshot_wanted`, so a
timeout cannot push a working book onto the recovery path.

### It fired live

| seq | event | detail |
|---|---|---|
| 11769 | `coverage_edge` | `margin_bps: "0.62"`, `trigger_bps: "1.0"`, generation 1 |
| 11771 | `snapshot_request` | `voluntary: true`, `reason: "coverage_edge"` |
| 11773 | `resync` | generation 2, **130 ms after the trigger** |

Margin restored from 0.62 bp to 4.56 bp; cooldown started; `refreshes_rejected: 0`.
**The ±0.1 % band was COMPLETE in 459 / 459 samples, across the generation change** — the promise
the policy exists to protect was never broken. The cost was visible too: selected walls fell to 0
for about ten seconds while every candidate was young again.

The **safety refresh has not fired live** — no session has yet run an uninterrupted hour. It is
proven by test only.

---

## 7. Verification

**Tests: 368 passed** across `test_ms_v0_*` and `test_liquidity_map_*` (backend), **78 passed**
for the preview component (frontend). New files: `test_liquidity_map_wallrule.py` (33),
`test_liquidity_map_checkpoint.py` (24), `test_ms_v0_resnapshot.py` (20).

| requirement | result |
|---|---|
| API response is O(1) in session length | yes — 92.6 KiB at 96 s, 92.5 KiB at 468 s with 15 MB of wall stream |
| active wall recovery | read from the checkpoint, `verified_by: COLLECTOR_STATE_CHECKPOINT`; the V1 reconstruction remains for sessions without one, with its proof intact |
| restart | new session recovered in 6 s; **0 walls selected for the first 10 s** because R4's floor is not yet met — correct, and visible via `session_age_ms`; 26 walls at 21 s |
| stale | collector stopped → `SESSION_ENDED`, `sample_is_current: false`, candidates 0, read cost **7.1 KiB** — no journal walk on a dead collector; after 3 s the file goes `STATE_FILE_STALE` and the source label changes |
| reconnect | covered by test (`test_a_reconnect_ends_the_candidates_and_the_screen_shows_the_resync`); no live reconnect occurred |
| wall 0 | display filter at 99,999,999 → `walls_shown: 0`, `nearest: null`, while `walls_selected: 34` stays, so the screen says the zoom emptied it, not the market |
| partial coverage | ±0.25/0.5/1 % PARTIAL throughout, identical lower bounds, explained on screen |
| 390 px | `scrollWidth 390 = clientWidth 390`, **0 px horizontal overflow**; no element wider than the viewport outside a scroll container. Same at 1440 px |
| existing trading service | **zero tracked files modified**; nothing outside the two packages imports either; separate process, separate port, no route registered on the terminal app |

`tests/crypto/` as a whole: **30 failed, 1970 passed, 25 skipped, 14 errors**, and **none of the
failures or errors is in a `liquidity_map` or `ms_v0` file**. Those failures are missing
gitignored freeze data (`btc_p2`, `btc_vol_a`, `c1_parity`, `d6_golden`, `expert_execution_e1`);
the full `tests/` run additionally cannot be collected because another session's in-flight edits
to `app/services/entry_management_runtime.py` break 32 imports. The same directory measured
39 failed / 1870 passed before this work started, but another session was editing files
throughout, so that delta is not attributable here — the claim is only that nothing in this
release fails.

### UI

Captured headless at 1440 px and 390 px (`scratchpad/shots/`): full page plus the coverage,
resnapshot, wall-set and ASK-side panels. The phone capture shows the result the release is for —
nearest wall **84,888.0 at 3.94 bp, 1,019,080 USDT, 44.2x, observed 1m 47s**, with the bin badges
(`2레벨`, `4레벨`), the `≥` markers on the three PARTIAL bands, the three counts
(`7 / 규칙 통과 16 / V0 후보 122`) and the rejection breakdown
(`금액 미달 84 · 국소 배수 미달 6 · 관측 시간 부족 4`).

---

## 8. Production readiness

**Ready:** the rule is frozen and hash-checked at runtime; the read cost no longer depends on
session length; a stopped collector is reported as stopped instead of looking live; coverage is
stated before any value is read off it; the resnapshot policy is published with what it costs; the
screen holds no direction, no rating, no order path, and the package cannot reach one.

**Not ready, and why:**

1. **The safety refresh is unproven live.** No session has run an uninterrupted hour. Same status
   as V0's gap→resync path.
2. **Wall V2 has been seen on roughly ten minutes of one quiet book.** The thresholds are frozen
   and justified against a measured distribution, but they have not been seen through a volatile
   session, a funding window or a liquidation cascade — precisely when the touch region behaves
   differently. R4's effect in particular is still confounded with session age.
3. **A resnapshot still costs the whole wall history.** The coverage trigger fired once in eight
   minutes of live running. In a trending market the 300 s cooldown is a floor of one wall-history
   loss per five minutes, and no candidate would show a persistence longer than that. Whether that
   is acceptable is a judgement about what the screen is for, and it is a user decision.
4. **The preview renders inside the dashboard app shell** (sidebar, US-market header), because
   `app-shell.tsx` is being edited by a concurrent session and was not touched. One line
   (`isEquityChrome`) fixes it.
5. **Mark price remains unavailable** — the V0 contract reaches three public endpoints and none
   carries one. Unchanged, and reported with its reason.

**Open user decisions:**

* **A.** Whether to fold the preview into the operating UI, and under which route.
* **B.** The coverage cooldown (300 s) and safety interval (3,600 s): both trade wall persistence
  against band coverage, and the live trigger showed the trade-off is real, not theoretical.
* **C.** Whether R3's 1 bp blind spot at the touch is acceptable, now that the excluded count is
  on screen.
* **D.** Whether to re-measure the Wall V2 distribution on a volatile session before anything is
  built on top of it.

No production deployment was performed and none is recommended until 1 and 2 are closed.

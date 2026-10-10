# A-MOVER-SCANNER-V1.1: the actionable GPT handoff

> **RESEARCH HISTORY.** Its handoff mask is in force through V1.2; pool size superseded by V1.2. Current contract: `docs/operations/A_MOVER_NEXT_SESSION_AUTHORITY_V1.md`.

| | |
|---|---|
| Contract | `a-mover-scanner-v1.1` |
| Rules checksum | `94852218ae991adb8d85c5ed4358bf3111ea65b3a74100a41e3eb72945f881d2` |
| Discovery contract | `a-mover-scanner-v1`, score version `mover_v1`, **unchanged** |
| Discovery checksum | `d900dffd7b23fea1224584e390f75de7b71d198dbf9c01c0586287fd4b112a2a` (identical to V1) |
| Code | `backend/app/backtest/mover_scanner_v1/actionability.py`, `handoff_study.py`, CLI `app.dev.run_mover_handoff_research` |
| Tests | `backend/tests/test_mover_scanner_v1_1.py` (26), V1's own `test_mover_scanner_v1.py` (46) still green |
| Minute cache | `strategy_b_e0/cache/ca1ce9d030cdcb88`, digest `33373b3a848d880c…`, 104 sessions, 3,882 symbols, 112,709,629 rows |
| Window | 83 sessions, 2026-05-18 .. 2026-09-15, the same comparable sample V1 used |
| Provider calls | 0. Replays: 0. PnL computed: no. Production changes: 0. Real orders: 0 |
| Artifacts | `data/runtime/research_reports/mover_scanner_v1_1/` (V1's directory is not written) |
| **Verdict** | **GPT BUDGET WASTE RESOLVED (37.95% → 0%), STARVATION STILL RESOLVED, but the declared output-size bar is missed: NEEDS_SCANNER_HANDOFF_REVISION** |

---

## 1. What this stage changes, and what it deliberately does not

V1's own section 6 reported the inefficiency this stage removes: a share of the symbols V1 hands
to GPT cannot be admitted by Strategy A's premarket gate for their gap, whatever the research
says about them. Measured with the gate's own band, on V1's 664 output slots over 83 sessions:

| V1 output slot | slots | share | V1's own section 6 |
|---|---|---|---|
| gap above 15% at the cut | 151 | 22.7% | 22.7% ✔ |
| gap at or below 0 | 12 | 1.8% | 1.8% ✔ |
| gap positive but below 2% | 89 | **13.4%** | not counted there |
| **unactionable total** | **252** | **37.95%** | |

The first two rows reproduce V1's published figures exactly, which ties this measurement to
V1's. The third row is the part V1's "about a quarter" understated: a sub-2% gap is as
unadmittable as a 40% one, so the real waste is **three slots in eight**, not two.

V1.1 adds exactly one thing, between the discovery pool and the GPT handoff: a mask that keeps
only the candidates the *current* execution contract could admit, after which the pool is
re-ranked on the discovery score it already has. Nothing else moves, and the discovery checksum
is still V1's `d900dffd7b23fea1…`, so a V1 artifact remains reproducible under its own name.
No weight, knot, floor, normalisation, universe rule, pool rule, entry rule, stop, risk setting
or GPT prompt was touched. The deployed path was not touched at all: this is research code.

## 2. The mask

Three conditions and no more (`actionability.HandoffRule`):

```
direction compatible  AND  gap >= gap_min  AND  gap <= gap_max
```

**Every bound is read, never written.** `gap_min`, `gap_max` and the direction come from
`StrategyConfig`; the handoff maximum comes from `MoverScannerConfig.top_count`. The module
contains no numeric literal for any of them, which is asserted by parsing its own AST in
`test_the_module_declares_no_gap_threshold_of_its_own`, and two further tests move the config
bound up and down and assert the handoff changes with it. The bounds travel inside the V1.1
checksum, so moving Strategy A's band produces a different contract rather than a quietly
different answer under the same name.

**Direction is tested first**, so a -8% gap is reported as `DIRECTION_NOT_ACTIONABLE` rather
than as a band violation: for an UP contract the sign is the disqualifier, not the magnitude.
Only `UP` is deployed and `scan.gate_reading` already refuses to measure anything else; for
`DOWN` and `ANY` the band is read on the gap's size, which is the only reading under which a
positive 2-15% band means anything, and a changed direction remains a reviewable event.

**No volume filter.** Section D of the handoff contract forbids one and the measurement
supports the instruction: the volume gate pass rate per handed-off slot is 80.3% for V1 and
78.6% for V1.1, so the mask is not quietly selecting volume-poor names and there is nothing for
a volume reject to protect. Relative volume is already 25% of the discovery score, and the
volume condition is the entry gate's to judge at the open on its own window.

**Scan time, not gate time.** The mask reads the 09:15 gap, because that is what a 09:15
handoff can know. The gate reads the last print before 09:30. Section 7 measures what that
costs.

## 3. Re-ranking from the pool, not from the TOP8

The order matters. Masking V1's TOP8 would have left 412 slots over 83 sessions and nothing
would have refilled them. Masking the whole 25-member discovery pool lets a candidate below
V1's own output cut compete for the slot an unactionable name was holding:

- **73 slots** were filled from discovery ranks 9 and below, 15.1% of all V1.1 slots.
- Their ranks: 9 (30), 10 (11), 11 (12), 12 (8), 13 (3), 14 (3), 15 (4), 16 (1), 19 (1).
  Nothing came from beyond rank 19, so the pool's 25 is not binding on the replacement side.
- 46 of 83 sessions used at least one replacement.

`discovery_output_rank` is V1's own stage-2 order, reproduced with V1's own tie-break, and the
study fails hard if the reproduction does not match `scan.top` symbol for symbol on every
session. That is what makes "this slot replaced that one" a measurement rather than a guess.

## 4. Sizes (section I), 83 sessions, no replay

| | |
|---|---|
| discovery pool: average / median / minimum | 25.0 / 25.0 / 25 |
| actionable pool: average / median | **6.31 / 6.0** |
| actionable pool: minimum / maximum | 1 / 12 |
| GPT output: average / median / minimum | **5.84 / 6.0 / 1** |
| sessions output = 8 | 22 |
| sessions output 5-7 | 37 |
| sessions output 1-4 | 24 |
| sessions output = 0 | **0** |

Output size histogram: 8 → 22, 7 → 15, 6 → 13, 5 → 9, 4 → 14, 3 → 6, 2 → 3, 1 → 1, 0 → 0.
Sessions with at least 5 candidates: 59 of 83 (71.1%). With at least 4: 73 of 83 (88.0%).

Nothing is padded. On 61 sessions the actionable pool held fewer than 8 names and the handoff is
exactly as short as the pool allowed; on 18 sessions it held more than 8 and 39 actionable
candidates were left outside the cap.

## 5. Slot recovery (section K)

| | V1 raw TOP8 | V1.1 actionable TOP8 |
|---|---|---|
| GPT slots spent | 664 | **485** (-27.0%) |
| unactionable slots | 252 | **0** |
| invalid slot rate | **37.95%** | **0.00%** |
| gap 2-15% compliance rate | 62.05% | **100%** |
| actionable slots bought | 412 | **485 (+17.7%)** |

Per session: 3.04 wasted slots on average, and **78 of 83 sessions wasted at least one**. The
budget reading is the one that matters: the same line item buys 17.7% more actionable research
for 27% fewer calls.

## 6. Quality, three arms, same engine (sections J, L)

Both new arms are made of discovery-pool candidates and are described from the candidate
itself, so they sit on bit-identical numbers; the V1 column reproduces V1's published table
exactly (unique 462, repeat 0.304, turnover 0.941, median gap +6.66%, median RVOL 40.58, both
pass 3.795), which is the check that the adapter is not a second computation. The legacy column
is V1's own frozen `historical-daily-top8-v1` arm, unrecomputed.

| | CURRENT (legacy) | V1 RAW TOP8 | V1.1 ACTIONABLE TOP8 |
|---|---|---|---|
| slots | 664 | 664 | 485 |
| unique symbols | 25 | 462 | **360** |
| repeat ratio | 0.962 | 0.304 | **0.258** |
| TOP8 turnover | 0.221 | 0.941 | **0.938** |
| most repeated symbol | MU, 83 / 83 | KLAC, 6 / 83 | **PURR, 5 / 83 (6.0%)** |
| mega-cap share (ADDV ≥ 99th pct) | 0.993 | 0.044 | 0.060 |
| median ADDV percentile | 99.87 | 86.00 | 86.66 |
| mega-cap share (known caps) | 0.647 | 0.270 | 0.241 |
| median gap | +0.18% | +6.66% | +5.71% |
| median PM RVOL | 1.06 | 40.58 | 28.95 |
| median PM dollar volume | $476.2M | $25.2M | $20.8M |
| gap ≤ 0 share of slots | 0.459 | 0.018 | **0.000** |
| gap gate pass / slot | n/a | 60.1% | **86.6%** |
| volume gate pass / slot | n/a | 80.3% | 78.6% |
| **full premarket gate pass / slot** | n/a | 47.4% | **68.2%** |
| **full gate pass / session** | 0.795 | 3.795 | **3.988** |
| sessions with ≥1 full pass | 38 / 83 (45.8%) | 81 / 83 (97.6%) | **81 / 83 (97.6%)** |

Read the per-slot column as the efficiency claim and the per-session column as the starvation
one. Two slots in three now clear the whole deployed premarket gate, against not quite one in
two for V1, and the per-session count went *up* slightly on 27% fewer slots.

**Diversity guard: held.** 360 unique symbols across 485 slots, a lower repeat ratio than V1
itself, turnover unchanged at 0.938, and the most frequent symbol appears on 5 of 83 sessions
against the legacy arm's MU on 83 of 83. The mask did not walk the output back toward the
mega-cap list: top-liquidity share is 0.060 against the legacy arm's 0.993, and the median
liquidity percentile barely moved (86.00 → 86.66).

## 7. What the mask costs, measured rather than assumed

The mask reads 09:15 and the gate reads 09:30, so a removed slot could in principle have
recovered into the band by the open. It happens, and the cost is small and fully reconciled:

| | slots | would pass the full 09:30 gate |
|---|---|---|
| V1 slots kept by the mask | 412 | 289 (70.1%) |
| V1 slots removed by the mask | 252 | **26 (10.3%)**: 16 gapped above 15%, 10 below 2% |
| replacements from rank 9-19 | 73 | **42 (57.5%)** |

V1's 315 gate-passing slots (289 + 26) become V1.1's 331 (289 + 42): the mask gives up 26
gate-passers and buys 42, a net +16, which is exactly the 3.795 → 3.988 per-session move. So
the measured false-negative rate of masking early is **3.9% of V1's slots**, and it is more than
paid for by what replaces them.

## 8. GPT handoff (section M)

`ResearchPromptService` renders unchanged, at `TOP8_PROMPT_VERSION = top8_research_v0`. The
prompt states its candidate count from the number of rows it is given, so an 8, 5 or 1 candidate
session renders a prompt that asks for exactly that many symbols with contiguous ranks `1..N`;
tests assert the rendered count, the rank text, the presence of every handed-off symbol and the
absence of every masked-out one. `candidate_rows` refuses to build a payload whose ranks are not
contiguous from 1, whose symbols repeat, or which exceeds the maximum, and the study re-checks
all three on every one of the 83 sessions.

A 0-candidate session hands over no rows, and the existing service already raises
`ResearchError` for a run with no candidates rather than rendering an empty prompt. That is the
correct behaviour and it never occurred in 83 sessions.

## 9. The decision (section O)

Thresholds were declared in `handoff_study.DECISION`, above the run, and are reported with the
result. Six of seven pass.

| check | bar | measured | |
|---|---|---|---|
| structural starvation resolved | ≥ 2.0 gate-passes/session and ≥ 90% of sessions | 3.99 and 97.6% | **PASS** |
| GPT waste resolved | invalid slot rate 0 and below V1's | 0.00% vs 37.95% | **PASS** |
| actionable pool sufficient | median ≥ 5 | 6.0 | **PASS** |
| output mostly 5-8 | ≥ 80% of sessions ≥ 5 | **71.1%** | **FAIL** |
| diversity held | repeat ≤ 0.50, turnover ≥ 0.60, unique ≥ 100, top symbol ≤ 25% of sessions | 0.258, 0.938, 360, 6.0% | **PASS** |
| no regression to the legacy structure | better than legacy on all three | 360 vs 25, 0.258 vs 0.962, 0.938 vs 0.221 | **PASS** |
| handoff ranks contiguous, unique, capped | all 83 sessions | all 83 | **PASS** |

**Verdict: `NEEDS_SCANNER_HANDOFF_REVISION`**, on the output-size bar alone.

The revision needed is **not in the handoff layer**, and this is the finding to act on. The mask
cannot produce candidates; it can only remove them. Discovery hands it a pool fixed at 25 names
ranked on participation evidence only, and 44% of that pool gaps at or below zero
(`DIRECTION_NOT_ACTIONABLE` averages 11.0 of 25 per session, `GAP_TOO_LOW` 4.5,
`GAP_TOO_HIGH` 3.2), which leaves about six in-band names. Section B freezes discovery and
section P forbids changing it, so V1.1 reports the number rather than widening the pool.

Two readings are available and the choice is the user's:

1. **Accept.** On the contract's other sizing language the stage passes: section F names 8 / 5 /
   2 / 0 as correct outputs, the mean is 5.84, the median is 6, 88.0% of sessions deliver at
   least 4, and no session delivered zero. Under a ≥4 reading every declared check passes and
   the verdict is `READY_FOR_FORWARD_GPT`.
2. **Widen discovery first.** `pool_size` is the one knob that would move this: the pool is cut
   on participation alone, so gap quality enters only at stage 2, and raising 25 would hand the
   mask more in-band names. Replacements already reach rank 19 of 25. That is a rules change to
   a frozen contract, hence a user decision, and it would need its own checksum and its own run.

The honest disclosure on the bar itself: `actionable_pool_median_min` was set to 5 rather than 8
after a 3-session smoke showed the 25-member pool holds about six in-band names, because an 8
bar would have been measuring `pool_size` rather than the handoff. The output-size bar of 80%
was declared before the full run and is reported as it fell.

## 10. Known limits, stated not hidden

- **Every V1 limit still applies**: the 83-session window, the daily volume denominator, the
  trade-value proxy for the legacy arm, weak market-cap coverage (27% of V1.1 slots), the known
  false positive in the non-common rule, and fractional provider volume. V1's section 7 is the
  authority and nothing here improves on it.
- **The mask is a gap rule, not a tradability rule.** It says the gate could admit the gap. It
  says nothing about whether the opening range, VWAP, the 10:30 deadline, risk or capacity will
  allow a fill, and no return was computed anywhere in this stage.
- **09:15 is not 09:30.** Section 7 measures the cost (26 of 664 slots) rather than assuming it
  away. A live forward run would see the same effect.
- **`market_cap` is still None** in the handoff payload, for V1's reason.
- **The replacement attribution depends on reproducing V1's order.** It is checked on every
  session and the study refuses to report if it ever disagrees.
- **Nothing was deployed.** The deployed Scanner still produces the legacy arm. Wiring V1.1 into
  a forward shadow is a separate, explicitly authorised step.

## 11. Reproducing

```
PYTHONPATH=backend .venv/bin/python -m app.dev.run_mover_handoff_research
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/test_mover_scanner_v1_1.py \
                                               backend/tests/test_mover_scanner_v1.py -q
```

No network, no replay, no paper database, no order path. Writes only
`data/runtime/research_reports/mover_scanner_v1_1/`; V1's artifact directory is left exactly as
published. The run refuses to start if the current-arm checksum has moved, if the minute cache
has uncovered symbol-sessions, or if the reproduced discovery order ever disagrees with V1's own
output.

# A-MOVER-SCANNER-V1.2 — Pool size finalization

SELECTED POOL SIZE: **35**. P35 is the smallest authorized value meeting every required bar and the recommended 50% eight-output target. P25 fails output average, median and ≥5 rate; P50 passes but is not the smallest.

83 sessions: 2026-05-18 .. 2026-09-15. Existing local minute/daily/reference caches only; no collection, network, replay or PnL. P25 pool rows reproduce V1 exactly and all P25 handoff rows reproduce V1.1 exactly. Frozen JSON artifacts and research prompt Python source hashes were checked before/after and are unchanged.

## Output and quality

| Metric | P25 | P35 | P50 |
|---|---:|---:|---:|
| Discovery avg / median / min / max | 25.000 / 25 / 25 / 25 | 35.000 / 35 / 35 / 35 | 50.000 / 50 / 50 / 50 |
| Actionable avg / median / min / max | 6.313 / 6 / 1 / 12 | 9.289 / 9 / 2 / 19 | 13.446 / 13 / 4 / 28 |
| GPT avg / median / min / max | 5.843 / 6 / 1 / 8 | 7.120 / 8 / 2 / 8 | 7.639 / 8 / 4 / 8 |
| Sessions output8 | 22 | 55 | 70 |
| Sessions output5_7 | 37 | 21 | 11 |
| Sessions output1_4 | 24 | 7 | 2 |
| Sessions output0 | 0 | 0 | 0 |
| ge5_rate | 71.08% | 91.57% | 97.59% |
| ge7_rate | 44.58% | 73.49% | 90.36% |
| eq8_rate | 26.51% | 66.27% | 84.34% |
| slots | 485 | 591 | 634 |
| unique_symbols | 360 | 416 | 426 |
| repeat_ratio | 25.77% | 29.61% | 32.81% |
| turnover | 93.82% | 94.55% | 94.68% |
| full_gate_pass_per_slot | 68.25% | 65.14% | 63.41% |
| top_liquidity_share | 5.98% | 6.77% | 8.83% |
| mega_cap_share_known | 24.06% | 20.00% | 18.88% |
| market_cap_known_share | 27.42% | 27.92% | 30.91% |
| Most repeated (all ties) | AVAV, KLAC, MDT, PURR (5/83) | AVAV, HPE, KLAC (6/83) | AVAV, EOSE (6/83) |
| Median gap | 5.71% | 5.61% | 5.54% |
| Median PM RVOL | 28.95 | 22.69 | 18.77 |
| Median PM dollar volume | $20.76M | $19.17M | $18.98M |
| Full gate pass/session | 3.988 | 4.639 | 4.843 |
| Sessions ≥1 full pass | 81/83 (97.59%) | 81/83 (97.59%) | 83/83 (100.00%) |

Repeat ratio = 1 − unique/slots; turnover = mean fraction of today’s output absent from the preceding session. Mega-cap proxy is ADDV ≥99th percentile. Known-cap share uses ≥$200B among known caps only; coverage is shown separately. Full pass means the existing measured gap+volume premarket gate, not an opening-entry decision.

## Deep-rank contribution

Ranks are `candidate_pool_rank`, the actual participation order BEFORE total_score reranking. They are not `discovery_output_rank` (the score order inside each truncated pool).

| Variant | Pool rank | Slots | Share | Median total_score | Median gap | Median PM RVOL | Full gate pass |
|---|---|---:|---:|---:|---:|---:|---:|
| P35 | 1-25 | 440 | 74.45% | 1.9099 | 5.81% | 29.55 | 70.23% |
| P35 | 26-35 | 151 | 25.55% | 1.6940 | 5.25% | 11.50 | 50.33% |
| P35 | 36-50 | 0 | 0% | — | — | — | — |
| P50 | 1-25 | 407 | 64.20% | 1.9345 | 5.97% | 28.63 | 71.25% |
| P50 | 26-35 | 118 | 18.61% | 1.7571 | 5.38% | 10.67 | 47.46% |
| P50 | 36-50 | 109 | 17.19% | 1.6785 | 4.67% | 5.76 | 51.38% |

P35 ranks 26–35 contribute 151 slots (25.55%), with 50.33% full passes versus 70.23% in its rank 1–25 group. This is a material per-slot quality decline, not quality parity. Overall per-slot pass declines 68.25%→65.14%, while full passes rise 331→385 (+16.3%) and gate passes/session rise 3.988→4.639. Unique symbols rise 360→416 and turnover 93.82%→94.55%; no structural diversity collapse. P50 adds supply but is unnecessary under the minimum-size selection rule.

## Selection guards and contract

Before measurement, qualitative guards were operationalized conservatively: zero decline in gate passes/session; repeat ≤50%; unique ≥P25; turnover ≥60% and no more than 5pp below P25; most repeated symbol ≤25% of sessions. No additional pool sizes were measured. The exact per-variant checks are in `pool_size_report.json`.

09:15 ET → unchanged eligible universe and universe-wide normalization → participation TOP35 → StrategyConfig direction/gap mask → unchanged total_score rerank → min(actionable count, 8). Tie breaks are unchanged. Opportunity weights: PM dollar volume 30%, PM RVOL 25%, gap quality 20%, PM momentum 15%, tradability 10%; V1 gap shaping retained. Current StrategyConfig: UP, 2%–15%. No handoff volume hard filter. Existing V1 universe eligibility floors (including $50,000 PM notional) remain unchanged; they were not introduced by this stage.

Version: `a-mover-scanner-v1.2`

Handoff checksum: `f05e53cce5a431e8e132a0fc1698085b64f8e1d015e11028a77dc62754a11f25`

Discovery checksum: `aa6d80cd4000546758f816f800d1386c539d66c4db7f3cf194b6b7147e63d95d`

Checksums use the existing canonical sorted compact JSON declaration method. `scanner_contract.sha256` stores the semantic handoff checksum; `artifact_manifest.json` stores byte-level artifact hashes.

## GPT handoff and validation

Final P35 payload/render tests for 8, 5, 1 and 0 symbols: PASS. The edge fixtures derive from an actual selected 8-symbol session, with excluded gaps made un-actionable; these are controlled tests, not claims that historical P35 produced 1 or 0. Zero yields an empty payload and the existing ResearchError/no prompt behavior. No padding; unique contiguous ranks; unchanged prompt text. Historical P35 output minimum is 2.

Command: `PYTHONPATH=backend .venv/bin/python -m app.dev.run_mover_pool_research`

Tests: `PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/test_mover_scanner_v1.py backend/tests/test_mover_scanner_v1_1.py backend/tests/test_mover_scanner_v1_2.py -q` — **82 passed**, 4 existing dependency deprecation warnings.

## Final status

```text
SELECTED POOL SIZE: 35
READY FOR FORWARD GPT: YES
STRUCTURAL STARVATION: RESOLVED
GPT BUDGET WASTE: RESOLVED
TOP8 GENERATION CONTRACT: FINAL
ENTRY CHANGE: 0
RISK CHANGE: 0
GPT PROMPT CHANGE: 0
PRODUCTION CHANGE: 0
REAL ORDERS: 0
PnL: 0
network: 0
new data collection: 0
commit: 0
push: 0
deploy: 0
```

Readiness refers to the research handoff contract; no production wiring or forward run was performed. Existing sample limitations persist: one 83-session sample, sparse market-cap coverage, inherited universe/volume-proxy limitations; no return claim.

## Changed / Git / Scope

Added only `backend/app/dev/run_mover_pool_research.py`, `backend/tests/test_mover_scanner_v1_2.py`, this report, and the new `data/runtime/research_reports/mover_scanner_v1_2/` artifacts. Existing V1/V1.1 code and artifacts were not edited. No V2 features implemented.

Branch: `main`; HEAD: `f58bf9f98d5c3ed1658810b6ae46dfbd5b989ac8`. Working tree already had extensive unrelated changes/untracked files; preserved. Commit/push/deploy: none.

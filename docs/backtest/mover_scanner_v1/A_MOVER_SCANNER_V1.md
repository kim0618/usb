# A-MOVER-SCANNER-V1: Strategy A premarket mover scanner

| | |
|---|---|
| Contract | `a-mover-scanner-v1`, score version `mover_v1` |
| Rules checksum | `d900dffd7b23fea1224584e390f75de7b71d198dbf9c01c0586287fd4b112a2a` |
| Code | `backend/app/backtest/mover_scanner_v1/`, CLI `app.dev.run_mover_scanner_research` |
| Tests | `backend/tests/test_mover_scanner_v1.py` (46) |
| Minute cache | `strategy_b_e0/cache/ca1ce9d030cdcb88`, digest `33373b3a848d880c…`, 104 sessions, 3,882 symbols, 112,709,629 rows |
| Current arm | frozen `historical-daily-top8-v1` `year_b_top8.json`, checksum `671f42438950690a…` (verified at run time) |
| Comparison window | 83 sessions, 2026-05-18 .. 2026-09-15 |
| Provider calls | 0. Replays: 0. PnL computed: no. Production changes: 0 |
| Artifacts | `data/runtime/research_reports/mover_scanner_v1/` |
| **Verdict** | **STRUCTURAL STARVATION RESOLVED, ready for GPT forward shadow** |

> Successor: the GPT handoff inefficiency this document reports in section 6 is measured and removed by [A_MOVER_SCANNER_V1_1.md](A_MOVER_SCANNER_V1_1.md). Nothing below has changed; V1's rules checksum and artifacts are still the ones named here.

---

## 1. The problem this replaces

The deployed morning Scanner acquires Kiwoom's trade-value TOP10 (`usa20540`) and ranks it with
`QuantScanner`. Trade value is a property of the company; Strategy A's entry gate is a property
of the morning. In the US market the trade-value board is structurally the same dozen mega caps
every day, and a mega cap rarely gaps 2% or trades 5% of its daily volume before the open. The
result in paper was 79 of 82 evaluations `PREMARKET_REJECTED`, mostly `GAP_TOO_LOW`.

This is a scanner defect, not a strategy defect. Nothing in the entry, stop, risk, exit,
pyramid, trailing, overnight or GPT prompt path is touched by this work.

## 2. The scanner

Scan time is `scan_cut_minute`, default **555 = 09:15 ET**, and the cut is point-in-time by
construction: a bar is read only if its start minute is strictly below the cut, so 09:15 sees
04:00 through 09:14 inclusive.

**Universe.** The dated common-stock reference cache in force on the session; ETFs, funds and
the provider's test tickers are already absent because those files hold security type CS only.
The provider nevertheless labels preferred shares and baby bonds CS, so the declared
`NON_COMMON_BY_CIK_PREFIX_NO_FIGI` rule removes a ticker when three signals agree: no
`composite_figi`, it extends a shorter ticker of the same CIK, and that shorter ticker is in the
universe. On 2026-07-01 that removes 83 names and keeps GOOG, GOOGL, BRK.A, BRK.B, AGNC, FULT,
ACN and ACGL. Average scanned universe: **3,813 symbols**.

There is **no market-capitalisation rule and no volatility rule**. The only other removals are
data quality and executability: no tape coverage, a split executing that morning, fewer than 3
premarket prints, premarket notional below $50,000, no previous regular close, price below
$1.00, no volume baseline. The two premarket floors are E1's audited values, reused rather than
invented.

**Features** at the cut: gap, premarket volume, premarket notional, premarket relative volume,
momentum, range, last price, 20-session ADV and ADDV, tradability.

Relative volume divides the scan-window volume by the **median** of the same window over the
previous 20 covered sessions, the normalisation the audited V2 premarket history uses, with a
1,000-share floor so a symbol that normally does not trade premarket stays finite. This is the
heart of the fix: on 2026-09-15 AAPL's premarket baseline is 599,671 shares and its premarket
volume was 451,302, so AAPL reads **0.75**, which is correct: it is not a mover.

**Score** (section G weights): premarket notional 30%, relative volume 25%, gap quality 20%,
momentum 15%, tradability 10%. Every component is normalised across the session's own
candidates before weighting. log1p for the heavy-tailed non-negative ones, then winsorise at
5/95, then standardise, which is `app.scanner.normalization`'s own recipe. Measured mean
contribution share over 83 sessions: **0.292 / 0.244 / 0.194 / 0.172 / 0.100**, against declared
weights 0.30 / 0.25 / 0.20 / 0.15 / 0.10. No component dominates.

**Gap quality** is shaped before it is ranked, through declared knots:
`-2% → 0.00, 0% → 0.05, 2% → 0.35, 5% → 0.75, 10% → 1.00, 15% → 1.00, 20% → 0.55, 30% → 0.20,
50% → 0.05`. Standardisation is monotone, so the shape survives it: a 20% gap scores 0.55
against a 4% gap's 0.617. A sub-2% gap is scored low, never rejected.

**Two stages.** The pool ranks on participation evidence only, notional and relative volume,
never relative volume alone, and keeps at most 25. The output re-ranks that pool on the full
opportunity score and keeps at most 8. `candidate_pool_rank` therefore carries information the
output rank does not: the mean pool rank of an output row is 10.2, and the maximum is 25.

`top_count` is a maximum, not a quota. Over 83 sessions the eligible count averaged 1,031 and
never fell below 786, so every session produced a full pool and a full 8; a thinner session
would produce fewer rows and the existing prompt would render for exactly that many.

## 3. GPT handoff

`handoff.py` fills the rows the deployed prompt already reads (`rank`, `symbol`, `score` and
`score_components_json`), so `ResearchPromptService` renders with **no prompt change**. The
prompt states its candidate count from the number of rows it is given, which is how a short
output works by itself; a test renders a 3-symbol prompt and a full one and asserts the
unchanged `TOP8_PROMPT_VERSION`. `market_cap` is sent as None, because the store holds dated
caps for ~212 symbols only and inventing one would be worse than the null the prompt already
renders.

## 4. Comparison (83 sessions, same sessions, same feature engine)

Both arms are described by one premarket feature engine and judged by the deployed gate's own
arithmetic. The gate is measured on **its own** window (04:00-09:30) with thresholds read from
`StrategyConfig` (gap 2-15%, ratio ≥ 5%, direction UP); it scores nothing and admits nothing.

| | CURRENT SCANNER | NEW MOVER SCANNER |
|---|---|---|
| unique symbols | 25 | **462** |
| repeat ratio | 0.962 | **0.304** |
| mega-cap share (ADDV ≥ 99th pct) | 0.993 | **0.044** |
| median ADDV percentile | 99.87 | **86.00** |
| mega-cap share (known caps) | 0.647 | 0.270 (cap known for 26% of slots) |
| median gap | +0.18% | **+6.66%** |
| median PM RVOL | 1.06 | **40.58** |
| median PM dollar volume | $476.2M | $25.2M |
| gap pass / session | 1.63 | **4.81** |
| volume pass / session | 2.20 | **6.42** |
| both pass / session | 0.80 | **3.80** |
| sessions with ≥1 both-pass | 38 / 83 (45.8%) | **81 / 83 (97.6%)** |
| gap ≤ 0 share of slots | 0.459 | **0.018** |
| TOP8 turnover | 0.221 | 0.941 |

The two scanners pick almost disjoint sets: mean per-session overlap **0.205 symbols**, and
**70 of 83 sessions share no symbol at all**. The current arm's fixed list is literal: MU in
83 of 83 sessions, NVDA 74, SNDK 69, AAPL 59, while the new arm's most frequent symbol appears
6 times in 83.

Pool and output: pool average and median 25.0, minimum 25; output average 8.0; sessions below 8: 0.

## 5. Structural starvation

**RESOLVED.** Gate-pass candidates per session 0.80 → 3.80 (4.8x), and sessions with at least
one gate-pass candidate 45.8% → 97.6%.

Read this as a scanner-to-gate funnel measurement and nothing more. The premarket gate is
necessary, not sufficient: GPT approval, the opening range, VWAP, the entry deadline, risk and
capacity all follow it, which is why the paper arm filled 3 trades in 82 evaluations while its
historical proxy shows 0.80 gate-passes per session. No return was computed here.

## 6. What the measurement says to fix next

Rank by gap bucket confirms the declared shape is doing its job. Mean output rank is best in
5-10% (3.86) and 10-15% (3.77), worst at gap ≤ 0 (7.50), and 20%+ takes 8 of 83 rank-1 slots
against 11.4% of all slots, so it is slightly under-represented at the top rather than pushed
there.

The remaining inefficiency is the share of slots the gap gate cannot admit by construction:
**22.7% of output slots gap above 15%** and 1.8% gap at or below 0, so about a quarter of the
GPT budget goes to symbols the UP 2-15% gate will refuse. Section H forbids a hard reject below
2% and section P forbids rejecting a name for gapping large, so V1 leaves them in and reports
the number. The single highest-value V1.1 knob is the gap-quality decay above 15% (and whether
gap ≤ 0 deserves a quality of 0.00 rather than a place in the cross-section at all). That is a
rules change and therefore a user decision.

## 7. Known limits, stated not hidden

- **Window.** Premarket relative volume needs a 20-session premarket baseline and the broad
  minute tape starts 2026-04-20, so the first 20 sessions can be a baseline but not a scan. The
  frozen current arm ends 2026-09-15. The intersection, 83 sessions, is the whole comparison.
- **Volume denominator.** Paper's gate divides by Kiwoom's 20-session average daily volume; the
  study divides by the grouped daily store's, the only full-market daily source on disk. The
  store's own note records grouped and per-symbol daily disagreeing on volume. The gap numerator
  is unaffected by the choice.
- **Trade-value proxy.** The current arm ranks the completed session's own dollar volume, while
  Kiwoom's board is a live intraday read at 07:00 KST. Same meaning, not necessarily the same
  order; this is the frozen artifact's own documented difference.
- **Mega cap by market capitalisation is weakly covered.** Dated caps exist for ~212 symbols,
  and they are the large ones, so the new arm's 0.270 is computed on a biased 26% of its slots.
  The liquidity percentile is the sound reading and it is the one that moved: 99.87 → 86.00,
  top-1% share 0.993 → 0.044.
- **The non-common rule has a known false positive**: a genuine share class whose own row lacks
  a FIGI is removed too, LILAK being the measured instance. Its cost was measured rather than
  assumed: over 20 sessions and 500 pool slots, **none of the 113 removed tickers would have
  reached the pool**, because they do not clear the $50,000 premarket notional floor.
- **Dollar volume uses bar close × bar volume**, matching the deployed scanner and the current
  arm's proxy. The provider's `vw` is not used as a price, since it sits outside `[l, h]` on 38%
  of premarket rows.
- **Fractional share volume.** Provider volume is fractional on most rows and is not directly
  comparable to Kiwoom's integer volume. Both arms are measured on the same source, so the
  comparison is internally consistent.

## 8. Reproducing

```
PYTHONPATH=backend .venv/bin/python -m app.dev.run_mover_scanner_research
```

No network, no replay, no paper database, no order path. Writes only
`data/runtime/research_reports/mover_scanner_v1/`. The run refuses to start if the current-arm
checksum has moved or if the minute cache has uncovered symbol-sessions.

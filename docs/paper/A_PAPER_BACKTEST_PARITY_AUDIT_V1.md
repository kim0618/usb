# A Paper <-> Backtest Strategy Parity Audit V1

audit_id `7f8ba5ddac42358b4519`. Code/config audit only: replay 0, network 0, real orders 0, production DB reads 0. Nothing was changed.

Verdict: **PARTIAL/FAIL -> FAIL** (TYPE A 23, TYPE B 4, TYPE C 15).

## 1. What O0/O1 mean

The O0/O1 results are Strategy A's shared core logic (the production QuantScanner, StrategyV0Engine, RiskEngine, SimBroker, stop/trailing protection and closing review) evaluated in a research environment: Research Universe V2 ranked TOP8, Massive unadjusted minute data with a REGULAR-minute daily volume basis, every TOP8 symbol approved by assumption, overnight declared UNKNOWN_CLOSE (O0) or ASSUMED_HIGH (O1), trailing fixed at NORMAL, 10,000 USD. They are not a historical clone of the running paper, whose universe, approvals, overnight/trailing authority and volume basis come from Kiwoom, a human and GPT records. Status: RESEARCH_ENVIRONMENT_RESULT.

## 2. Parameter matrix

| Field | Paper | O0 | O1 | Class | Notes | Source |
|---|---|---|---|---|---|---|
| Gap min | 0.02 | 0.03 | 0.03 | INTENTIONAL_STRATEGY_DELTA | research override strategy.premarket_gap_min_pct | `app/strategy/config.py:21; run.json parameter_set` |
| Gap max | 0.15 | 0.15 | 0.15 | SAME |  | `app/strategy/config.py:22` |
| Premarket volume ratio threshold (value) | 0.05 | 0.05 | 0.05 | SAME | same number; its inputs differ (rows PM volume numerator/denominator) | `app/strategy/config.py:23` |
| Premarket volume ratio formula | build_premarket_context | same function | same | SAME | PM minute sum / mean of last 20 daily bars within 45 days | `app/services/entry_management_runtime.py:299-335; session_replay.py:360` |
| PM volume denominator source | Kiwoom daily acc_trde_qty (upd_stkpc_tp=1) | Massive REGULAR minute volume summed per session | same as O0 | ENVIRONMENT_PARITY_MISMATCH | different basis: Massive minute tape is 3.6-20.2% below the daily bar (MASSIVE_DAILY_BASIS.md 2.1); Kiwoom daily = Massive daily MATCHING_BASIS | `app/integrations/kiwoom/mapping.py:63; app/backtest/replay/provider.py:300-318` |
| PM volume numerator source | Kiwoom PREMARKET minute bars 04:00..as_of | Massive PREMARKET minute bars 04:00..as_of | same as O0 | ENVIRONMENT_PARITY_MISMATCH | provider coverage differs; the 50-56% Kiwoom/consolidated figure is not in any repo artifact (memory of a 2026-09-15 server-side audit only) | `entry_management_runtime.py:313-314; session_replay.py:328` |
| Opening range minutes | 15 | 15 | 15 | SAME |  | `app/strategy/config.py:24; app/strategy/indicators.py:17-24` |
| Entry deadline | 10:30 ET | 10:30 ET | 10:30 ET | SAME |  | `app/strategy/config.py:25` |
| VWAP rule | price > session VWAP | same | same | SAME | session_vwap shared | `app/strategy/config.py:34; indicators.py:27-33` |
| OR breakout rule | required | required | required | SAME |  | `app/strategy/config.py:35` |
| Entry reference tolerance | 0 | 0 | 0 | SAME |  | `app/strategy/config.py:33; engine.py:191-210` |
| Initial stop rule | engine (OR-based) | same | same | SAME | evaluate_entry shared | `app/strategy/engine.py:142-189` |
| Trailing ATR normal | 1.5 | 1.5 | 1.5 | SAME |  | `app/strategy/config.py:39` |
| Trailing multiplier authority | GPT trailing_profile: TIGHT 1.0 / NORMAL 1.5 / WIDE 2.0 / UNKNOWN 1.5 | UNKNOWN_DEFAULT -> 1.5 | UNKNOWN_DEFAULT -> 1.5 | ENVIRONMENT_PARITY_MISMATCH | paper stop width depends on the GPT record; backtest fixes NORMAL | `app/strategy/engine.py:298-306; entry_management_runtime.py:263-264,436` |
| Trailing activation | 1R | 1R | 1R | SAME |  | `app/strategy/config.py:41; run.json derived.trailing` |
| ATR period | 14 | 14 | 14 | SAME |  | `app/strategy/config.py:37` |
| Pyramiding (strategy max_pyramid_adds) | 1 | 0 | 0 | INTENTIONAL_STRATEGY_DELTA | research override | `app/strategy/config.py:42` |
| Pyramiding (risk max_pyramid_adds) | 1 | 0 | 0 | INTENTIONAL_STRATEGY_DELTA | research override | `app/risk/config.py:20` |
| Risk per trade | 0.5% | 0.5% | 0.5% | SAME |  | `app/risk/config.py:10` |
| Daily risk | 3R | 3R | 3R | SAME |  | `app/risk/config.py:12` |
| Base capacity / pyramid reserve | 0.80 / 0.20 | 0.80 / 0.20 | 0.80 / 0.20 | SAME |  | `app/risk/config.py:13-14` |
| Symbol exposure | 0.60 | 0.60 | 0.60 | SAME |  | `app/risk/config.py:15` |
| Max open positions / new symbols per day | 3 / 3 | 3 / 3 | 3 / 3 | SAME |  | `app/risk/config.py:17-19` |
| Commission / spread / slippage | 10 / 10 / 5 bps | same | same | SAME | ExecutionConfig() default in both | `app/execution/config.py:10-12; main.py:49; baseline/runner.py:362` |
| Fill delay / partial fill | 1 bar / off | same | same | SAME | SimBroker shared | `app/execution/config.py:14-15; app/broker/sim.py:46-91` |
| Entry price ceiling | fill <= execution_price(reference, BUY) | same | same | SAME |  | `app/broker/sim.py:69-72` |
| Overnight authority | GPT overnight_suitability record (HIGH/MEDIUM carry) | UNKNOWN_CLOSE (never carries) | ASSUMED_HIGH (declared for every symbol) | ENVIRONMENT_PARITY_MISMATCH | O0 replaces the unavailable historical GPT record with UNKNOWN; O1 declares HIGH. The O0-vs-O1 axis itself is an intentional research axis (row below) | `entry_management_runtime.py:265,437; engine.py:274; backtest/authority/resolver.py` |
| Overnight mode O0 vs O1 (research axis) | n/a | UNKNOWN_CLOSE | ASSUMED_HIGH | INTENTIONAL_STRATEGY_DELTA | the one intended difference between O0 and O1 | `baseline/runner.py with_research_overnight` |
| Max hold | 2 sessions (variant C) | same | same | SAME | holding_deadline shared | `app/services/position_protection.py:41,331` |
| Closing review | close - 10 min, strength >= 0.70, stop distance <= 2R, stress 20%/5%, 1 overnight slot | same | same | SAME | closing_review_decision shared | `app/services/book_snapshots.py:169-197; engine.py:255-281` |
| Starting capital | 7,428.92 USD (PAPER_INITIAL_CASH) | 10,000 USD (ASSUMED_RESEARCH) | 10,000 USD | ENVIRONMENT_PARITY_MISMATCH | all sizing is % of equity with fractional quantities: USD scales by 0.743, returns unaffected | `app/dev/bootstrap_paper_account.py:13; baseline/contract.py STARTING_CASH` |
| Candidate universe | Kiwoom trade-value ranking usa20540, first 10 rows at scan time | Research Universe V2: 29 declared (NYSE/Nasdaq CS, ADV rank, cap >= 10B, sector cap 5) | same as O0 | ENVIRONMENT_PARITY_MISMATCH |  | `app/market/universe.py:28-54; deploy/systemd/usb-morning-scan.service; research/universe_selection.py:48` |
| Scanner ranking / TOP8 | QuantScanner | same QuantScanner | same | SAME | same filters, weights, sort key | `app/scanner/scanner.py:53,219,250; research/scanner.py:141-149` |
| Scanner market-cap input | Kiwoom usa20550 at scan time | static dated reference (ASSUMED_STATIC) | same as O0 | ENVIRONMENT_PARITY_MISMATCH |  | `app/market/universe.py; research/metadata.py:66` |
| Scanner daily cut | 07:00 KST (~18:00 ET), partial acc_trde_qty visible at 16:00 | Massive daily visible at 20:00 ET | same as O0 | ENVIRONMENT_PARITY_MISMATCH | stated in research/contract.py:10-13 | `kiwoom/mapping.py:63,68; research/daily.py:138-150` |
| Entry approval | human APPROVE rows only (GPT import must cover TOP8) | ALL_RESEARCH_SYMBOLS (ASSUMED) | same as O0 | ENVIRONMENT_PARITY_MISMATCH |  | `entry_management_runtime.py:229-266; services/research.py:45-50` |
| Candidate evaluation order | GPT rank | scanner rank (ASSUMED) | same as O0 | ENVIRONMENT_PARITY_MISMATCH | matters when capacity binds | `entry_management_runtime.py:260,860; portfolio/candidates.py:165-168` |
| Minute data provider | Kiwoom REAL (market_data_only) | Massive, unadjusted | same as O0 | ENVIRONMENT_PARITY_MISMATCH | provider difference is unavoidable; see canonical inputs | `app/market/factory.py; integrations/massive/client.py:238` |
| Daily price adjustment | Kiwoom daily upd_stkpc_tp=1 (possibly adjusted, unconfirmed); minute =0 | Massive adjusted=false | same as O0 | ENVIRONMENT_PARITY_MISMATCH | affects the denominator on split sessions | `app/integrations/kiwoom/client.py:211,221` |
| Bar timestamp / availability | bar open, available_at = +1 min; only bars Kiwoom returned (FUTURE_DATA filter) | bar open, available_at = +1 min assumed | same | ENVIRONMENT_PARITY_MISMATCH | real Kiwoom latency UNKNOWN; cntr_tm-as-bar-start assumed | `kiwoom/mapping.py:74-94; replay/provider.py:64-66` |
| Tick loop / owner order | 3 concurrent async minute loops (entry, position, EOD) | fixed entry -> position -> EOD per tick, open+0.5s | same | ENVIRONMENT_PARITY_MISMATCH |  | `app/main.py:59-64; portfolio/replay.py:18-34; replay/clock.py` |
| Orchestration wrappers | position_lifecycle / end_of_day_lifecycle | position_replay / portfolio account | same | ENVIRONMENT_PARITY_MISMATCH | DUPLICATED: evaluate, pyramid add, Day2 activation, holding limit, daily state, session equity | `position_lifecycle.py:201-311; end_of_day_lifecycle.py:140-179,363; position_replay.py:591-770; portfolio/account.py:87-173` |

## 3. Approval / authority

- 1_entry_approval_differs: YES: paper evaluates only human-APPROVE rows of a GPT-covered TOP8; backtest approves every TOP8 symbol (ASSUMED)
- 2_only_overnight_differs: NO: entry approval, evaluation order, overnight suitability and trailing profile all differ
- 3_scanner_candidate_acceptance_differs: YES: the scanner function is shared, but the universe it ranks, its market-cap input and its daily cut differ
- 4_historical_data_for_human_gpt: NO for the research range: no GPT/human decision is replayed (AuthorityResolver ASSUMED); RECORDED coverage not re-measured in this audit
- 5_replaced_by_assumed: candidate universe (RESEARCH_UNIVERSE), approval (ALL_RESEARCH_SYMBOLS), rank (scanner order), overnight (UNKNOWN_CLOSE / ASSUMED_HIGH), trailing (UNKNOWN_DEFAULT = NORMAL)

## 4. Premarket volume semantics: SCALING_MISMATCH

- docs/MASSIVE_DAILY_BASIS.md 2.1: Massive daily = Kiwoom acc_trde_qty (MATCHING_BASIS); Massive minute tape 3.6-20.2% below the daily bar
- run.json derived.premarket_volume_v1: backtest denominator = REGULAR minute bars summed; runner warning: parity with Kiwoom acc_trde_qty UNKNOWN
- not in repo: the Kiwoom minute ~= 50-56% of consolidated figure (only a memory note of a 2026-09-15 server-side audit; data on the server /tmp)
- backtest denominator is smaller than the paper denominator for the same day, so the same 0.05 passes more easily in the backtest; numerator scale difference UNKNOWN in repo

## 5. Engine reuse

| Step | Classification | Detail |
|---|---|---|
| scanner ranking | SHARED_IMPLEMENTATION | QuantScanner.scan |
| premarket context | SHARED_IMPLEMENTATION | build_premarket_context (inputs differ) |
| opening range | SHARED_IMPLEMENTATION | indicators.opening_range |
| VWAP | SHARED_IMPLEMENTATION | indicators.session_vwap |
| entry decision | SHARED_IMPLEMENTATION | StrategyV0Engine.evaluate_entry / entry_reference_price |
| risk sizing | SHARED_IMPLEMENTATION | RiskEngine.evaluate_base_entry; entry_capacity |
| daily risk state / session equity | DUPLICATED_IMPLEMENTATION | DailyRiskRepository vs PortfolioReplayAccount |
| execution pricing / fill | SHARED_IMPLEMENTATION | SimBroker.submit_order, ExecutionConfig |
| stop / trailing protection | SHARED_IMPLEMENTATION | position_protection.replay_protection |
| position evaluation wrapper, pyramid add | DUPLICATED_IMPLEMENTATION | position_lifecycle vs position_replay (both call execute_add) |
| closing review | SHARED_IMPLEMENTATION | book_snapshots.closing_review_decision -> engine.closing_review |
| Day2 activation, holding limit | DUPLICATED_IMPLEMENTATION | end_of_day_lifecycle vs position_replay (shared holding_deadline) |
| session classification of minute bars | DUPLICATED_IMPLEMENTATION | kiwoom mapping (exchange_calendars) vs massive classify |
| scanner universe acquisition | DIFFERENT_IMPLEMENTATION | KiwoomUniverseSource vs declared research universe |
| approval / authority | DIFFERENT_IMPLEMENTATION | DB APPROVE + GPT record vs AuthorityResolver ASSUMED |

## 6. Canonical inputs

| Input | Status | Parity |
|---|---|---|
| gap_pct | SHARED formula; previous close = prior session final regular minute; reference = last visible PREMARKET close | PARTIAL (same formula, different tape) |
| premarket_volume_ratio | SHARED formula; denominator basis differs (Kiwoom daily vs Massive REGULAR minute sum); numerator provider coverage differs | MISMATCH |
| OR high/low | SHARED function over provider minute bars | PARTIAL (tape) |
| VWAP | SHARED function, regular bars, typical price x volume | PARTIAL (volume coverage) |
| signal price | SHARED engine | PARTIAL (tape) |
| ATR | SHARED (session-only) | PARTIAL (tape) |
| reference price | SHARED entry_reference_price, tolerance 0 | MATCH |
| execution price | SHARED SimBroker + ExecutionConfig | MATCH (fill bar open from different tape) |

Canonical layer: no separate provider-independent metrics layer; both providers emit MinuteBar/DailyBar to the same engine; divergence at raw mapping (kiwoom/mapping.py vs replay/provider.py _build) and the daily volume source.

Starting capital: risk 0.5% of equity, capacity 80% of equity, symbol exposure 60%, fractional quantities, no lot rounding: quantities and USD PnL scale by 0.74289, return % unchanged.

## 7. TYPE C blockers (priority order)

| # | Impact | Item | Paper | Backtest | Normalize? | Historical? | Code area |
|---|---|---|---|---|---|---|---|
| 1 | RESULT_MEANING | Candidate universe, market-cap source and scan-time cut | Kiwoom trade-value top 10 at 07:00 KST, scan-time cap | Research Universe V2 (29), static dated cap, 20:00 ET cut | only by policy (one universe definition for both) | NO for the Kiwoom ranking before recorded ScannerRuns; forward only | `app/market/universe.py, app/dev/run_morning_scanner.py, backtest/research/scanner.py` |
| 2 | RESULT_MEANING | Entry approval authority (human APPROVE + GPT TOP8 coverage) vs ALL ASSUMED | APPROVE rows only | every TOP8 symbol approved | policy only | NO for the research range (no GPT/human records replayed; the RECORDED coverage was not re-measured in this audit); forward only via RECORDED mode | `entry_management_runtime.load_approved_candidates; backtest/authority/*` |
| 3 | RESULT_MEANING | Overnight and trailing authority from the GPT record vs UNKNOWN_CLOSE/ASSUMED_HIGH and UNKNOWN_DEFAULT | overnight_suitability and trailing_profile from GPT | declared modes | policy only | NO for past GPT records; forward via RECORDED | `engine.py:274,298-306; authority/resolver.py` |
| 4 | TRADE_OCCURRENCE | Premarket volume ratio basis (denominator Kiwoom daily vs Massive REGULAR minute sum; numerator provider coverage) | Kiwoom | Massive | YES in principle: one canonical basis for numerator and denominator per provider, measured | PARTIAL (Massive daily = Kiwoom daily basis MATCHING; numerator ratio unmeasured in repo) | `build_premarket_context; replay/provider.py:300-318; kiwoom/mapping.py:63` |
| 5 | TRADE_OCCURRENCE | Candidate evaluation order (GPT rank vs scanner rank) under capacity | GPT rank | scanner rank | policy only | NO (no GPT rank history) | `entry_management_runtime.py:260,860` |
| 6 | TRADE_OCCURRENCE | Bar publication latency and tick loop order | actual Kiwoom arrival; concurrent loops | +1 min exact; fixed order | measure latency; order is a design choice | NO for latency | `kiwoom/mapping.py FUTURE_DATA; portfolio/replay.py; app/main.py` |
| 7 | TRADE_OCCURRENCE | Duplicated orchestration (Day2 activation, holding limit, add, daily state) | services/*_lifecycle | replay/position_replay, portfolio/account | YES: differential tests or shared implementation | n/a | `position_lifecycle.py, end_of_day_lifecycle.py, position_replay.py, portfolio/account.py` |
| 8 | SIZING_PNL | Daily price adjustment (Kiwoom daily upd_stkpc_tp=1 vs Massive unadjusted) | possibly adjusted | unadjusted | YES (confirm and fix one basis) | YES | `kiwoom/client.py:211` |
| 9 | DISPLAY_REPORT | Starting capital 7,428.92 vs 10,000 USD | 7,428.92 | 10,000 | YES (choose one) | YES | `bootstrap_paper_account.py; baseline/contract.py` |

## 8. Minimum fix plan (design only)

1. **canonical market metrics**: one written basis for premarket volume ratio (numerator and denominator) per provider; replay denominator from the provider daily bar (Massive daily = Kiwoom acc_trde_qty, MATCHING_BASIS) instead of REGULAR minute sums; measure the Kiwoom/Massive PM numerator ratio as a stored artifact; confirm Kiwoom daily adjustment. Tests: basis contract tests per provider; byte parity of every stored run under an explicit basis version bump.
2. **scanner / universe parity**: choose one universe policy for both (paper ranks the research universe, or research replays archived paper ScannerRun inputs forward); archive every paper scan input. Tests: same scan inputs -> same TOP8 in both paths.
3. **approval / authority parity**: forward: backtest RECORDED mode fed by paper records; or paper adopts the ASSUMED policy for a research account. Tests: authority resolver differential on recorded sessions.
4. **capital parity**: one starting cash for both. Tests: sizing identity at equal equity.
5. **execution / risk / orchestration parity**: remove duplicated wrappers or add paper-vs-replay differential tests (Day2 activation, holding limit, add, daily state, loop order). Tests: differential on recorded paper sessions.
6. **regression replay**: replay recorded paper sessions through the backtest path and require identical decisions. Tests: per-session decision/fill identity.

O0/O1 are kept as they are, labelled RESEARCH_ENVIRONMENT_RESULT until paper parity is established.

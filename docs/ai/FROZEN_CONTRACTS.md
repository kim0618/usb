# USB Frozen Contracts

Every value in this document was read from the current source tree on
2026-09-04. Nothing here is inferred or remembered. Each section names the file
that owns the truth.

**Rule for agents:** do not change any value or rule below unless the user
explicitly asks for that change in the current task. Tuning, "improving", or
"fixing" a frozen number as a side effect of another task is a defect, not an
improvement. If a change is genuinely required, it needs a new version string
(for example `quant_v1`), not an edit in place, because historical persisted
runs carry the old version and must stay interpretable.

Companion documents: `docs/ai/CURRENT_STATE.md` (where the project is now),
`docs/ai/SAFETY_RULES.md` (what an agent must never do).

Authoritative freeze declaration: `docs/INTERNAL_V1_FREEZE.md` (2026-09-02).

---

## Version strings

Source: `backend/app/api/router.py::settings_api`, which reads each owning
config. These strings are persisted with the data they produced.

| Component | Version | Owner |
| --- | --- | --- |
| Quant scanner | `quant_v0` | `backend/app/scanner/config.py` |
| Top-8 research prompt | `top8_research_v0` | `backend/app/research/versions.py` |
| Stock detail prompt | `stock_detail_research_v0` | same |
| Research import schema | `gpt_research_v0` | same |
| Evidence confidence | `evidence_v0` | same |
| Risk | `risk_v0` | `backend/app/risk/config.py` |
| Execution simulation | `execution_v0` | `backend/app/execution/config.py` |
| Strategy | `strategy_v0` | `backend/app/strategy/config.py` |
| Shadow variants | `shadow_variants_v0` | same |
| Runtime safety / operations | `operations_v0` | `backend/app/monitoring/config.py` |
| Adoption triage | `adoption_filter_v0` | `backend/app/research/adoption.py` |
| Backend API | `/api/v1`, 30 routes + `/health` = 31 OpenAPI paths | `backend/app/api/router.py` |
| Alembic head | `20260901_0008` | `backend/migrations/versions/` |

---

## Quant V0

Source: `backend/app/scanner/config.py`, `metrics.py`, `normalization.py`,
`scanner.py`. Narrative: `docs/QUANT_SCANNER.md`.

Eligibility (each failure yields an explicit `ExclusionReason`):

| Rule | Value | Reason code on failure |
| --- | --- | --- |
| Metadata must exist | - | `MISSING_METADATA` |
| Benchmark symbol excluded from candidates | `SPY` | `BENCHMARK_SYMBOL` |
| Symbol must be active | - | `INACTIVE` |
| Market cap must exist | - | `MISSING_MARKET_CAP` (never silently 0) |
| Minimum market cap | `300,000,000` | `MARKET_CAP_TOO_LOW` |
| Minimum latest close | `5.0` | `PRICE_TOO_LOW` |
| Minimum 20-day average dollar volume | `20,000,000` | `LOW_LIQUIDITY` |
| Required aligned history | `21` bars (`max(lookback)+1`) including the scan date | `INSUFFICIENT_HISTORY` |
| Trading-date grid must match the benchmark exactly (no forward fill) | - | `MISALIGNED_HISTORY` |
| Metrics must be finite | - | `INVALID_MARKET_DATA` |

Metric formulas (all on point-in-time filtered bars, `available_at <=
scan_as_of`, `trading_date <= trading_date`):

- `rvol` = latest volume / mean(volume of previous 20 bars), lookback 20.
- `relative_strength` = 5-day return of the symbol minus 5-day return of SPY.
- `dollar_volume` = latest close x latest volume (latest bar only).
- `momentum` = 20-day return.
- `average_dollar_volume` = mean(close x volume) over the previous 20 bars
  (eligibility only, not a scored metric).

Normalization (cross-sectional, within the eligible pool):

1. `log1p` on `rvol` and `dollar_volume` only (both non-negative).
   `relative_strength` and `momentum` are signed and are not log-transformed.
2. Winsorize at percentiles `5.0` / `95.0`.
3. Population z-score (`numpy.std`, ddof=0). If the standard deviation is 0,
   every symbol gets 0.
4. NaN/infinity anywhere raises `DataError`; it is never silently coerced.

Weights (must sum to 1.0, enforced in `__post_init__`):

```text
rvol                0.35
relative_strength   0.30
dollar_volume       0.20
momentum            0.15
```

Final score = sum of the four weighted normalized contributions.

Ranking and TOP8 (`scored.sort(key=lambda row: (-score, -normalized_rvol, symbol))`):

1. final score descending
2. normalized RVOL descending
3. symbol ascending

Ranks start at 1. `is_top8 = rank <= 8`. If fewer than 8 are eligible, all
eligible candidates are TOP8. Input or DB insertion order is never a tie-break.

Persisted `ScannerRun` / `ScannerCandidate` snapshots are the Quant truth. The
API, the research prompt, and the frontend read the snapshot; they never
recompute a score.

The Kiwoom ranking pre-filter (`backend/app/market/universe.py`, limit 1..100)
only bounds acquisition. It is not an eligibility rule and must not become one.

---

## Research

Source: `backend/app/research/`, `backend/app/services/research.py`. Narrative:
`docs/GPT_RESEARCH.md`.

- **No automatic GPT API call exists anywhere in V1.** USB renders a prompt and
  imports a pasted JSON. Do not add a provider SDK, an HTTP call to an LLM, or
  an auto-import job.
- `prompt_version`: `top8_research_v0` (TOP8), `stock_detail_research_v0`
  (single symbol detail). `ACCEPTED_PROMPT_VERSIONS` accepts **only**
  `top8_research_v0` on import; the detail prompt is a research aid and has no
  import path and no DB merge.
- `schema_version` must equal `gpt_research_v0` exactly.
- The import contract is the Pydantic model `GPTResearchResult`, rendered into
  the prompt as its own JSON Schema. `extra="forbid"` at both levels: unknown
  top-level or per-candidate fields are rejected, not ignored.
- Import validation, in order: 1 MB byte limit; JSON parse; schema validation;
  `schema_version` / `prompt_version`; the scanner run exists and is
  `COMPLETED`; `trading_date` matches the run; no duplicate tickers; the
  candidate symbol set and count must exactly equal the stored TOP8; GPT ranks
  must be unique and contiguous from 1; the SHA-256 of the canonicalized payload
  must not already exist for that run.
- The exact raw JSON string is persisted on `GPTAnalysis.raw_json` together with
  `payload_hash`. It is immutable; there is no edit path.
- `analysis_at` must be timezone-aware. `provider` and `model` are required
  non-blank operator-supplied audit metadata; USB does not infer them.
- `unknown_fields` is an explicit, deduplicated, sorted list. `UNKNOWN` is a
  legal enum value for `catalyst_duration`, `stop_profile`, `trailing_profile`,
  and `overnight_suitability`. Unknown is a valid answer and must stay valid.
- Sources are per-claim `{claim, url, type, title, published_at}` with
  `type` in `SEC | IR | EXCHANGE | OFFICIAL | NEWS | OTHER`. Claims are stored
  lowercased; exact duplicates are dropped. `published_at`, when present, must
  be aware.

### Evidence (`evidence_v0`)

Source: `backend/app/research/evidence.py`. Deterministic structural coverage
score, **not** a probability that a claim is true. Frozen formula:

```text
quality: SEC/IR/EXCHANGE/OFFICIAL = 1.0, NEWS = 0.8, OTHER = 0.4
score  = 40 * best quality of the "catalyst" claim
       + 10 * sum(best quality of each of the top 4 non-catalyst claims)
       + 10 if any source is SEC/IR/EXCHANGE/OFFICIAL
       + 10 if sources span 2 or more distinct URL hosts
       - 5  * min(count of distinct unknown_fields, 5)
score  = clamp(round(score), 0, 100)
if there is no "catalyst" source at all: score = min(score, 49)
```

Evidence is computed once at import time and persisted. It is never recomputed
by the API or the frontend.

---

## Human Decision

`adoption_filter_v0` is a read-only triage layer. Overall uses 70/60, catalyst
75/60, momentum 60/40, and safety (`risk_score`, higher is safer) passes at 30.
All PASS is `ADOPTION_CANDIDATE`; a marginal dimension without a failure is
`REVIEW_REQUIRED`; failures are `EXCLUDED`, except one failure within five
points when all other dimensions pass. Evidence, fundamentals and Quant fields
are advisory only. The filter never persists or creates a decision.

`recommendation_rank` is a deterministic 1..N human-review order over the same
persisted truth: classification group (`ADOPTION_CANDIDATE`, `REVIEW_REQUIRED`,
`EXCLUDED`), then GPT rank, then Quant rank (null last), then symbol. It
introduces no score, changes no classification, and is not an approval - the UI
`순위 #1` never implies or produces an `APPROVE`.

Source: `backend/app/services/research.py::HumanDecisionService`,
`backend/app/models/research.py`.

- Values are exactly `APPROVE` and `REJECT`. There is no third stored value;
  "no row" means undecided.
- **At most two `APPROVE` rows per `GPTAnalysis`.** Enforced in the service
  (`count_approvals(analysis_id, excluding_symbol=symbol) >= 2` raises), surfaced
  as HTTP 409.
- **`APPROVE` never means `BUY`.** It grants the symbol permission to be
  evaluated by Strategy and Risk today. Entry still requires the premarket gate,
  the opening range / VWAP breakout, a REGULAR session, and Risk approval.
- Research import and the human decision are separate records and separate
  steps. Importing research must never create a decision, and a decision must
  never be inferred from GPT scores, evidence, or Quant rank.
- **No automatic decision.** No scheduler, seed, migration, test fixture used in
  production paths, or agent may write a `HumanDecisionRecord` on the operator's
  behalf. `backend/app/dev/seed_ui.py` deliberately creates none, and
  `inspect_readiness` in `backend/app/dev/run_real_market_simulation.py` is
  read-only and asserted clean by test.
- A decision is mutable: re-deciding the same symbol updates the existing row
  (decision, note, `decided_at`). `decided_at` must be aware.
- UI labels are display-only: `APPROVE = 채택`, `REJECT = 거절`, no row =
  `미결정`. The API payload and the database keep the English enums.

---

## Risk V0

Source: `backend/app/risk/config.py`, `backend/app/risk/engine.py`. All money is
`Decimal`; floats are converted through `decimal_from` (via `str`) at the
boundary and never used for money arithmetic.

| Parameter | Value |
| --- | --- |
| `risk_per_trade_pct` (1R) | `0.005` of equity |
| `max_daily_risk_units` | `2` (daily risk limit = 2 x 1R) |
| `base_capacity_pct` | `0.80` |
| `pyramid_reserve_pct` | `0.20` (base + reserve must total exactly 1) |
| `max_symbol_exposure_pct` | `0.60` |
| `max_new_symbols_per_day` | `2` |
| `max_pyramid_adds` | `1` |
| `overnight_stress_gap_pct` | `0.20` |
| `max_overnight_stress_loss_pct` | `0.05` of equity |
| `max_overnight_positions` | `1` |

Frozen semantics:

- `1R = equity * risk_per_trade_pct`. Requested quantity = `1R / per-share risk`
  in account currency; final quantity is capped by
  `min(requested notional, base capacity remaining, symbol capacity remaining,
  cash)` and never rounded to a broker lot. Broker lot, tick, and price rounding
  do not belong in Risk.
- Reservations are additive per trading date (`DailyTradingState`:
  `attempted_symbols`, `planned_risk_reserved`, `base_notional_reserved`,
  `pyramid_notional_reserved`, `add_counts`). The ACTUAL book uses persisted
  daily state; each shadow variant keeps its own isolated state keyed by
  `(trading_date, variant)`.
- **Same-day re-entry is forbidden**: a symbol already in `attempted_symbols` is
  rejected with `SYMBOL_ALREADY_ATTEMPTED`.
- **Averaging down is forbidden**: a pyramid add requires
  `current_price > average_price`, otherwise `POSITION_NOT_PROFITABLE`.
  Pyramid funding comes only from the 20% reserve.
- `safe_mode` rejects both base entry and pyramid add (`SAFE_MODE`).
- For the ACTUAL book, `human_approved` false rejects with
  `HUMAN_NOT_APPROVED`. The SHADOW book intentionally runs without human
  approval.
- Cross-currency sizing requires an explicitly directed `FxRate`; a missing or
  mis-directed rate returns `CURRENCY_MISMATCH` / `INVALID_FX_RATE`. Risk never
  invents a rate.
- Overnight: `evaluate_overnight_notional` returns `HOLD_FULL`,
  `REDUCE_AND_HOLD`, or `EXIT_ALL` from a 20% gap stress test capped at a 5%
  equity loss, and refuses a second overnight position.
- Every rejection is a typed `RiskRejectionReason`. Do not add silent
  pass-through paths.

---

## Execution V0 (simulation cost model)

Source: `backend/app/execution/config.py`, `backend/app/broker/sim.py`.

| Parameter | Value |
| --- | --- |
| `default_spread_bps` | `10` |
| `default_slippage_bps` | `5` |
| `commission_bps` | `10` |
| `fx_cost_bps` | `0` |
| `fill_delay_bars` | `1` (next bar only) |
| `partial_fill_enabled` | `False` |
| `partial_fill_ratio` | `0.5` (only if enabled) |
| `ambiguous_bar_policy` | `WORST_CASE` (the only accepted value) |

SimBroker semantics: long-only; fills at the next bar's open, adjusted adversely
by `(spread + slippage)` bps; rejects with a typed `RejectionReason`
(`INVALID_QUANTITY`, `INVALID_PRICE`, `UNSUPPORTED_CURRENCY`, `NO_NEXT_BAR`,
`INVALID_MARKET_DATA`, `INSUFFICIENT_CASH`, `SELL_EXCEEDS_POSITION`,
`UNSUPPORTED_SIDE`, `ORDER_ALREADY_FINAL`); tracks cash, weighted-average cost,
realized PnL, gross/net PnL and gross/net R per trade, and an ambiguous-bar
counter. Net R after costs is the official performance figure.

SimBroker state is in-memory and process-local. That is a known limitation, not
a contract to work around with a global singleton. See
`docs/ai/CURRENT_STATE.md` and `docs/ai/SAFETY_RULES.md`.

---

## Strategy V0

Source: `backend/app/strategy/config.py`, `engine.py`, `lifecycle.py`,
`indicators.py`. Narrative: `docs/STRATEGY_V0.md`.

| Parameter | Value |
| --- | --- |
| `premarket_gap_min_pct` / `max_pct` | `+0.02` / `+0.15` inclusive |
| `premarket_volume_ratio_min` | `0.05` of historical average daily volume |
| `opening_range_minutes` | `15` (09:30-09:44 bar starts) |
| `entry_deadline_et` | `10:30` ET inclusive |
| `require_price_above_vwap` | `True` |
| `require_opening_range_breakout` | `True` |
| `max_entry_attempts_per_symbol` | `1` |
| `atr_period` | `14` (SMA of 1-minute regular-session True Range) |
| Trailing ATR multipliers | TIGHT `1.0`, NORMAL/UNKNOWN `1.5`, WIDE `2.0` |
| `trailing_activation_r` | `1` (trailing and add both need +1R) |
| `max_pyramid_adds` | `1` |
| `max_holding_trading_days` | `2` (Day 3 does not exist) |
| `overnight_enabled` | `True`, `overnight_max_positions` `1` |
| `closing_review_before_close` | `10` minutes |
| `closing_strength_min` | `0.70` |
| `overnight_max_stop_distance_r` | `2` |

Frozen lifecycle (`StrategyPhase` and the `_TRANSITIONS` map are the contract;
an illegal transition raises):

```text
RESEARCH_READY -> HUMAN_APPROVED | HUMAN_REJECTED | PREMARKET_PASSED | PREMARKET_REJECTED
HUMAN_APPROVED -> PREMARKET_PASSED | PREMARKET_REJECTED
PREMARKET_PASSED -> OPENING_RANGE_BUILDING -> WAITING_ENTRY -> ENTRY_SIGNALLED -> POSITION_OPEN
POSITION_OPEN -> PYRAMID_ADDED | OVERNIGHT_REVIEW | EXIT_SIGNALLED
PYRAMID_ADDED -> OVERNIGHT_REVIEW | EXIT_SIGNALLED
OVERNIGHT_REVIEW -> OVERNIGHT_HELD | EXIT_SIGNALLED
OVERNIGHT_HELD -> DAY2_ACTIVE | EXIT_SIGNALLED
DAY2_ACTIVE -> EXIT_SIGNALLED -> EXITED
terminal: HUMAN_REJECTED, PREMARKET_REJECTED, EXITED, NO_TRADE
```

Frozen decision rules:

- Premarket gate order: human approval (ACTUAL book only) -> data validity ->
  research blocked -> new negative catalyst -> gap low -> gap high -> premarket
  volume. Each failure returns its own `StrategyReason`.
- Entry requires, on regular-session bars only: the opening range is complete,
  now is at or after 09:45, now is at or before the 10:30 deadline, reference
  price strictly above session VWAP **and** strictly above the opening-range
  high. Initial stop is the opening-range low and must be strictly below entry.
- Stop evaluation precedes trailing on the same bar: a stop that existed before
  the bar is checked against the bar low first; a stop raised by this bar's high
  only becomes active on the next bar. The active stop never loosens.
- ATR trailing activates only after `highest - entry >= initial R`.
- Add (pyramid) requires: no add signal issued yet, `add_count <
  max_pyramid_adds`, price above average price, price above VWAP, gain at least
  +1R, and a new high. Winners only, once.
- Closing review: `holding_day_number >= variant.max_holding_days` forces EXIT.
  Overnight hold requires all of: the variant allows overnight, overnight is
  enabled, `overnight_suitability` in `{MEDIUM, HIGH}`, no new negative
  catalyst, price above VWAP, closing strength `>= 0.70`, `current_price -
  active_stop <= 2 x initial R`, an active stop below price, the risk stress test
  passes, and an overnight slot is free.
- Day-2 premarket may emit EXIT on a new negative catalyst; otherwise HOLD.
- Day 2 is the next XNYS session (`MarketCalendar.holding_day_number`), never
  the next calendar day.
- One engine serves both books. Shadow does not get its own strategy engine, and
  Strategy never imports a concrete broker.

---

## Session Policy

Source: `backend/app/strategy/session_policy.py`. Audit:
`docs/SESSION_POLICY_AUDIT.md`. Table: `docs/REAL_MARKET_SIMULATION.md`.

| Session | Market data | New entry | Pyramid | Position monitoring | Overnight review |
| --- | --- | --- | --- | --- | --- |
| PREMARKET | yes | **no** | **no** | yes (observe/context) | no |
| REGULAR | yes | yes | yes | yes | yes (closing review) |
| POSTMARKET | yes | **no** | **no** | yes (existing position only) | yes |
| CLOSED (non-session) | no | no | no | no | no |

- `MarketCalendar` (XNYS via `exchange_calendars`) is authoritative for trading
  days, holidays, DST, and early closes. Clock-derived session labels on bars
  never override calendar truth.
- `StrategyLifecycleRunner.execute_entry` / `execute_add` return a no-op when
  the session forbids the action, and `regular_fill_bars` restricts fill
  candidates to bars inside that day's regular open/close window, so a signal
  cannot fill across a session boundary.
- All datetimes in the system are timezone-aware. Naive datetimes are rejected
  by domain validators (bars, decisions, snapshots, strategy state).

---

## Shadow

Source: `backend/app/strategy/config.py::VARIANT_CONFIGS`,
`backend/app/shadow/`, `backend/app/strategy/runner.py`.

| Variant | Overnight | Max holding days | Trailing | ATR multiplier | Control |
| --- | --- | --- | --- | --- | --- |
| A | no | 1 | ATR | 1.5 | |
| B | yes | 2 | ATR | 1.0 | |
| C | yes | 2 | ATR | 1.5 | **yes** |
| D | yes | 2 | ATR | 2.0 | |
| E | no | 1 | STRUCTURE | none | |

- **C is the control.** `ShadowVariant.C.is_control` is true and the API marks
  `control: variant == "C"`.
- All eight TOP8 candidates fan out to all five variants, independently of the
  human decision (`ShadowService.fan_out` ignores approvals entirely).
- Shadow shares the same Strategy engine, Risk engine, and SimBroker code as the
  ACTUAL book. Isolation is per-variant daily risk state, not a forked engine.
- Shadow uses `TradingEligibility(human_approved=False, book="SHADOW")`; Risk
  skips the human gate only for the SHADOW book.
- Shadow trailing uses the variant multiplier; ACTUAL uses the research-derived
  `trailing_profile`.
- Uniqueness: one row per `(scanner_candidate_id, variant, variant_version)`.
- Evaluation eligibility thresholds (`backend/app/shadow/domain.py`):
  `MIN_ELIGIBLE_TRADES = 300`, `MIN_TRADING_DAYS = 60`. Do not draw strategy
  conclusions below both.
- `shadow_trades` has **no source column**. `/api/v1/shadow/*` hardcodes
  `"source": "SIMULATION"`. Until a migration adds a real source, results from
  different data origins must not be aggregated together.

---

## Broker and Kiwoom mode

Source: `backend/app/core/config.py`, `backend/app/market/factory.py`,
`backend/app/integrations/kiwoom/client.py`.

- **`SimBroker` is the only broker implementation.** There is no
  `KiwoomPaperBroker`, no `KiwoomLiveBroker`, no order builder, and no broker
  registry that could switch execution away from simulation.
- `BROKER_PROVIDER` accepts only `simulation`; any other value raises at
  settings validation.
- `KIWOOM_MODE` accepts only `market_data_only`; any other value raises.
- `MARKET_DATA_PROVIDER` accepts `fake | replay | kiwoom`. Selecting `kiwoom`
  additionally requires simulation broker and market-data-only mode, enforced
  twice: in `Settings.validate_stage_10a_safety` and again in
  `build_kiwoom_provider`.
- The Kiwoom HTTP client has a hard `(api-id, path)` allowlist:
  `usa10100`, `usa10099`, `usa20100`, `usa20530`, `usa20540`, `usa20550`,
  `usa20590`, `usa06011`, `usa06012` over `/api/us/stkinfo`, `/api/us/mrkcond`,
  `/api/us/rkinfo`, `/api/us/chart`. Anything else raises `ENDPOINT_BLOCKED`
  before any HTTP call. `/api/us/ordr` is absent by construction, and
  `order_request_count` instruments it at zero.
- No account, balance, position, order, cancel, modify, or FX endpoint is
  allowlisted or called.
- Rate limiting: separate auth / query / chart / realtime buckets; 3 req/s real,
  1 req/s mock. Bounded retry (default 2) with exponential backoff on timeout,
  network error, 429, and 5xx. Auth failure, invalid request, and invalid symbol
  are not retried. Provider errors are normalized to `AUTH_FAILED`,
  `RATE_LIMITED`, `MARKET_DATA_UNAVAILABLE`, `INVALID_SYMBOL`,
  `PROVIDER_TIMEOUT`, `ENDPOINT_BLOCKED`; raw bodies never reach the UI.

### Point-in-time mapping rules

- A daily bar is observed at 16:00 ET of its trading date; a bar whose close has
  not happened yet is dropped as `FUTURE_DATA`.
- A minute bar is available no earlier than one minute after its bar-open
  timestamp; future/incomplete bars are dropped.
- `available_at >= observed_at` is validated on every bar, and the scanner
  independently re-applies `available_at <= scan_as_of`.
- Missing market cap stays `None` and produces `MISSING_MARKET_CAP`. It is never
  mapped to zero, and USB never computes market cap itself.

---

## API V1 contract

Source: `backend/app/api/`, narrative `docs/API_V1.md`.

- Prefix `/api/v1`; `/health`, `/docs`, `/openapi.json` are unversioned.
- Routers are thin adapters. They must not recompute Quant scores, evidence,
  approval limits, risk sizing, strategy transitions, or runtime mode.
- Errors are `{"error": {"code", "message", "details?"}}`. 422 validation, 404
  missing resource, 400 malformed, 409 workflow conflict, 500 generic. Stack
  traces and internal paths are never returned.
- Financial `Decimal` values are JSON strings and preserve stored scale.
  Analytical float scores stay JSON numbers. Datetimes are ISO 8601 aware.
- `/api/v1/settings` is an allowlist. Database URLs, credentials, keys, tokens,
  and `.env` content are never serialized.
- `/api/v1/trading` reports `broker_mode: "SIMULATION"` and
  `availability: "NO_ACTIVE_SIM_BROKER"` with an empty account when no in-process
  SimBroker exists. That empty state is correct behavior, not a bug to paper
  over.
- `/api/v1/shadow/summary` is backward compatible: no parameters means all-time.
  `start_date` / `end_date` are inclusive; closed paths use the `exit_at` date in
  America/New_York, non-closed paths use their `ScannerRun.trading_date`. An
  inverted range is 400, invalid syntax is 422.
- Kill switch requires `confirm: true`. Failure resolution alone never restores
  NORMAL.
- CORS uses explicit origins only (no wildcard) and allows GET/POST/PUT only.

---

## Runtime safety (`operations_v0`)

Source: `backend/app/monitoring/`. Narrative: `docs/RUNTIME_SAFETY.md`.

- Modes: `NORMAL`, `SAFE_MODE` (no new ENTER/ADD; normal exits, risk-reducing
  partial sells, mandatory Day-2 exit and reconciliation still allowed),
  `HALTED` (no automatic strategy action; kill-switch liquidation and status
  only).
- `SAFE_MODE` and `HALTED` survive restart. Reconciliation success never
  silently clears `HALTED`. Recovery requires manual acknowledgement,
  reconciliation PASS, and zero unresolved CRITICAL failures.
- Broker position is execution truth. A mismatch with strategy state creates a
  mismatch record, a failure event, and SAFE_MODE. Internal state is never
  silently corrected.
- Defaults: heartbeat 60s interval / 180s stale, market data 120s stale,
  execution failure threshold 3 within a 300s sliding window, premarket
  monitoring off.
- `runtime_state` is a singleton row (`id = 1`). Failure metadata is canonical
  JSON and must never contain a secret or token.

---

## UI / Display contracts

Source: `frontend/lib/display.ts`, `frontend/lib/format.ts`,
`docs/DESIGN_SYSTEM.md`, `docs/FRONTEND_V1.md`.

- The backend is the only source of truth. The frontend never recomputes a
  financial value, never falls back to mock data, and never infers a missing
  field. Missing contract data renders as `-`, `데이터 미제공`, or `정보 없음`.
- Enums stay English in the API and the database. Korean labels are display-only
  via the shared mappers. An unknown enum renders as its raw value, never as an
  empty string or `알 수 없음`.
- Human decision labels: `APPROVE = 채택`, `REJECT = 거절`, none = `미결정`.
- **USD is the primary display currency.** KRW is a secondary, prefixed
  `환산 약`, and is shown only when a trusted FX source supplies the converted
  amount. There is no trusted FX source today, so production must not render
  KRW. Never hardcode a USD/KRW rate in the frontend.
- Strategy status mapping (`strategyStatusDisplay`) returns a Korean label plus a
  semantic tone; an unknown raw state keeps the raw value with a neutral tone.
- Color is supporting information only; every critical state also has a text
  label. Green = success/approve/profit, red = danger/reject/halt/loss, amber =
  warning/safe mode, blue = interaction and selection only.
- Dark is the default theme; both themes share the same semantic tokens
  (`background`, `surface`, `line`, `foreground`, `primary`, `success`,
  `warning`, `danger`). Theme choice persists in `usb-theme` localStorage.
- Pixel-level layout is intentionally **not** frozen here. Design refinements are
  allowed; the semantics above are not.

---

## Explicitly not frozen

- Kiwoom adapter details beyond the read-only market-data boundary:
  authentication ergonomics, capability/status reporting, realtime transport,
  paper and live modes.
- Actual commissions, slippage, FX costs, tick/lot/fractional rounding, and
  broker-native protective orders. These need real broker evidence.
- Naver Cloud deployment, reverse proxy, systemd, watchdog.
- Frontend layout and visual refinement within the display semantics above.
- Any strategy performance conclusion. Synthetic replay and fixture results are
  implementation validation only.

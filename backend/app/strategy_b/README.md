# Strategy B `REALTIME_MOMENTUM_V1`: Pure Research Layer

## Purpose

Point-in-time feature calculations, types and config contracts for Strategy B. The same
functions will serve a future historical backtest and a future realtime runtime. This layer
has no I/O, no clock, no database and no broker: input goes in as values, results come
back as immutable values.

Isolation: nothing here imports Strategy A (`strategy/`, `risk/`, `broker/`) or the
backtest core (`backtest/portfolio`, `backtest/replay`, collectors). A test checks this
through the AST.

| Module | Contents |
|---|---|
| `models.py` | `SetupType` (HOD_BREAKOUT, FIRST_PULLBACK), `CandidateState`, `Session`, `MomentumBar`, `Measured` + `Availability`, `FeatureSnapshot`, status enums |
| `config.py` | `StrategyBConfig` sections, strict `to_dict`/`from_dict`, `canonical_json`, `fingerprint` |
| `session.py` | `SessionBoundaries` (caller-supplied, early closes included), `AggregationScope` |
| `features.py` | `SessionTape` and the feature functions |
| `snapshot.py` | `compute_feature_snapshot` |
| `rvol.py` | time-of-day RVOL from minute bars |
| `split_adjustment.py` | PIT split factors |
| `sparse_session.py` | HYBRID-S post-session validator, synthetic minute-clock view |
| `halt_inference.py` | research-only halt flag |
| `corporate_actions.py` | corporate-action flags |
| `scope.py` | PIT research scope (L1) from D-1 inputs |
| `spread.py` | inputs for a future spread model (no estimate) |
| `scanner.py` | B-F0 gate (`GateReason`) and candidate score, with a deterministic ranking |
| `eligibility.py` | B-F0 hard eligibility: scope, corporate actions, halt, tape density |
| `setups.py` | `HOD_BREAKOUT` detection: window, trigger price, initial stop |
| `fsm.py` | the candidate FSM: `Candidate`, `Tick`, `FillOutcome`, `advance` |
| `exits.py` | open-position management: stop, 2R partial, trail, time stop, end of day |
| `sizing.py` | share count from the risk budget and the position cap |

Units: `return_*` and `*_pct` are percent points (5.0 = 5%), `*_fraction` is 0 to 1.

## Time Units

```text
*_minutes = wall-clock time
*_bars    = actual observed, non-synthetic market bars
```

Strategy B accepts sparse tapes, so the two are not interchangeable. With trades at

```text
09:31
09:34
09:39
```

there are 3 observed bars but 8 elapsed minutes, and 5 bars can take far more than 5 minutes.

| Unit | Config fields |
|---|---|
| wall-clock minutes | `candidate.candidate_ttl_minutes`, `candidate.setup_ttl_minutes`, `candidate.signal_ttl_minutes`, `exit.time_stop_minutes`, `exit.eod_min_margin_minutes`, `features.rolling_dollar_volume_window_minutes`, `features.volume_acceleration_window_minutes`, `halt.min_gap_minutes`, `halt.max_gap_minutes` |
| actual bars | `hod_breakout.consolidation_min_bars`, `hod_breakout.consolidation_max_bars`, `first_pullback.min_duration_bars`, `first_pullback.max_duration_bars` |

A TTL or time stop in minutes cannot be stretched by a silent tape. A pattern length in bars
never counts synthetic carry-forward minutes. The names are the canonical contract: there is
no alias for the earlier bar-suffixed TTL and time-stop keys, and `from_dict` rejects them.

## PIT guarantees

- A bar opening at `t` is available at `t + 1 minute` (`models.AVAILABILITY_DELAY`, the same
  contract as the historical replay provider). Every feature cuts the tape with
  `available_at <= as_of` before reading, so bars after `as_of` cannot change a result.
- HOD/LOD are running extremes up to `as_of`, never the day's final high.
- RVOL history must be strictly earlier sessions. A profile dated today or later raises.
- Splits apply only when `execution_date <= current date`.
- The research scope for date D accepts only metadata as of D-1 or earlier and daily bars
  before D. It raises instead of filtering, and it takes no current price or volume argument.
- The sparse-session verdict needs D's official daily bar, so it is post-session only and
  never a same-day feature. `CA_SUSPECT` accepts only an earlier session's verdict.
- Mutation tests inject future bars, future splits, a final-HOD bar and D-day scope data and
  assert that nothing changes, or that a refusal is raised.

## Sparse-bar policy (HYBRID-S)

- Windows are wall-clock. `return_Nm` compares the last trade available at `as_of` with the
  last trade available at `as_of - N minutes`, not the bar N positions back.
- A silent minute has zero volume and an unchanged last price. `minute_clock_view` makes this
  explicit as `synthetic=True` bars without touching the tape.
- Synthetic bars are allowed for clock returns, volume windows and RVOL. They are forbidden
  for candlestick patterns, ATR, breakout/pullback detection and fills
  (`require_actual_bars`). `SessionTape` refuses synthetic bars.
- VWAP and dollar volume: see the policy sections below.
- Session validation does not require a bar every minute. It checks minute O/H/L against
  the daily bar, minute volume <= daily volume × tolerance, and an empty tape against daily
  volume > 0. The close is not compared.

## VWAP policy

No synthetic VWAP fallback.

- Historical: session VWAP is Σ(source `vw` × volume) / Σ volume over available bars, using
  the provider's per-bar VWAP. If any bar with volume in the range has no source VWAP, the
  result is `Measured(None, NO_SOURCE_VWAP)`, which is UNKNOWN.
- Forbidden as a stand-in: typical price `(H+L+C)/3`, the close, or any other OHLC blend.
- A zero-volume bar without a VWAP does not block the VWAP (it contributes nothing).

### Realtime VWAP contract (future, not implemented)

If a realtime adapter (for example Kiwoom) delivers trades with `price`, `volume` and
`timestamp`, the Strategy B runtime may compute the session VWAP itself as

```text
session VWAP = Σ(price × volume) / Σ volume   over the session's trades so far
```

and hand it to the feature layer in the bar's `vwap` field (or an equivalent input). The
feature layer does not know or care whether a VWAP came from Massive or from such a runtime
accumulator; it only sees a source VWAP or its absence. If the realtime feed has neither a
VWAP nor trade-level price and volume, realtime VWAP stays `NO_SOURCE_VWAP`. No Kiwoom code
exists in this package.

## Dollar volume policy

- Intraday (scanner) dollar volume is USD and has one definition, `SessionTape.dollar_volume`,
  with the price chosen once in `FeatureConfig.dollar_volume_basis`:
  - `CLOSE` (research default): `minute_dollar_volume = close × volume` per bar, summed over the
    window. Chosen because a close exists in both Massive and realtime minute bars.
  - `SOURCE_VWAP`: `source vwap × volume` per bar, `NO_SOURCE_VWAP` when a VWAP is missing.
- VWAP availability and the dollar-volume basis are separate: with the `CLOSE` basis, dollar
  volume is available even when `session_vwap` is `NO_SOURCE_VWAP`.
- `spread.SpreadFeatureInput.minute_dollar_volume` is `close × volume` of the last bar,
  always the close.
- The research scope's median dollar volume is daily `close × volume` from D-1 and earlier.

## Halt policy

`halt_inference` is a research flag from minute-tape gaps (Basic data has no halt feed).

| Status | Meaning |
|---|---|
| `HALT_INFERRED` | historical minute evidence strongly resembles a halt/resume pattern |
| `NO_HALT_SIGNAL` | the evidence does not indicate a halt |
| `UNKNOWN` | sparse or no-trade data prevents a reliable determination |

`UNKNOWN != HALT` and `UNKNOWN != NO_HALT`. A consumer that needs yes or no must map
`UNKNOWN` explicitly.

Halt uncertainty and low liquidity are separate concepts. `halt = UNKNOWN` together with
low dollar volume is a normal combination. The halt detector never rejects a symbol for low
liquidity; this layer only returns observations, and a future strategy adapter applies a
liquidity filter on the dollar-volume and density features.

## Research scope and test tickers

Scope inputs, in order of trust: PIT ticker metadata (security type, exchange/market,
listing status, `test_issue` flag) as of D-1, then the D-1 daily history. Market cap is not
used.

`ScopeConfig.test_symbols` (ZVZZT, ZWZZT, ZXZZT, ZVV, NTEST, ATEST) is a hand-typed list with
`test_symbols_provenance = RESEARCH_DEFAULT_UNVERIFIED`: verified = false, source = research
default. It is not an official exchange list and has not been checked against exchange
notices. It is a backup safeguard: a match is reported as `TEST_TICKER_UNVERIFIED_LIST`,
separate from `TEST_TICKER` (metadata `test_issue = True`).

## Split policy

Massive `adjusted=true` is not used, because it back-applies splits that happened after
the bar. `split_adjustment` returns `price_factor`, `share_factor`, `split_on_day` and
`recent_split` from raw split records. A split on D applies to D-1 values but not to D's
bars, which already trade post-split.

## Declared rules (B-F0)

`docs/backtest/strategy_b/B_F0_FSM_RULES_V1.md` fixes the gate, the score, eligibility, the
`HOD_BREAKOUT` setup, the entry signal and the exit rules before any B result exists, and
`b_fsm_rules_v1.json` is the machine-readable declaration with a canonical checksum. The code
carries no threshold of its own: every number lives in `StrategyBConfig`, and a test asserts
that the declaration and the config defaults are the same numbers.

`FIRST_PULLBACK` stays defined in the config and undetected: V1 studies one setup.

## Not implemented

- the engine that ties it together: the portfolio (open positions, `max_open_positions`, the
  daily loss limit), the fill price itself, the replay loop and the run store. `fsm.py` asks
  the engine for a fill through `Tick.fill` and never prices one; `exits.py` and `sizing.py`
  decide what happens to a position it already holds, from values it is handed.
- realtime workers, Kiwoom, WebSocket
- a spread estimate (`SpreadFeatures` has no `estimated_spread_pct`), so the declared
  `max_spread_pct` gate is recorded as not applied
- backtest integration (replay loop, run store, metrics, manifest)
- tuned parameters: every config default is `RESEARCH_DEFAULT_UNOPTIMIZED`

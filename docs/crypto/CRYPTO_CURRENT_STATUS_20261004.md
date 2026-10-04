# Current CRYPTO Status

As of 2026-10-04 (KST). Written by reading the repository: commits, committed contracts and
results documents, and the code. Where this document states a verdict it quotes the frozen
document that owns it rather than restating a summary.

There was no canonical crypto status document before this one. `docs/ai/CURRENT_STATE.md` is a
2026-09-04 repository-wide audit of the equities side and mentions nothing here.

## Manual Trading

* **BTC/ETH/SOL multi-symbol implemented.** `b0fb8b1` trades BTCUSDT, ETHUSDT and SOLUSDT from
  one manual terminal, selected by a tab strip, with real Manual LIVE orders on each.
  `44cc50d` fixed the selected tab having no rendered pressed state. `735d0a9` put the three
  PAPER symbols on one wallet.
* **Isolation is structural, not conditional.** There is no symbol-aware adapter; there is a
  dictionary of per-symbol objects, each built from a frozen config naming one instrument. An
  order is refused unless the request, the router config, the reader, the snapshot, the filters
  and the position Binance just reported all name the same symbol.
* **Shared Binance Futures wallet.** One HTTP client (one rate-limit budget), one audit mirror
  (so the cross-symbol order of events is the order they happened), one arm session (arming is
  permission over the account), one user data stream. PAPER mirrors this with `SharedCash`: one
  wallet behind several single-position accounts, so a BTCUSDT position reduces what ETHUSDT can
  open, as it does on the real account. Positions, fills, liquidation and per-symbol realised
  PnL and charges stay per symbol.
* **Symbol-specific**: chart, history, feed, position, Safe MAX, leverage, filters, order and
  CLOSE. **Selected symbol persistence** is in the terminal layout.
* **Production deploy status: not determinable from this repository, and not this document's to
  assert.** The committed `CRYPTO_MULTISYMBOL_V1_REPORT.md` states "LOCAL ONLY. Nothing
  deployed." and lists four prerequisites (ETH/SOL measured reference files on the host, the
  signed read-only validation for both symbols, the `BINANCE_LIVE_MAX_QTY` coin-denominated
  ceiling question, and the account's 20x cap lifting 2026-10-29 01:34 UTC). Two later commits
  the same day, `d3795b5` ("missing runtime dependencies for production release") and `1dd4867`
  ("retain crypto navigation alongside latest strategy UI"), reference a production release.
  **The session that owns that release should confirm the state here.** Nothing in the present
  Stage deployed, restarted or ordered anything.
* **C1 / C1x = BTC-only.** `CRYPTO_C1_SIGNAL_V1.md`: Bybit BTCUSDT linear perpetual, and the
  signal "주문을 내지 않는다" - it touches neither paper, nor Binance, nor leverage, nor Auto
  Exit. Not extended to ETH or SOL.
* **Market Structure = BTC-only.** The collector requests `symbol: "BTCUSDT"` and there is no
  symbol parameter anywhere in `market_structure_v0`.
* **AUTO unchanged.** No AUTO strategy file was extended for multi-symbol.

## Signal / Research

Verdicts quoted from the frozen results documents that own them:

| Line of work | Verdict | Source |
|---|---|---|
| C1 | auxiliary event, places no orders | `CRYPTO_C1_SIGNAL_V1.md` |
| C1 Exit E2 | `EXPLORATORY_ONLY`, PROMISING **0**, E3 recommended NO | `c1_exit/C1_EXIT_E2_RESULTS_V1.md` |
| C1x | diagnostic only. The one candidate that showed information from the rule itself, in an auxiliary comparison explicitly marked "판정 아님" | `c1_exit/C1_EXIT_E2_RESULTS_V1.md` §22 |
| Regime filter (R1) | overall verdict `INCONCLUSIVE`, therefore **no production promotion** | `C1_REGIME_R1_RESULTS_V1.md` |
| Strength | `NO_STRENGTH_SIGNAL` | `C1_STRENGTH_RESULTS_V1.md` |
| Post-entry lifecycle | `WEAK_LIFECYCLE_SIGNAL` | `c1_exit/C1_POST_ENTRY_LIFECYCLE_RESULTS_V1.md` |
| Directional Probability R0 | `B. WEAK_DIRECTIONAL_MODEL`, no horizon reached C, no basis to raise to `USABLE_FOR_FORWARD_MODEL` | `directional_probability_r0/REPORT.md` |

Nothing in this table is promoted to production. The C1 Exit E2 contract's clause Z4 forbids
describing any of it as a confirmed strategy or as operationally applicable, and that holds.

## Market Structure

* **V0 collector complete.** Read-only Binance USDⓈ-M depth and aggTrade, book
  synchronisation with explicit gap classes, persistence with writer authority, derived metrics
  (bands, flow), wall candidates. Frozen contract `btc-ms.v0.1`
  (`market_structure_v0/DATA_CONTRACT_V0.md`, hashed beside it).
* **Wall V2** = frozen `lm-wall.v2` (`liquidity_map_v1/WALL_RULE_V2.md`, hashed). Five
  thresholds: notional 250,000 USDT, multiple 5, distance 1.0 bp, persistence 10,000 ms, bin
  5.0 USDT. The 500,000 USDT display filter is the operator's and is separate.
* **Liquidity Map Preview** = isolated page at `/liquidity-preview`, reading the collector's
  output root without writing to it. It is not wired into the app shell.
* **Staged refresh / buffer-and-replay** (V1.3). A voluntary resnapshot no longer replaces the
  live book: frames arriving during the REST round trip are buffered, the snapshot becomes the
  base of a second book, the buffer is replayed onto it, and the swap happens only when
  `staging.last_update_id == live.last_update_id`. The swap therefore changes levels and bounds
  and never assigns the chain position, so a rollback is structurally impossible.
* **Continuity = `lm-continuity.v4`** (`liquidity_map_v1/WALL_CONTINUITY_V1_2.md`, sha256
  `0ed46edcc0a58957b99cc14bf5b8bba92a4111eb94026b7353311042e58327b3`). HARD is the default and
  carries nothing. SOFT requires all five gates. v4's single change is that S4's 300 ms ceiling
  does not apply to a staged refresh whose chain was preserved, because that window contains no
  unenumerated event; the exemption requires `chain_preserved` plus S1, S2, S3 and a measured
  window, and every transition publishes which case it was in.
* **Writer collision: root cause found and fixed.** `flock` locks an inode, not a path, so
  deleting the lock file let a second writer in silently. The fix is two-sided: the incumbent
  re-checks the inode and fails closed, and a newcomer refuses on a fresh state file plus a live
  pid. Deleting the whole output root is the case the newcomer cannot detect, and that limit is
  pinned by a test rather than left implicit.

### Current known limitations, unfixed

1. **Late aborted-snapshot install.** When a staged refresh is abandoned at
   `REFRESH_DEADLINE_MS` (1,000 ms) its REST read is still in flight. The late snapshot arrives,
   finds no refresh in flight, and is installed by the *recovery* path onto a healthy book with
   no check that it is newer. Measured under forced conditions: the book went back about 141,000
   update ids and the next frame was `GAP_FIRST_DELTA`. **Unfixed. Not assigned to a separate
   session.** Reachable only when the REST read itself exceeds 1,000 ms; observed production
   reads are 75-175 ms and the worst ever recorded is 485 ms.
2. **The 300 s cooldown on a successful refresh.** Measured costing 72 seconds of broken ±0.1%
   band promise in a 30-minute natural session (negative 4.0%, worst -1.85 bp), entirely inside
   one cooldown window. Held at 300 s by decision. **Unfixed, open for review.**
3. **Not production ready**, and the Liquidity Map does not claim to be: no live HARD resync has
   been observed outside forced conditions, the hourly safety refresh has never fired of its own
   accord, there has been no 24-hour session, and the app shell is untouched. The 24-hour
   isolated trial is prepared and not started (`liquidity_map_v1/TRIAL_24H_RUNBOOK_V1_4.md`).

## Current Strategic Direction

Not a sparse-C1-centred system. The intended composition is:

```
Price Structure
  + Liquidity / Order Flow
  + Derivatives Context
  + C1 / Directional Probability as auxiliary inputs
      -> LONG / SHORT / WAIT
      -> Entry / Target / Invalidation
      -> HOLD / CLOSE / REVERSE
```

C1 and Directional Probability enter as auxiliary context, not as the trigger. Their own frozen
verdicts above are the reason: a sparse event with no strength signal, a weak lifecycle signal
and a weak directional model cannot carry a system on its own, and the structure and liquidity
layers are what the recent work has been building.

## Next

1. Liquidity Map final hardening
2. Price Structure R0
3. Price Structure + Microstructure context
4. Manual forward journal
5. Signal system
6. PAPER AUTO
7. LIVE AUTO

**AUTO is not at a live stage and is not close to one.** Items 6 and 7 are listed as the end of
a sequence, not as pending work. Nothing in the research table above has been promoted, so
there is no validated strategy for an AUTO path to execute; manual trading is the only thing
placing orders today.

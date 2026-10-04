# CRYPTO Manual Terminal - Multi-Symbol V1

**Status: LOCAL ONLY. Nothing deployed. Production deploy is the next decision and is yours.**

BTCUSDT, ETHUSDT and SOLUSDT, selectable from one screen, with real Manual LIVE orders on each.
Written against the fifteen-point scope; every item is answered below, including the one that
was not delivered.

---

## 1. BTC hardcode removal, scope and what was left alone

The audit found 60 `BTCUSDT` literals under `backend/app/crypto`. Only a minority are about the
manual terminal; the rest are research, and research results are about the instrument they were
measured on.

**Removed (in scope):**

| Where | Was | Now |
| --- | --- | --- |
| `live/credentials.py` | `SUPPORTED_SYMBOL = "BTCUSDT"`, and `load_config` **raised** on any other symbol | whitelist of three, `LiveConfig.symbols`, `for_symbol()` |
| `terminal/feed.py` | module `SYMBOL`, `TOPICS`, `BOOK_TOPIC` | `BybitPublicFeed(symbol=...)`, topics derived |
| `terminal/chart_history.py` | module `SYMBOL`, cache keyed `(timeframe, before, limit)` | `get(..., symbol=)`, cache keyed by symbol first |
| `terminal/trade_candles.py` | one module-level feed | one feed per symbol, default symbol's is the module attribute |
| `terminal/live_routes.py` | one adapter | `SymbolRuntime` registry, every route takes `symbol` |
| `terminal/api.py`, `server.py` | one feed, one session | one feed and one session per symbol |
| `paper/instrument.py` | BTC defaults only | `from_instruments_info` / `from_file`, per symbol |
| frontend | ~28 hardcoded `BTC` unit labels, `BTCUSDT` header, `선물 · BTCUSDT 가상매매` | base asset from Binance's `baseAsset`, symbol from the snapshot |

**Deliberately left on BTCUSDT**, because they are claims about BTCUSDT and nothing else:
`c1/*` (C1 and C1x), `market_structure_v0/*`, `liquidity_map/*`, `derivatives/*`,
`liquidation_forward/*`, `research/*`, the `dev/` collectors, and `crypto/__init__.py:SYMBOL`
(the D2 data foundation).

## 2. Symbol architecture

The whitelist lives at `backend/app/crypto/symbols.py` - **not** inside `live/`. A structural
test asserts that nothing outside `live/` imports from it, because the PAPER terminal must not
be able to reach the account code even transitively; the whitelist is needed on both sides of
that line, so it belongs on neither.

Isolation is **structural, not conditional**. There is no symbol-aware adapter. There is a
dictionary of per-symbol objects, each built from a frozen config naming one symbol:

```
LiveRuntime
├── shared: credentials, HTTP client (one rate-limit budget, one clock sync),
│           audit mirror (one file, so cross-symbol order is the real order),
│           ArmSession (arming is permission over the account), user data stream (one socket)
└── symbols: { BTCUSDT: SymbolRuntime, ETHUSDT: ..., SOLUSDT: ... }
             each holding its own AccountReader, LiveOrderRouter, BinanceLiveAdapter,
             LeverageCapability (own file), ManualLivePerformance (own file)
```

There is no argument any caller can pass that makes a reader read another symbol, so a BTC
figure cannot reach the ETH screen by being handed the wrong parameter - the parameter does not
exist. `live_runtime.adapter`, `.performance`, `.leverage_capability` keep their names and keep
meaning the default symbol's, so every existing caller and test is unchanged.

**Two bugs this arrangement created and the code now handles:**

- One user data socket serves the whole account. Reading `stream.telemetry.dirty` was correct
  with one adapter: with three, whichever polled first called `clear_dirty()` and the others
  never learned a fill had happened. Each adapter now keeps its own flag, fanned out from
  `LiveRuntime._on_stream_change` to **every** adapter - Binance's `ACCOUNT_UPDATE` carries no
  symbol, and a balance change is an input to every symbol's Safe MAX.
- A registry entry and a module attribute naming one object drift. `trade_feeds` captured
  `trade_feed` at import, so anything replacing the attribute left the route serving the stale
  object - an empty 15 s chart with no error anywhere. The default symbol's entry is now read
  from the attribute.

## 3. selected_symbol as the single authority

Held once, in the page, so it survives the PAPER/LIVE switch for free; persisted in
`localStorage` so it survives a reload; read inside the state initialiser so the first render is
already on the remembered symbol rather than painting BTC for a frame. Every wrapped in
try/catch - a terminal that fails to render because it could not remember a tab is worse than
one that opens on BTC. A stored symbol the server does not permit is discarded.

Two mechanisms keep figures from mixing, and **both** are needed:

1. **State is cleared the instant the symbol changes**, in `useLayoutEffect` so the cleared
   state is what the browser paints. Without it the previous symbol's balance, position, ladder
   and PnL render under the new tab for one poll interval - every number wrong, every label
   right.
2. **Every response is checked against the symbol current when it arrives.** Clearing alone is
   not enough: a request issued before the tab changed can resolve after it. `AbortSignal` does
   not cover it either, because the response may already be past the point abort takes effect.
   The check is on the symbol the response *claims*, so a server that answered about the wrong
   instrument is also caught.

`symbol` is a **required first argument** on every per-symbol API call, not an optional one with
a default - an optional symbol means a forgotten argument silently reads BTCUSDT, which is the
exact failure this change exists to prevent, so the compiler catches it instead. `arm`,
`disarm` and `positions` take none: they are about the account.

## 4. Common wallet, per-symbol Safe MAX

Safe MAX is bounded by **Binance's own `assets[USDT].availableBalance`**, which is already net
of every open position's initial margin on every symbol. A held BTC position therefore shrinks
the ETH ladder without the ETH path knowing anything about BTC, and nothing recomputes a balance
of its own. The ladder is a binary search over that symbol's own step grid against that symbol's
own filters, book and leverage - `sizing.py` was already fully parametric and needed no change.

Proven in tests at two balances: 10,000 USDT vs 100 USDT with a BTC position open gives a
strictly larger ETH MAX, and at 2 USDT only ETHUSDT's smallest order fits while BTC and SOL are
refused with `INSUFFICIENT_MARGIN`.

## 5. Positions

ONE_WAY unchanged; hedge mode is still refused by the account gate. The three positions coexist
and a tab change sends **no write at all** (asserted). The strip above the tabs shows every
symbol including the flat ones - "SOL FLAT" is information somebody acts on and an absent row is
not - from **one** unfiltered `GET /fapi/v3/positionRisk` (weight 5), not three reads that could
return three different instants.

**Beyond the spec:** the strip also lists open positions on symbols this terminal does not
trade. The account is one wallet and the operator can open a position in the Binance app; it
holds margin that every tab's Safe MAX already reflects, so a strip titled OPEN POSITIONS that
hid it would be wrong by omission. Read-only, labelled, no controls attached.

## 6. Orders

`LONG`/`SHORT`/`CLOSE` go to the selected symbol only. Before the quantity is read and before
anything is priced, `LiveOrderRouter._agree_on_symbol` requires five sources to be the same
string: the screen's request, the router's config, the reader, the snapshot, the filters, and
the position Binance just reported. A disagreement is `SYMBOL_MISMATCH`, recorded in the audit
mirror with every symbol it saw, and no order is built.

This should be unreachable in a correct build, which is exactly why it is checked: the
catastrophic failure on a multi-symbol screen is not a rejected order, it is an accepted one
against the wrong instrument, and nothing downstream would notice - `exchangeInfo` for the wrong
symbol yields a plausible step size, `positionRisk` for the wrong symbol yields a plausible flat
position, and Binance fills the result. CLOSE re-reads the position from Binance and **that read
is checked too**, because it is the quantity that actually goes on the wire.

## 7. Filters

All per symbol from `exchangeInfo`, already modelled by `SymbolFilters`: `tickSize`,
`pricePrecision`, `quantityPrecision`, LOT_SIZE and MARKET_LOT_SIZE `stepSize`/`minQty`/`maxQty`,
`MIN_NOTIONAL`/`NOTIONAL`, plus the leverage bracket per symbol. Quantity normalisation and Safe
MAX both read them. A missing filter raises; nothing is defaulted.

**Read-only validation against the live Binance API, 2026-10-04** (public endpoints, no key, no
write):

| | BTCUSDT | ETHUSDT | SOLUSDT |
| --- | --- | --- | --- |
| tickSize | 0.10 | 0.01 | 0.0100 |
| pricePrecision | 2 | 2 | **4** |
| quantityPrecision | 3 | 3 | **2** |
| market stepSize | 0.001 | 0.001 | 0.01 |
| MIN_NOTIONAL | **50** | **20** | **5** |
| market maxQty | 120 | 2,000 | 80,000 |
| smallest orderable | 0.001 (84.9 USDT) | 0.008 (21.6 USDT) | 0.05 (6.0 USDT) |

All three parsed with no missing field. The smallest order differs by **14x in notional**, which
is why a shared grid would have been a real sizing error rather than a cosmetic one.

## 8. Leverage - and the 10/29 restriction question you asked me to settle

Per symbol: own bracket table, own `LeverageCapability`, own file
(`leverage_capability_<SYMBOL>.json`; BTC keeps the original filename). The audit scan that
seeds a capability skips lines naming another symbol, so **no refusal observed on BTC is ever
written into ETH's or SOL's store**. A test asserts it.

**Is the 20x cap symbol-wide or account-wide? Account-wide, by Binance's own words.** The
refusal recorded on 2026-10-01 reads:

> "You can start trading with more than 20x leverage by 2026-10-29 01:34 (UTC), because higher
> leverage is available **30 days after Futures account registration**."

The sentence never mentions BTCUSDT. The reason it gives is the age of the Futures *account*,
which is not a property an instrument has.

So I did **not** copy BTC's capability to ETH/SOL, and I did not ignore the evidence either.
There is a third, separate thing: `account_scope`, derived from the shared audit file on every
`/leverage` request, present only when the refusal's own sentence names the account. It greys
the step on all three symbols, carries `observed_on_symbol` so the screen says where it was
heard, and expires by itself at the instant Binance named (asserted at the millisecond). The
classifier is narrow - both an account noun and a registration/age clause must be present - so
"the symbol does not support 50x" and "position is open" do not qualify. A later success above
the threshold, on any symbol, discards it.

**If you disagree with that reading, the whole behaviour is one function
(`leverage.is_account_scoped`) and removing it leaves the per-symbol stores untouched.**

## 9. Chart

On a symbol change: previous candles dropped, new history seeded, WS subscription changed,
overlays and C1 markers cleared. **Timeframe is preserved.** One `BybitPublicFeed` per symbol,
each with its own socket and book - a shared socket would mean an ETH sequence gap discarding
the BTC book, because the continuity rule is per instrument.

The series key is `symbol|timeframe`, not `timeframe`. With the timeframe alone, switching
instruments on 1m kept the same series and the chart **amended** it with the new prices instead
of replacing them.

## 10. PAPER - and the one thing I did not deliver

PAPER supports manual trading on all three. Each symbol has its own session, run id
(`<base>-<SYMBOL>`), directory, ledger and input tape, so **no file the BTC run owns is opened
by another symbol's session**. Verified: the four existing ledgers and input tapes are
byte-identical (sha256) before and after a preview run that opened and closed real paper
positions on ETH and SOL.

ETHUSDT and SOLUSDT could not inherit BTC's `InstrumentSpec` - their steps differ by up to two
orders of magnitude on Bybit, and a plausible number in `qty_step` is an order the exchange
refuses or one it accepts at a size nobody intended. So I **measured** them the way BTC was
measured: `/v5/market/instruments-info` and `/v5/market/risk-limit` saved raw under
`data/runtime/crypto/<SYMBOL>/reference/`, parsed by `InstrumentSpec.from_file`, with the file
path and its sha256 recorded in the run's own config. Every risk table is re-verified for
maintenance-margin continuity per symbol (all three pass; SOL's first-tier ceiling is 100x where
BTC's and ETH's are 150x).

**NOT DELIVERED: shared PAPER cash across symbols.** `paper.account.Account` owns exactly one
`Position` and derives `available_balance` from it, and the engine's determinism proof is about
that shape. Giving one account three positions is a rewrite of the proven core, and doing it
inside this change would have put new code between the operator and an engine whose behaviour is
fixed by that proof. Your scope said "가능하면", so I took the escape hatch rather than quietly
restructuring it. **Consequence: each PAPER symbol has its own virtual cash, seeded from the
same configured capital.** LIVE is unaffected - there the wallet really is shared and Binance's
own `availableBalance` is the authority. If you want shared paper cash, say so and I will treat
it as its own piece of work with its own determinism argument.

## 11-12. C1/C1x and Market Structure isolation

C1 is a BTCUSDT research result and stays there. On another symbol the signal engine is not
polled at all - no request, not a hidden marker - and `/api/crypto/paper/c1-auto` refuses with
`C1_SINGLE_SYMBOL` rather than answering. Verified in the browser: the AUTO control is present
on BTCUSDT and absent on ETH and SOL.

Market structure and the liquidity map hold by construction: the manual terminal contains no
wall or flow panel at all - they live on their own preview route. A test now asserts that
neither the terminal's four components nor its page nor the backend `terminal` package reaches
for `liquidity-map`, `market_structure`, `wallstate` or `wallrule`, so a future session wiring a
wall ladder into the terminal has to see it fail and decide about the symbol deliberately.

## 13. AUTO

Unchanged. `ExitGuard` still watches one instrument. What changed is that it now **refuses**
rather than answering: asked about a symbol it does not watch it returns `AUTO_SINGLE_SYMBOL`
with the symbol it does watch, and configuring one there is a 409. Answering with its own state
under another tab would show a BTC stop on an ETH screen; configuring one there would arm a
close on a position the operator was not looking at.

## 14. Tests

- **Every test touching the code I changed: 416 passed, 0 failed.** (`test_crypto_multisymbol`,
  all eleven `test_binance_live_*`, the paper engine/sizing/c1-auto/reset-api tests, and
  `test_trade_candles`.)
- **Whole backend `backend/tests/crypto`: 1,989 passed, 0 failed** when measured at 14:5x.
  A later run showed a handful of `test_liquidity_map_*` and `test_ms_v0_*` failures that come
  and go between runs: both packages are **untracked, another session's work in progress**, and
  they were mid-edit while I measured. Nothing I changed is in either package, and a run
  excluding them is green. Worth re-measuring once that session lands.
- **Frontend: 52 files, 835 passed, 0 failed.** TypeScript clean except that same session's
  `liquidity-map-preview` files.
- New: `backend/tests/crypto/test_crypto_multisymbol.py` (35 tests) and
  `frontend/components/crypto-multisymbol.test.tsx` (19 tests), plus cases added to the chart
  tests. The backend file uses a transport that answers **per symbol**, unlike the existing
  fixtures which serve BTCUSDT whatever they are asked - a code path reading the wrong symbol
  gets the wrong instrument back and the assertion fails, rather than passing because every
  answer was the same.
- Four existing tests were updated because the contract deliberately changed (the "V1 refuses
  ETHUSDT" test, three call signatures, one series key). The "V1 refuses" test was replaced by
  one asserting the property it protected: an unlisted symbol is refused at config load rather
  than attempted.

## 15. Isolated Preview, and the three defects it caught

Run locally with no Binance key, trading disabled, isolated paper/live/reference roots. All
three tabs render with their own chart, prices, grid and unit; selection survives a reload;
no horizontal overflow at 1600 px or 390 px; a request trace shows **zero requests about any
other symbol** while a tab is open. A paper order on ETH moved only ETH's position and equity
while BTC and SOL stayed flat, SOL refused an off-grid 0.55 (`QTY_OFF_GRID`, step 0.1) and
accepted 0.5, and BTC refused 0.0005 (`QTY_BELOW_MINIMUM`).

The preview was worth running because it found three things the tests did not:

1. **`ChartSection` never passed the symbol to its two chart hooks**, so both fell back to
   BTCUSDT and **every tab drew BTCUSDT candles** while the header, position and order panel
   were all correct about ETH or SOL. It has no visual signature; it was found by tracing the
   requests the page actually made. Fixed and pinned by a test.
2. **`SYMBOL_REFERENCE_ROOT` was a bare relative path.** The deployed unit runs with
   `WorkingDirectory=/root/usb_runtime/crypto_paper`, not the repository root - which is why
   `CRYPTO_PAPER_ROOT` is absolute in that unit. ETHUSDT and SOLUSDT would have reported "no
   measurement" in production while every test passed locally. Now: explicit
   `CRYPTO_SYMBOL_REFERENCE_ROOT`, then beside the run state, then the repository path, and the
   refusal names every path it tried.
3. **A stale `loadEarlier` closure** could ask for the previous symbol's history. Harmless (the
   response was discarded downstream) but it should not be sent; guarded at entry.

---

## What production would need, when you decide to deploy

Not done, and listed so the decision is informed rather than discovered:

1. The measured reference files for ETHUSDT and SOLUSDT must reach the host, or
   `CRYPTO_SYMBOL_REFERENCE_ROOT` must point at them. Without them those two symbols have PAPER
   unavailable (LIVE is unaffected - it reads Binance directly).
2. The **signed** read-only validation has not been run: `account`, `positionRisk`,
   `symbolConfig`, `commissionRate` and `leverageBracket` for ETHUSDT and SOLUSDT need the key,
   which is only on the host. Everything above is either unit-tested against recorded shapes or
   validated against the live public endpoints.
3. `BINANCE_LIVE_MAX_QTY` is documented in `deploy/crypto/binance_live.env.example` as a ceiling
   "in BTC". It is **not applied** on the order path (only reported in views), and the live
   secret file does not set it - but if it were ever re-enabled it would be coin-denominated and
   meaningless across three instruments: 0.01 is ~850 USDT of BTC, ~27 of ETH and ~1.2 of SOL.
   It should become a notional ceiling or stay off.
4. The account's 20x cap lifts 2026-10-29 01:34 UTC. Until then 50x and 100x are greyed on all
   three symbols with the account-scope reason above.

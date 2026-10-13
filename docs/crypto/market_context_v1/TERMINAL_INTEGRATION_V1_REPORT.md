# Manual Market Context V1 - terminal integration report

    2026-10-05 · local only · 0 commits · 0 pushes · 0 deploys
    contract  mc-display.v1  sha256 25891d6267dcd9e4e0b0e719802e1f95c79a6aa6b1d99676fc637870e169a3b1  (unchanged)
    panel     mc-v1.0
    journal   mc-v1-journal-lock.1  (new)

**Verdict: B. READY_FOR_PRODUCTION_READ_ONLY**, with the three limitations in section I stated as
part of that verdict rather than against it. **Not deployed.** Nothing was pushed and nothing was
committed.

The previous step answered the placement question against a frame of labelled boxes carrying the
terminal's real dimensions. Boxes were honest about what they were and that was also their limit: a
box cannot be pushed by a panel, cannot report a canvas that rounds its width up, and cannot show
what happens when the symbol tabs change underneath. This step renders the **real terminal** - the
same component `/crypto-paper` renders, from the same file - on an isolated route, against live
Binance public data, and measures what the previous report could only reason about.

**The contract did not move.** `mc-display.v1` still hashes to what was recorded and the panel's own
check still reports `agrees: true`. Section 10 describes the tape as append-only and says nothing
about how many processes may write it; adding a single-writer lock closes that hole without
contradicting a sentence of sections 1-12, so there is no amendment and no supersede entry. The
lock's policy is published in the payload (`journal.writer.recovery`) and stated in section E here.

**git.** Two tracked files changed, both by necessity and both behaviour-preserving:
`frontend/app/crypto-paper/page.tsx` (the screen was lifted into a component so a preview could
render the real one; the route is now a wrapper) and `frontend/components/crypto-paper.test.tsx`
(section F - a pre-existing test was placing real orders). Everything else in this step is new and
untracked. The 39 other modified files in the working tree belong to other sessions and were not
touched; `frontend/next-env.d.ts` and `frontend/tsconfig.json` were rewritten by Next's dev server
during the render and restored to the exact bytes they had before this step began.

---

## A. Integration architecture

### One screen, one definition of it

`CryptoPaperPage` was 314 lines of page component. It is now `components/crypto-terminal.tsx`
exporting `CryptoTerminal`, and `app/crypto-paper/page.tsx` is a two-line wrapper that renders it
with no arguments. The component gained exactly one thing:

```tsx
export function CryptoTerminal({ marketContext }: {
  marketContext?: (symbol: string) => ReactNode;
} = {}) { ... }
```

A **function of the symbol**, not a ready-made node. That is the one design decision in this file
worth defending. This terminal has already paid for a symbol mix-up once: a chart hook that was not
passed its symbol drew BTC candles on every tab, and because the header, the position and the ticket
were all correct there was no visual signature at all - it was found by reading a request trace. A
slot that takes a node would let a caller hand an ETH tab a BTC panel. A slot that takes the symbol
cannot.

The deployed route passes nothing, and a test asserts that the tree it renders is **character-for-
character identical** to `<CryptoTerminal marketContext={undefined} />`. A second test asserts that
the production route issues **zero** requests to the context backend - not "renders nothing", which
a CSS-hidden panel would also satisfy while still polling a second service from the trading screen.

### What the panel reads, and what it cannot reach

Unchanged from the preview step, and re-proved by the tests that already enforced it: its own ASGI
app on its own port (8012), imported by nothing that deploys, an import graph that cannot reach
`paper`, `live`, `terminal`, `strategy`, `backtest` or any C1 module, a closed list of six
environment keys with no credential in it, and one file opened for writing. Section F is the part
that is new.

### Reuse, measured

| Rendered by the preview route | Source |
|---|---|
| source switch, symbol tabs, volatility badges, open-position strip | `crypto-symbol-tabs.tsx`, `crypto-live-terminal.tsx` - unmodified |
| market header, chart, timeframes, position card, position strip, disclosures | `crypto-terminal-layout.tsx` - unmodified |
| order ticket, leverage, quick size, ledger, trades, performance | `crypto-paper-terminal.tsx` - unmodified |
| C1 AUTO control | `crypto-c1-auto-control.tsx` - unmodified |
| MARKET CONTEXT | `market-context-panel.tsx` |

No component was copied, re-implemented or stubbed. The preview is the terminal plus a banner.

---

## B. Terminal layout

**PC: the chart's column, directly under the chart.** The right column is the order ticket's fixed
360px, and nothing added to the left column can move it. The panel sits after `ChartSection` and
before the wide-layout position panel.

Measured on the **visible** ticket. The terminal renders the ticket twice by design - a phone tree
and a wide tree, each simple - so the first `order-ticket` in the DOM at xl is the `xl:hidden` copy
whose rect is all zeros. The first pass of this measurement read that one and produced numbers that
moved with the scroll position; they were discarded.

| Width | Route | ticket top | LONG button top | ticket width | panel top | page height |
|---|---|---|---|---|---|---|
| 1600 | `/crypto-paper` | 419 | 790 | 360 | - | 1,724 |
| 1600 | preview | 522 | 893 | 360 | 1,128 | 3,737 |
| 1440 | `/crypto-paper` | 419 | 790 | 360 | - | 1,705 |
| 1440 | preview | 522 | 893 | 360 | 1,147 | 3,805 |
| 390 | `/crypto-paper` | 997 | 1,335 | 358 | - | 1,812 |
| 390 | preview | 1,254 | 1,592 | 334 | 1,903 | 2,208 |

The ticket is 103px lower on the preview at wide widths and 257px lower at 390px. **All of that is
the preview banner**, which is 75px wide / 147px tall-at-390 plus the preview page's own `px-3 py-4`
- and the banner does not ship. The panel itself is below the ticket at 390 and below the chart at
xl, so in neither case is it between the ticket and the top of the page.

**The number that actually settles it** is the open/close comparison, made inside one page load with
nothing else touched between the two reads (across two loads the figures drift a few pixels for
reasons unrelated to the panel - a HOT badge moving to a different tab, a price string gaining a
digit):

| Width | ticket top, as loaded | ticket top, after toggle | ticket moved | LONG moved | panel height |
|---|---|---|---|---|---|
| 1600 | 522 | 522 | **0 px** | **0 px** | 1,942 → 91 |
| 1440 | 522 | 522 | **0 px** | **0 px** | 1,991 → 91 |
| 390 | 1,273 | 1,273 | **0 px** | **0 px** | 111 → 988 |

Opening or closing the context panel moves the order ticket and the LONG button by zero pixels at
every width tested.

**Horizontal overflow is 0px** at 1600, 1440 and 390, in every state captured: default, expanded,
collapsed, and on each of BTCUSDT, ETHUSDT and SOLUSDT - 18 measured renders, `scrollWidth -
clientWidth == 0` in all of them. The probe also walked every element in the document looking for
one whose right edge exceeded the client width and found none.

**Layer priority** is the contract's own order, unchanged: PRICE, LIQUIDITY, FLOW, DERIVATIVES,
AUXILIARY.

**Everything else is where it was.** Symbol tabs, chart, position, order ticket, Safe MAX / quick
size, PnL and the four disclosures all render and behave as before; the regression tests in section H
pin the block list and the ticket's container class.

---

## C. Mobile behaviour, 390px

The panel opens **collapsed**, as a single header:

```
MARKET CONTEXT   BTCUSDT · 읽기 전용                      [펼치기]
PRICE · LIQUIDITY · FLOW
PRICE [LIVE]  LIQUIDITY [LIVE]  FLOW [LIVE]  DERIVATIVES [LIVE]  AUXILIARY [STALE]
```

111px tall. **Collapsing hides figures and never hides freshness** - all five layers' state words
are on screen collapsed, so a shut panel can never be read as "everything is fine".

Why a whole-panel collapse and not the per-layer `compact` the preview step shipped: compact still
leaves a header, a state strip, the no-rollup sentence, an open PRICE section and a footer, which
measured **470px** at 390 - 470px of scrolling between the ticket and the next thing. Shut, it is
111px.

Expanded at 390 the panel is 988px, the page grows 2,227 → 3,104, and the ticket does not move,
because the panel is **below** the ticket on a phone. That position is not a preference. Measured in
the preview step: with the panel between the chart and the ticket at 390px, the ticket started
1,736px down the page with four layers already collapsed.

The default follows the viewport (`max-width: 1279px`) rather than a prop, so a caller cannot forget
it; once the operator presses 펼치기 or 접기 their choice wins for that mount. PC opens expanded.

Sticky/order behaviour is untouched - the panel is rendered in exactly one of the two trees, gated
on the same `useIsWide()` the ticket's preview gating already uses, so one panel is mounted and one
poll loop runs.

---

## D. Symbol isolation

**BTCUSDT** shows all five layers. Measured live during the QA run: `PRICE LIVE · LIQUIDITY LIVE ·
FLOW LIVE · DERIVATIVES LIVE · AUXILIARY STALE` (C1 store older than five minutes - the engine is
not running locally, which is the correct reading and not a defect).

**ETHUSDT / SOLUSDT** show `PRICE UNAVAILABLE · LIQUIDITY UNAVAILABLE · FLOW UNAVAILABLE ·
DERIVATIVES LIVE · AUXILIARY UNAVAILABLE`, each with its reason - `LEVEL_BOOK_NOT_CALIBRATED`,
`COLLECTOR_IS_BTC_ONLY`, `RESEARCH_IS_BTC_ONLY`. DERIVATIVES carries that symbol's own figures: the
ETH screenshot shows OI 2,327,122.795 and mark 2,703.1, which are ETH's and appear nowhere in the
BTC payload.

**One defect was found by rendering ETH and would not have been found by reading the code.** The
panel opened "the first three layers in contract order" on a wide screen. On ETH that opened PRICE,
LIQUIDITY and FLOW - three sections that each say only "not available for this symbol" - and left
DERIVATIVES, the one layer that *is* ETH's, shut. The panel had nothing on it. The rule is now "the
first openable layers", where openable means the state is not `UNAVAILABLE`. On BTC this reproduces
the previous behaviour exactly; on ETH and SOL it opens DERIVATIVES.

**No BTC figure reaches a non-BTC screen.** Three mechanisms, and they are deliberately redundant
because this failure has no visual signature:

1. the backend's `SYMBOL_SUPPORT` matrix refuses the BTC-only layers per symbol;
2. the panel's poll effect is keyed on the symbol and **clears its payload** on a key change, so
   there is no window in which the previous symbol's figures are on screen under the new label;
3. the integration renders the panel with `key={symbol}`, so a tab change unmounts it outright.

Checked by taking the BTC-only figures **off the captured BTC fixture** rather than typing them into
the test - a hard-coded number the fixture no longer contains is a test that passes by looking for
nothing - and asserting none of them appears in the ETH or SOL render.

**BTC → ETH → SOL → BTC**, and fast switching: four tab presses with every context answer held open,
asserting that the panel is in its loading state with **no** `data-symbol` attribute and no
liquidity section, rather than showing the last symbol it had. Then every answer is released and the
panel settles on the tab that is actually selected. The same sequence was walked in the browser at
all three widths; the screenshots are in `preview/terminal/`.

---

## E. Journal writer lock

### What it is for, measured rather than assumed

Two processes appending to one `context-YYYYMMDD.jsonl` do not tear the file - `O_APPEND` writes
under a page stay parseable. What they destroy is the thing the tape is collected for. The cadence
rule lives in each writer's own `cursors`, so two writers produce two interleaved cadences, a
duplicate for most minutes, and `written_because` values that describe neither.

That damage is the first test in the suite, run with authority bypassed: four minutes, two writers,
**eight records**, two of them `FIRST_RECORD`, and only four distinct payloads - the duplicates are
indistinguishable from real records and **no reader can repair the file afterwards**. Every other
expectation in the file is a comparison against that number rather than against an intuition.

### The policy

* **Scope: the root.** One advisory `flock` on `<root>/.writer.lock`, held for the life of the
  process. The root is the unit because a day file is only the current day's name for it.
* **One writer. The second fails closed** - it writes nothing, it does not retry, and it does not
  advance its own cursor (a writer that believed it had written would skip the next real line as a
  duplicate the moment it got the lock back).
* **The lock is a precondition of `append`, not of `start`.** `start()` exists so a second preview
  is refused at boot rather than at the first closed minute, but a caller that skips it still cannot
  write - a test pins that.
* **Holder identity is recorded**: pid, session id, start time, lock version, written as one JSON
  line at offset 0. Nothing in this package truncates, so a shorter payload can leave a previous
  holder's tail behind; the reader takes the first line only and ignores the rest rather than
  cleaning up a file it may not own.
* **Release.** `stop()` on an orderly shutdown, and the kernel on anything else.
* **Stale lock recovery: there is none, and that is the rule.** `flock` lives on the open file
  description, so the kernel drops it when the holder exits for any reason - clean stop, crash,
  SIGKILL - and the next writer acquires immediately. There is therefore no stale-lock timeout, no
  "lock older than N" heuristic, and **no path that removes another process's lock file**. A lock
  file naming a dead pid is a hint, and it is overwritten in place by whoever next acquires.
* **The inode hole, which the lock cannot close by itself.** `flock` locks an inode, not a path.
  Delete the lock file under a live holder and the next `open(O_CREAT)` makes a different inode that
  locks cleanly, and both processes then believe they are alone. So the holder re-checks the inode
  identity **before every append** and stops permanently when it can no longer prove it owns the
  root. The successor can then take the root, so exactly one cadence continues rather than two.
* **Journal failure never blocks the screen.** Losing the lock stops the writing and nothing else.
  No exception leaves the serving path, every GET still answers, every layer still renders, and the
  loss is reported as `journal.status` - one word off a closed list of seven: `OFF`, `WAITING`,
  `WRITING`, `STALE`, `ERROR`, `REFUSED`, `AUTHORITY_LOST`. `STALE` is a display threshold at 180s
  and **never gates a write**; a tape that stopped writing because it looked stale would turn a
  reporting question into data loss.

### What was taken from the V0 collector's lock, and what was left there

Taken, because they are properties of `flock` and not of that collector: remembering the locked
inode and re-checking it; an identifying payload so a refused writer can name the holder; and one
retry in `acquire`, because between `open` and `flock` the file can be replaced and a lock on an
inode nothing can reach is worse than no lock - it reads as success.

Left behind deliberately: orphan `.open` recovery (this tape has no half-written files; one append,
flushed and fsynced, is the whole unit of work), the compact state file and the
`live_writer_evidence` heuristic over a published pid, session bookkeeping, and - the important one
- **raising out of the serving path**. The V0 collector exists to write, so losing authority stops
the process. This writer rides on a read-only panel, so losing authority stops the writing.

### Live evidence

Against the preview process actually serving this QA (pid 81825), a second writer started in a
separate real process on the same root:

```
status            : REFUSED
writable          : False
holder            : {'lock_version': 'mc-v1-journal-lock.1', 'pid': 81825,
                     'record': 'MC_V1_FORWARD_CONTEXT',
                     'session_id': '81825-1791175823682', 'started_ms': 1791175823682}
my pid            : 82118
append returned   : False
blocked_writes    : 1
records_written   : 0
```

The tape contained only the first process's lines. The holder's own view through the API:
`status WRITING · lock held True · pid 81825 · inode 915379`.

Over the QA run the tape wrote **34 records** (BTC 30 / ETH 2 / SOL 2), skipped **964** as unchanged,
and blocked **0**. `written_because`: `CONTEXT_CHANGE` 18, `NEW_MINUTE_BAR_CLOSE` 11, `FIRST_RECORD`
3, `NEW_MINUTE_WALL_CLOCK_MINUTE` 2 - the last two are the ETH and SOL rows, which have no structure
layer and so fall back to the wall clock, exactly as designed. Liquidity states on the tape: LIVE 21,
STALE 9, UNAVAILABLE 4 - **the gaps were written, not skipped**, which is the property the whole
schema exists for.

Crash recovery is exercised against a real kernel rather than a mock: a holder is spawned as a
subprocess, a second writer confirms `REFUSED`, the holder is killed with SIGKILL (no handler runs,
no `stop()` is called), the lock file is still on disk still naming the dead session - and the next
writer acquires and appends. Same test with SIGTERM.

---

## F. Read-only / mutation proof

### Static

Unchanged and still enforced: no module imports `crypto.paper`, `crypto.live`, `crypto.terminal`,
`app.strategy`, `app.services` or any C1 module; a subprocess import of the API proves none of them
loads; the internal dependency list is closed to the six layer modules the contract names; no
credential token, no private venue path, no signing primitive; every route is a GET.

Two scans were **tightened** by this step rather than loosened:

* The `mkdir` check counted occurrences of the word in `journal.py`'s raw source. The writer lock
  gave that file three sentences explaining why there is only one directory creation, at which point
  the scan would have failed on its own documentation and the convenient fix would have looked like
  weakening it. It now counts `.mkdir(...)` **calls off the AST** and asserts the list is exactly
  `[("journal.py", "self.root")]` - which also catches a `mkdir` reached through an alias.
* `open()` is not the only way to write a file, and the lock has to use the descriptor-level calls.
  A new scan pins every `os.open`/`os.write`/`os.lseek`/`os.truncate`/`os.link`-family call in the
  package to `journal.py`, pins every `os.open` target to `self.path` (the lock file), and pins the
  flags to `os.O_RDWR | os.O_CREAT` off the AST - no `O_TRUNC`, because truncating a lock file is
  how a live holder's payload gets destroyed by a process that was about to be refused.

  That flag check was first written as a raw-text ban on `O_TRUNC` and `O_APPEND` and failed
  immediately - on this feature's own docstring, which uses the word `O_APPEND` to explain why two
  appenders do not tear the file. Same lesson as the `CONSUMED` literal scan two sections away: a
  ban belongs on the thing that executes, and a ban that fires on documentation is one that gets
  deleted rather than obeyed.

### Runtime: the sentinel

The terminal rendered on the preview route is the real one, wired to the real order callbacks -
callbacks that were cut out would no longer be the thing under test. So "no order was sent during
QA" cannot be a statement about how carefully the QA was clicked. The preview route installs a
`fetch` sentinel that **refuses and counts every non-GET** before it leaves the browser, and the
terminal is not mounted until it is in place. That ordering is structural, not hopeful: child
effects run before parent effects, so a terminal mounted beside the sentinel could have fired a
write through the unpatched `fetch`.

The sentinel is also the most direct available evidence that the buttons are still wired to what
they were wired to - a click that produces a refusal reached `cryptoApi.order`.

### The run

Every enabled order control on the screen was pressed - LONG, SHORT, CLOSE - and leverage buttons
were attempted:

```
pressed                         : ['long-button', 'short-button', 'close-button']
requests_by_method              : {'GET': 120}
non_get_attempts_seen_by_browser: []
page sentinel                   : gets 108 · blocked 3 · last "POST .../api/crypto/order"
order ticket error              : "NETWORK_ERROR · Paper Terminal API에 연결할 수 없습니다." ×2
backend account state unchanged : True
```

Two independent witnesses: the browser's own request log (0 non-GET left the page) and the paper
backend's account state before and after (`ledger_event_count`, `position_side`, `position_qty`,
`position_signed_qty`, `avg_entry`, `realized_pnl`, `cumulative_fees`, `cumulative_funding_paid`,
`used_margin`, `reset_count`, `mode`, `leverage` - identical on all three symbols).

`equity`, `unrealized_pnl` and `input_record_count` are deliberately **not** in that invariant set.
The first two move with the mark price whenever a position is open and the third counts public
quotes arriving on the feed; including them made the probe report a change twice for reasons nothing
on the screen caused, which is worth recording because it is how a mutation proof quietly becomes
unfalsifiable in the other direction.

Across the separate screenshot run - 18 page loads, three widths, three symbols, 523 GET requests -
the browser recorded **0 non-GET requests**.

### A real defect found on the way

The paper backend's ledger had five events in it and a 0.008 BTC LONG position that nothing in this
work had opened. It was filled at 13:56:00, which is inside the window in which `npx vitest run` was
running and before any browser started.

`components/crypto-paper.test.tsx` had a test that rendered the order ticket, clicked LONG, and
invoked the dispatched action for real with `fetch` unstubbed and the rejection swallowed by
`.catch(() => {})`. On a machine with nothing on port 8100 that is harmless. On a machine with a
paper terminal running there - the ordinary state of a machine being used to look at this screen -
**running the frontend test suite opens a position on it**.

Fixed the way the sibling test for the mobile card's CLOSE already does it: `vi.spyOn(cryptoApi,
"order")` with a mock, which also lets the test assert the request it sends rather than only that
something was dispatched. Two other candidates were checked and are safe (`crypto-v26.test.tsx`
already mocks; `crypto-chart.test.tsx` never invokes).

Verified directly rather than by inspection: ledger, position, quantity and cumulative fees on all
three symbols recorded, the full 1,047-test suite run against the live backend, and the same figures
read again - **identical**. Before the fix the same suite added four ledger events and a position.

### What Market Context itself can reach

| | |
|---|---|
| Can reach | GET on its own app (8012); the five layer contracts, read-only; the append-only context journal |
| Cannot reach | order, CLOSE, leverage, arm, AUTO, account write, any credential, any private venue path, any C1 module |

Two further containments for the preview specifically: the terminal backend it talks to was started
with **no exchange credential**, so `credentials_present: false` and the BINANCE LIVE tree is
unreachable from the route at all; and its runtime root is a scratch directory, not the repository's
paper data.

---

## G. Preview screenshots

Route: `/crypto-terminal-preview` (not linked from anywhere, not in the navigation, imported by no
deployed page). Rendered against live Binance USDⓈ-M public data with a Market Structure V0 collector
running. Files in `preview/terminal/`:

| File | What it shows |
|---|---|
| `terminal-1600-default.png`, `terminal-1440-default.png` | PC: all five layers under the chart, ticket unmoved on the right |
| `terminal-1600-toggled.png`, `terminal-1440-toggled.png` | PC collapsed to the one-line header |
| `terminal-390-default.png` | phone: collapsed header below the order ticket |
| `terminal-390-toggled.png` | phone expanded, ticket still above it |
| `terminal-{1600,1440,390}-ETHUSDT.png`, `-SOLUSDT.png` | BTC-only layers `UNAVAILABLE`, DERIVATIVES live with that symbol's own figures |
| `terminal-{1600,1440,390}-BTCUSDT.png` | the return leg of BTC → ETH → SOL → BTC |
| `production-{1600,1440,390}.png` | `/crypto-paper` at the same widths, with no panel |
| `mutation-after-clicks.png` | after LONG, SHORT and CLOSE were pressed |
| `measure-widths.json`, `measure-toggle.json`, `measure-symbols.json`, `mutation-probe.json` | the measurements quoted above |

States captured on real data: PRICE live with `trend NEUTRAL`, a published support at 85,390.3 and
**no published resistance** (`NO_LEVEL_ABOVE`), `breakout UP` age 491m, `retest HELD`; walls present
on both sides (ASK 85,564.1 / 523,138 USDT / 4.92bp, BID 85,481.8 / 586,234 USDT / 4.71bp) with
`±0.1% COMPLETE` and the three wider bands `PARTIAL ≥`; 60s flow 3.99M buy / 7.02M sell; vanished
walls `CANCEL_LIKE 41 · CONSUMED_CANDIDATE 1 · UNKNOWN 3` shown beside R0's base rates; a live
`ABSORPTION_CANDIDATE` with `order_identity_proven: false`; DERIVATIVES live on all three symbols;
AUXILIARY `STALE` on BTC and `UNAVAILABLE` off it.

Not captured: `PARTIAL` as a wall-set state and an `AUXILIARY LIVE` path - section I.

---

## H. Tests

| Suite | Result |
|---|---|
| `backend/tests/crypto/test_market_context_v1_*.py` | **212 passed** (186 before, +24 writer lock, +2 isolation) |
| `backend/tests/crypto` (whole directory) | **2,574 passed**, 0 failed, 0 errors |
| frontend, full | **1,047 passed / 58 files** |
| `crypto-terminal-integration.test.tsx` (new) | 33 |
| `tsc --noEmit` | clean |
| `eslint` on every file this step touched | clean |

New backend file `test_market_context_v1_writer_lock.py` (24): the two-writer damage baseline;
collision and refusal; refusal names the holder; lock-file payload rewritten in place without
truncating; unparseable lock file read as a hint; SIGKILL and SIGTERM crash recovery against real
subprocesses; no self-recovery while the holder lives; orderly release; idempotent stop; the inode
hole and permanent fail-closed; a replaced lock file refused rather than held; the seven-state
closed list; a root that cannot be created; the stale threshold never gating a write; construction
with no side effects; the recovery rule published rather than left to a runbook; the panel
assembling every layer while refused; every route still answering; the lifespan not starting the
timer without authority and releasing on shutdown.

New frontend file (33): deployed-route regression including the character-identical tree and zero
context requests; panel placement at 1600/1440/390 and the ticket's container class unchanged; one
mounted panel; mobile collapsed default with the three layer names, the 펼치기 control and all five
state chips; expansion keeping the ticket above; wide default expanded; overflow and single-column
structure; BTC full context; ETH and SOL isolation and the no-BTC-figure sweep; the four-stop symbol
walk; fast switching with answers held; stale liquidity and flow; no qualifying wall; context
backend down; journal `REFUSED`, `AUTHORITY_LOST`, `STALE`, `ERROR`; polling continuing while the
tape is stopped; the sentinel counting and refusing; a pressed LONG refused with the callback intact;
the terminal not mounting before the sentinel; `fetch` restored on unmount.

**Fixtures are recorded, not written.** Hand-written payloads were tried first and were wrong in
three places that all type-checked - `vanished.this_reading` where the layer also publishes
`vanished.recent`, `ts_ms` where a chart bar carries `start_ms` and `confirmed`, and a `sides` block
with no depth bands - and every one of them rendered something plausible. The fixtures are now the
recorded responses of the two processes the screen actually talks to, captured during this run.

---

## I. Known limitations

1. **The wall-set `PARTIAL` state still has no screenshot.** It is covered by tests and was not
   produced by the live book during this run. A state nobody can force is a state that cannot be
   photographed on demand; it is named here rather than quietly omitted.
2. **`AUXILIARY LIVE` was not captured.** The C1 engine is not running locally, so the layer read
   `STALE` throughout - which is the correct reading of a store older than five minutes, and leaves
   the live path unphotographed.
3. **The 390px chart canvas renders empty in headless Chrome.** Visible in both the preview *and*
   `production-390.png`, so it is the capture environment and not this change. Worth knowing before
   anyone reads the phone screenshots as a chart regression.
4. **The forward tape's 24h behaviour is still unmeasured**, and so is the Liquidity Map's - the
   same blocker the preview report listed. This step did not change it.
5. **The journal lock is single-host.** `flock` protects one filesystem's inode. Two machines
   writing one NFS root is outside what this closes, and nothing in the current deployment does
   that.
6. **The preview QA ran against PAPER only.** The BINANCE LIVE tree was unreachable by construction
   (no credential), which is the right containment for a preview and does mean the panel has not
   been rendered beside the live account's tree outside of tests.
7. **The absorption and `CANCEL_LIKE` rules are still unvalidated by outcome.** Unchanged from the
   preview report, restated because they are now on a screen that looks operational.
8. **No chart overlay.** Levels and walls are not drawn on the candles; that needs a change to the
   chart component itself.

---

## J. Production readiness

**B. READY_FOR_PRODUCTION_READ_ONLY.**

| Condition | Result |
|---|---|
| renders correctly in the real terminal layout | yes - real components, 1600 / 1440, ticket moved 0px |
| 390px correct | yes - collapsed default, 0px overflow, ticket moved 0px, panel below the ticket |
| writer lock PASS | yes - fail closed, live refusal against the serving process, SIGKILL recovery, inode hole closed |
| BTC / ETH / SOL isolation PASS | yes - three independent mechanisms, no BTC figure off BTC, fast-switch window checked |
| stale / unknown fail closed | yes - a dash and a reason, never a zero; no layer borrows another's freshness |
| mutation 0 | yes - 0 non-GET over 643 requests across two runs, backend account state identical, order buttons pressed |
| tests pass | yes - 212 backend MC V1, 2,574 backend crypto, 1,047 frontend, tsc and eslint clean |

**Not deployed, and that is the instruction, not an omission.** No commit, no push, no production
route change beyond the behaviour-preserving extraction.

What a deployment would need beyond this document: `MC_V1_JOURNAL_ROOT` chosen on the host and
owned by one unit; the Market Structure V0 collector running, since LIQUIDITY and FLOW are
`UNAVAILABLE` without it; and a decision about the preview banner and the sentinel, both of which
are preview furniture and neither of which belongs on a deployed screen - the sentinel in particular
would refuse the operator's own orders.

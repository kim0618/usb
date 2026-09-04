# Stage 10B-3.4 — Minute Pagination Depth Fix

Implemented on 2026-09-04 as a bounded backend-only correction.

- `usa06011` history no longer silently stops at the generic 10-page limit.
- Minute collection stops when continuation ends or the oldest canonical
  `America/New_York` timestamp reaches the aware requested start boundary.
- `MAX_MINUTE_HISTORY_PAGES = 50` remains a hard safety cap (up to roughly
  5,000 observed 100-row pages), preventing unbounded continuation.
- Cap + continuation + unreached target raises normalized
  `INSUFFICIENT_HISTORY`; `MarketContextService` preserves that reason instead
  of returning `NOT_AVAILABLE_FROM_PROVIDER`.
- ScannerRun snapshot `latest_close` is used as the same-day postmarket return
  base when the fetched minute range has no regular bar.
- Mock verification covers continuation, exact boundary, natural completion,
  hard-cap truncation, postmarket data beyond page 10, latest-bar selection,
  and return-base correctness.
- Bounded TSLA live verification for 2026-09-03: 12 pages, target reached,
  continuation remaining, zero matching canonical 04:00-20:00 ET bars. This
  proves the 10-page truncation is removed but does not prove historical TSLA
  postmarket availability for that date. Premarket remains unconfirmed.
- No frontend, migration, operator DB write, scanner, simulation, order path,
  or server restart was involved.

---

# Stage 10B-3.3 — Extended Session History & KST Display Polish

Implementation completed on 2026-09-04 as a bounded follow-up to Stage
10B-3.2. The market-context composer now selects the latest available
premarket and postmarket bars for the analysis's unchanged US `trading_date`,
regardless of the current display session. Evidence source timestamps use the
shared KST-primary formatter. No migration, research schema, execution-session
policy, provider endpoint, operator DB write, or process restart was added.

The remainder of this document records the Stage 10B-3.2 audit and implemented
foundation on which this follow-up depends.

Implementation completed on 2026-09-04 after revalidating this audit against
the working tree. Application code, deterministic tests, and relevant docs were
updated; no migration, `.env`, runtime database, frozen execution policy,
research schema, or HumanDecision contract was changed. A bounded AAPL live
check was performed without printing credentials, tokens, or raw payloads.

Companion documents: `docs/ai/CURRENT_STATE.md`, `docs/ai/FROZEN_CONTRACTS.md`,
`docs/ai/SAFETY_RULES.md`.

---

## Verdict

- **Metadata (company/exchange):** AVAILABLE UPSTREAM, LOST DOWNSTREAM.
  Kiwoom's ranking TRs (`usa20540`/`usa20550`) already carry `stk_enm`/`stk_nm`
  (company name) and `stex_tp` (exchange), and `SymbolMetadata` already has
  both fields. The value is read into memory at scan time and then dropped
  when `RankedCandidate` is built. **KIWOOM SUFFICIENT** for these two fields
  — no new external call needed, only a plumbing fix.
- **Industry/sector:** NOT AVAILABLE FROM CURRENT CONTRACT. No field exists
  anywhere in the domain model, Kiwoom mapper, or DB. `usa10100` is
  allowlisted but never called outside the manual smoke script, and no raw
  field name for industry has ever been observed live. **KIWOOM PARTIAL** —
  plausible per `docs/REAL_MARKET_SCANNER.md:88-90` ("`usa10100` provides
  symbol/name/exchange/industry/ETF identity"), but unconfirmed until a live
  call is made and the exact field is inspected.
- **Market cap:** structurally PRESENT end-to-end (already reaches the
  frontend today, unit conversion is a no-op everywhere), but the **unit is
  unconfirmed** and the specific NVDA figure is implausible for a real
  company of that name (see Market Cap section). Classification: AMBIGUOUS.
- **Daily OHLC:** `open`/`high`/`low` are AVAILABLE UPSTREAM, LOST DOWNSTREAM
  at the exact same code location as company_name/exchange
  (`backend/app/scanner/scanner.py:213-231`). `previous_close`/`previous_volume`
  are PRESENT and already correct. `previous_return_pct` is NOT CURRENTLY
  FETCHED but is trivially derivable from data already fetched during the
  scan (no new Kiwoom call needed at scan time).
- **Premarket/Postmarket:** NOT AVAILABLE FROM CURRENT CONTRACT. No quote
  field carries an extended-hours price. Minute bars (`usa06011`) are
  structurally capable of carrying a proxy last-traded price for
  premarket/postmarket (mapping already tags `MinuteBar.session`), but
  Kiwoom's actual extended-hours minute coverage is explicitly documented as
  unconfirmed. Classification: DERIVABLE FROM MINUTE, pending live
  confirmation.
- **Header time policy:** ET-only today (`etTime()` is the only timestamp
  formatter in the frontend; no KST formatter exists anywhere). KST-primary
  display is a pure frontend addition — no internal/backend timezone logic
  needs to change.
- **Session badge bug root cause:** Classification **(E) intended policy,
  applied to a role it wasn't designed for.** `session_policy.py`'s
  `POSTMARKET` state has no upper time boundary by design (documented,
  frozen, execution-permissioning intent) — and, symmetrically, `PREMARKET`
  has no lower boundary either. This same open-ended shape is independently
  re-implemented (not shared code) in three places: the frozen execution
  guard, the Header's `/api/v1/market/status`, and the Kiwoom minute-bar
  mapper. The Header bug is **not** a timezone bug, **not** a stale-cache bug,
  and **not** literally the same code path as the frozen execution guard —
  those are two separate, non-overlapping implementations that happen to
  share the same (intentional, for execution) unbounded-postmarket shape.
  Fixing the Header's display is safe and does **not** touch
  `backend/app/strategy/session_policy.py` or `backend/app/strategy/runner.py`.
- **DB migration requirement:** **NONE.** Every field this stage needs
  (company_name, exchange, previous_open/high/low/return_pct) can be added to
  the existing `scanner_candidates.score_components_json` JSON column with no
  schema change. Premarket/postmarket are proposed as pure runtime
  composition (not persisted), so they need no migration either. Industry, if
  pursued, also fits inside the same JSON column.
- **Codex implementation readiness:** Ready for a single bounded stage. The
  highest-uncertainty item (industry field name, extended-hours minute
  coverage) requires one supervised live smoke before or during
  implementation; everything else is derivable from code already read in this
  audit.

---

## Root Causes

One line per missing/wrong field, pointing at the exact fix location:

| Field | Root cause | Fix location |
| --- | --- | --- |
| company_name | Read into `SymbolMetadata` at scan time, then dropped when `RankedCandidate` is constructed | `backend/app/scanner/scanner.py:213-231` (`_normalize_and_rank`) |
| exchange | Same drop point as company_name; also never added to the API's `candidate_dict()` | `backend/app/scanner/scanner.py:213-231`, `backend/app/api/service.py:32-39` |
| industry | No field exists in `SymbolMetadata`, Kiwoom mapper, or DB; source TR (`usa10100`) never called outside smoke.py | `backend/app/market/reference.py:11-20`, `backend/app/integrations/kiwoom/mapping.py`, new call site |
| market_cap | Not a code bug — `mac` is carried raw with zero multiplier anywhere; the **unit itself is unconfirmed by Kiwoom's public contract** | `backend/app/integrations/kiwoom/mapping.py:21-26`, `frontend/lib/format.ts:24-26` |
| previous_open/high/low | `DailyBar` has all three; only `latest.close`/`latest.volume` are extracted when building `RankedCandidate` | `backend/app/scanner/scanner.py:213-231` |
| previous_return_pct | Never computed anywhere in the codebase; the two closes it needs (`item.bars[-1].close`, `item.bars[-2].close`) are already in memory at the same line that currently only reads `latest.close` | `backend/app/scanner/scanner.py:213-231` |
| premarket / postmarket | No field, no fetch, no wiring anywhere — the Drawer hardcodes the "미제공" string as a literal tuple, not a fallback for a null API value | `frontend/app/research/page.tsx:57` (symptom); root fix is a new backend composition path |
| Header session badge at 21:43 ET | `POSTMARKET` has no upper time bound in any of the three independent session-derivation implementations — this is documented, intentional execution-permissioning behavior reused, unmodified, for a human-facing display badge | `backend/app/api/router.py:49-61` (`market_status()`), mirrored in `backend/app/strategy/session_policy.py:32-43` (frozen, do not touch) and `backend/app/integrations/kiwoom/mapping.py:53-58` |
| Header time is ET-only | No KST formatter exists in the frontend at all (`grep` for `Asia/Seoul`/`KST` returns zero hits) | `frontend/lib/format.ts:17` (only `etTime` exists) |

---

## Current Data Flow

```text
Kiwoom usa20540 (transaction-amount rank) ─┐
Kiwoom usa20550 (market-cap rank)         ─┼─▶ KiwoomUniverseSource.acquire()
                                            │     (universe.py:27-54)
                                            │     company_name + exchange + market_cap
                                            │     ARE read here (stk_enm/stk_nm, stex_tp, mac)
                                            ▼
                                   prime_provider() (universe.py:56-67)
                                   builds SymbolMetadata in-memory, no HTTP call
                                            │
                                            ▼
Kiwoom usa06012 (daily OHLCV, 21+ bars) ──▶ KiwoomMarketDataProvider.get_daily_bars
                                            → DailyBar{open,high,low,close,volume} (all present)
                                            │
                                            ▼
                              QuantScanner._evaluate_symbol / _normalize_and_rank
                              (scanner.py:127-232)
                              item.metadata (company_name, exchange) ─┐
                              item.bars[-1] (open,high,low,close)    ─┤  ALL READ HERE
                              item.bars[-2].close (prior close)      ─┘  ALL DISCARDED HERE
                                            │
                                            │   ✂ only market_cap, latest_close,
                                            │     latest_volume survive into
                                            ▼     RankedCandidate (scanner/domain.py:37-51)
                              RankedCandidate.score_components()
                              (scanner/domain.py:53-68)
                                            │
                                            ▼
                              ScannerService.persist_result()
                              (services/scanner.py:66-79)
                              → scanner_candidates.score_components_json
                                {raw, normalized, weighted_contributions,
                                 final_score, market_cap, latest_close,
                                 latest_volume}   ← company_name/exchange/OHLC/
                                                     return% never reach the DB
                                            │
                    ┌───────────────────────┴────────────────────────┐
                    ▼                                                 ▼
   GET /scanner/runs/{id}                          GET /research/{id}/candidates/{symbol}
   api/service.py:32-39 candidate_dict()            api/service.py:68-80 research_candidate()
   reads score_components_json →                    reads GPTCandidateAnalysis + ONLY
   {company_name (always None), quant_score,        quant.rank / quant.score from
    latest_close, latest_volume, market_cap,         ScannerCandidate — market_cap,
    raw_metrics, ...}  (no exchange key coded)       close, volume are NOT even selected
                    │                                                 │
                    └────────────────┬────────────────────────────────┘
                                      ▼
                    Frontend research/page.tsx: openDetail() fetches BOTH
                    endpoints in parallel, merges client-side (by
                    scanner_candidate_id / candidate_id) into `detail` + `quant`
                                      │
                                      ▼
                    Drawer render (page.tsx:56-57):
                    회사명 ← quant.company_name (null today)
                    거래소 ← hardcoded "정보 없음" literal (no field read at all)
                    업종   ← hardcoded "정보 없음" literal (no field read at all)
                    시가총액 ← quant.market_cap via compactUsd() (works, unit unconfirmed)
                    전일 종가/거래량 ← quant.latest_close / latest_volume (works)
                    전일 등락/고가·저가/프리마켓/애프터마켓 ← hardcoded "unavailable" literal
```

Header session/time flow (separate from the above):

```text
GET /api/v1/dashboard → market_status() (router.py:49-61, independent
  re-implementation of session_at, does NOT call session_policy.py)
  → { current_time: UTC-aware, session: PREMARKET|REGULAR|POSTMARKET }
                     │  (CLOSED only ever returned on a non-trading day)
                     ▼
app-shell.tsx:37  etTime(state.data.system_time) + " ET"  (ET only)
                  formatMarketSession(state.data.market.session)  (no CLOSED label)
```

---

## Target Data Flow

```text
Scan time (unchanged trigger, after regular close):
  item.metadata.company_name/exchange  ─┐
  item.bars[-1] (open,high,low,close)  ─┼─▶ RankedCandidate (+4 new fields,
  item.bars[-2].close (prior close)    ─┘    +1 derived previous_return_pct)
                     │
                     ▼
  score_components() emits additional keys:
  company_name, exchange, previous_open, previous_high, previous_low,
  previous_return_pct   (industry key added only if the live usa10100
  smoke confirms a usable field)
                     │
                     ▼
  scanner_candidates.score_components_json  (SAME JSON column, no migration)
                     │
        ┌────────────┴─────────────┐
        ▼                           ▼
  candidate_dict() (api/service.py)   research_candidate() (api/service.py)
  gains: exchange, previous_open/     gains: a merge of the ScannerCandidate
  high/low, previous_return_pct       snapshot fields (company_name, exchange,
  (company_name already coded,        market_cap, previous_open/high/low/
  just needs a non-null value now)    close/volume, previous_return_pct, rvol)
        │                           │  so the Drawer no longer needs a second
        │                           │  scanner-run fetch just to get quant
        │                           │  context (optional simplification;
        │                           │  current 2-fetch pattern also still works)
        ▼                           ▼
  Drawer open (unchanged trigger) → NEW backend composition call:
  GET /research/{id}/candidates/{symbol}?include=market_context (or a
  dedicated sibling endpoint) → MarketContextService
    - premarket: latest PREMARKET-session minute bar (TTL-cached,
      short-circuits outside PREMARKET) vs previous_close (from the
      persisted snapshot, no live fetch needed for the base)
    - postmarket: latest POSTMARKET-session minute bar (TTL-cached) vs
      TODAY's regular close (requires ONE fresh daily-bar fetch for the
      current trading date — NOT the persisted snapshot, which is one day
      stale relative to postmarket)
    - both return NOT_AVAILABLE / OUTSIDE_SESSION / UNIT_UNCONFIRMED style
      typed reasons, never a fabricated number
                     │
                     ▼
  Drawer renders: 회사명, 거래소, 업종(if available), 시가총액(unit-flagged),
  전일 시가/고가/저가/종가/등락률/거래량, RVOL, 프리마켓{가격,등락,관측시각 KST},
  애프터마켓{가격,등락,관측시각 KST} — every cell backed by a real field,
  "데이터 미제공" only when the typed reason says so
```

```text
Header (KST primary, ET internal untouched):
  GET /api/v1/dashboard → market_status() EXTENDED (display-only):
    same MarketCalendar-derived open/close facts, PLUS an explicit
    display-only CLOSED window (operator-approved cutoff — see Session
    Display Plan) — session_policy.py (frozen execution guard) is NOT
    touched, NOT imported, NOT modified.
                     │
                     ▼
  frontend: kstTime(system_time) primary + etTime(system_time) secondary/
  tooltip; formatMarketSession gains CLOSED → "거래 종료"
```

---

## Metadata Plan

### Field-by-field capability table

| Field | Source | Current | Target | Blocker |
| --- | --- | --- | --- | --- |
| company_name | `usa20540`/`usa20550` rows, `stk_enm`/`stk_nm` (`universe.py:49`) | AVAILABLE UPSTREAM / LOST DOWNSTREAM (dropped at `scanner.py:213-231`) | Add to `RankedCandidate`, `score_components()`, already-coded `candidate_dict()["company_name"]` starts returning real data | None — pure plumbing fix |
| exchange | `usa20540`/`usa20550` rows, `stex_tp` (`universe.py:45`) → `canonical_exchange()` (`mapping.py:17-18`) | AVAILABLE UPSTREAM / LOST DOWNSTREAM (same drop point; also missing from `candidate_dict()`) | Same as company_name + add `"exchange"` key to `candidate_dict()` | None |
| industry/sector | `usa10100` (per `docs/REAL_MARKET_SCANNER.md:88-90`, "provides ... industry ... identity") | NOT AVAILABLE FROM CURRENT CONTRACT — no field in `SymbolMetadata`, no raw field name ever observed, TR never called outside `smoke.py` | Add `industry: str | None` to `SymbolMetadata`; call `client.metadata()` (already implemented, currently dead code outside smoke) from `universe.py` or as a `get_metadata()` fallback path; map the confirmed raw field | **Requires one supervised live `usa10100` smoke call** to discover the actual field name before mapping code can be written correctly — do not guess a field name |

### Source priority verdict

**KIWOOM PARTIAL.** Company name and exchange are KIWOOM SUFFICIENT (already
being fetched every scan via the ranking TRs, just discarded). Industry is
unconfirmed — plausible per Kiwoom's own `usa10100` docs, but no field name
has ever been observed in this codebase's live smoke output or fixtures. Do
not introduce a second/external metadata provider for this stage; if the live
`usa10100` smoke shows no usable industry field, industry should render
"정보 없음" honestly rather than fall back to a second source, per the
audit's own no-new-provider constraint.

### GPT text safety check (self-verification item)

Confirmed **not** a risk today: `GPTCandidateResult`/`GPTResearchResult`
(`backend/app/research/domain.py:73-114`) are `extra="forbid"` at every
level. If an operator's pasted ChatGPT JSON contained `company_name` or
`exchange` keys, the entire import would be **rejected**, not silently
absorbed. The only GPT-authored company text that survives is the free-text
`company_summary` field, which is never parsed into structured metadata
anywhere (`grep -rn "company_summary" backend/app/` shows prose storage and
prose display only). This must remain true after this stage — do not add any
company_name/exchange/industry parsing out of `company_summary` or any other
GPT field.

---

## Daily Market Context Plan

| Field | Availability | Classification |
| --- | --- | --- |
| previous_open | `DailyBar.open`, mapped from `open_pric` (`mapping.py:37`) | AVAILABLE UPSTREAM / LOST DOWNSTREAM at `scanner.py:213-231` |
| previous_high | `DailyBar.high`, mapped from `high_pric` (`mapping.py:38`) | Same |
| previous_low | `DailyBar.low`, mapped from `low_pric` (`mapping.py:39`) | Same |
| previous_close | `DailyBar.close` → `RankedCandidate.latest_close` | PRESENT, already correct |
| previous_volume | `DailyBar.volume` → `RankedCandidate.latest_volume` | PRESENT, already correct |
| previous_return_pct | `(bars[-1].close - bars[-2].close) / bars[-2].close`; both closes already in `item.bars` at `scanner.py:213` | NOT CURRENTLY FETCHED, but trivially derivable — the required 21-bar history (`scanner/config.py:54-59`) always yields at least 2 closes for any eligible candidate, so this is never a division-by-missing-data case for a candidate that passed eligibility |
| trading_date alignment | `ScannerRun.trading_date` already persisted and exact (`models/scanner.py:17-43`) | PRESENT — do not let a KST display change this value (see Time Display Plan) |

**Ownership of the return% calculation: backend, at scan time**, inside
`QuantScanner._normalize_and_rank` (`scanner/scanner.py:213-231`), using data
already in memory — not a new fetch, not a frontend computation (frontend
computation would violate `docs/ai/FROZEN_CONTRACTS.md`'s "the frontend never
recomputes a financial value").

Implementation shape (illustrative, not a diff):

```python
latest = item.bars[-1]
prior = item.bars[-2] if len(item.bars) &gt;= 2 else None
previous_return_pct = (
    None if prior is None or prior.close == 0
    else (latest.close - prior.close) / prior.close
)
```

No migration: all five new keys go into the existing
`scanner_candidates.score_components_json` JSON column, the same column that
already carries `market_cap`/`latest_close`/`latest_volume`.

---

## Premarket Plan

- **Source:** `usa20100` (quote) has no extended-hours price field in any
  observed response, comment, or fixture — ruled out as a direct source.
  `usa06011` (minute) is the only structurally plausible source: its mapper
  already tags `MinuteBar.session = PREMARKET` for any bar timestamped before
  09:30 ET (`mapping.py:53-58`), independent of whether Kiwoom's feed
  actually contains such rows for a given symbol/date.
- **Derivation:** DERIVABLE FROM MINUTE, **pending live confirmation.**
  `docs/KIWOOM_INTEGRATION_SPIKE.md:48` and `docs/SESSION_POLICY_AUDIT.md:9,17,49`
  all explicitly flag extended-hours minute coverage as unconfirmed. This
  audit did not and must not make a live call to resolve it — Codex's first
  implementation step should be one supervised, opt-in minute-chart smoke
  call during premarket hours to confirm rows exist before building the
  production path. If they don't exist, ship `NOT_AVAILABLE` (typed reason),
  not a fabricated value.
- **Return base:** `latest_premarket_price` vs **previous regular close**
  (`(premarket_last - previous_regular_close) / previous_regular_close`).
  `previous_regular_close` is exactly the persisted `previous_close` from the
  Daily Market Context Plan above — **no live fetch needed for the base**,
  only for the premarket price itself.
- **Timestamp:** the minute bar's own aware `America/New_York` timestamp
  (`MinuteBar.timestamp`), never a naive datetime — consistent with
  `docs/ai/SAFETY_RULES.md` §10.
- **Cache:** `KiwoomMarketDataProvider` currently has **no cache at all** for
  minute bars (unlike the unlimited-lifetime metadata/daily caches at
  `kiwoom.py:28,31`). A Drawer-open-triggered live fetch needs an explicit
  short TTL (recommend 30-60s, matching how fast a minute bar can change) in
  a new composition-layer cache — do not add an unbounded cache like the
  existing metadata cache, since premarket price is expected to move.
- **Rate limit:** minute requests use the `chart` bucket, 3 req/s real / 1
  req/s mock (`rate_limit.py:28-36`), same bucket as daily-bar fetches. A
  30-60s TTL keeps repeated Drawer opens for the same symbol from consuming
  this budget.
- **Ownership:** backend composition service, not the frontend, not the
  existing `KiwoomMarketDataProvider` internals unless Codex judges a
  provider-level cache cleaner — either placement is acceptable as long as
  the TTL and the rate-limit bucket discipline are preserved.

---

## Postmarket Plan

- **Source / derivation:** identical to Premarket Plan — `usa06011` minute
  bars, `MinuteBar.session == POSTMARKET` (bars at/after 16:00 ET local time,
  `mapping.py:57-58`), same DERIVABLE FROM MINUTE / pending-confirmation
  classification.
- **Return base — important nuance not present in the Premarket case:**
  `latest_postmarket_price` vs **today's regular-session close**
  (`(postmarket_last - regular_close) / regular_close`). Unlike premarket,
  this base is **not** the persisted scan-time snapshot — that snapshot's
  `previous_close` is the close from *before* the current trading day even
  started. Postmarket happens *after* today's own regular close, which has
  not been scanned/persisted yet (the next `ScannerRun` only happens after
  tonight's close, for tomorrow's candidates). Getting the correct
  `regular_close` therefore requires **one fresh daily-bar (or 16:00 minute
  close) fetch for the current trading date** at Drawer-open time — this is
  a genuine new live call, not reusable from the persisted snapshot. Flag
  this explicitly in the Codex prompt; it is easy to silently reuse the wrong
  (stale) close.
- **Cache / rate limit:** same TTL-cache design as Premarket. The extra
  same-day daily-bar fetch can share the existing unbounded `_daily_cache`
  (`kiwoom.py:31`) IF keyed correctly by exact date range — but since
  "today's close" changes meaning every day, prefer a TTL'd fetch here too
  rather than trusting the unbounded cache to naturally invalidate.
- **Timestamp:** same aware-datetime requirement as premarket.

---

## Market Cap Plan

**Unit safety verdict: do not assert a confirmed unit.**

Every hop in the pipeline — `mapping.py:21-26` (`number()`), `universe.py:73-77`
(`_optional_number()`), `scanner/config.py:11` (`minimum_market_cap =
300_000_000.0`, itself an implicit raw-dollar assumption), and
`frontend/lib/format.ts:24-26` (`compactUsd`) — treats the raw `mac` value as
already-scaled USD with **zero multiplier applied anywhere**. This audit
confirmed no accidental extra `×1000`/`÷1000` bug exists; the pipeline is
internally consistent. The problem is Kiwoom's own public contract, which
(per `docs/KIWOOM_INTEGRATION_SPIKE.md:83,93,249` and `smoke.py:93`'s own
`"unit UNCONFIRMED"` annotation) never states what unit `mac` is actually in.

**Corroborating evidence found in this audit, not previously documented:**
the real persisted value for NVDA is `market_cap: 5535047000.0`
(`data/runtime/usb_real_market_review.sqlite3`, `scanner_candidates` id=5),
which the frontend renders as `$5.5B`. A real-world company named NVIDIA
Corporation has a market capitalization on the order of trillions of
dollars, not billions — a ~500-700x discrepancy that does not correspond to
any clean unit multiplier (not thousands, not millions). This is independent
evidence, beyond the already-documented "unit unstated" concern, that the raw
`mac` figure should **not** be presented to an operator as a confirmed dollar
figure.

**Recommended design (Option B): show the number, flag the unit.**

- Do not hide the field entirely (Option A) — the raw figure is still
  internally comparable across TOP8 candidates (same field, same pipeline,
  same undetermined unit applied uniformly), so it retains some relative
  signal value for an operator comparing candidates.
- Do not present it as a confidently-formatted currency string (current
  behavior, `$5.5B`) — that implies a precision and correctness that hasn't
  been established.
- Concretely: keep `compactUsd`'s number formatting but drop the `$`
  currency styling for this specific field and append a visible
  "단위 확인 중" (unit unconfirmed) qualifier, e.g. `5.5B (단위 확인 중)` or a
  small muted-tone suffix badge, consistent with the existing
  `docs/DESIGN_SYSTEM.md` "Neutral: 정보성 상태" tone. This is a UI-only
  change (`frontend/lib/format.ts` + the Drawer row), no backend change
  required beyond what's already shipping.
- Do not attempt to "fix" the unit by guessing a multiplier in this stage —
  that would be exactly the kind of unconfirmed assumption `docs/ai/SAFETY_RULES.md`
  and this audit's own §36 checklist forbid. Confirming the true unit needs a
  live `usa10100`/`usa20550` cross-check against a known real-world market
  cap for a stable large-cap symbol, which is out of scope for this
  docs-only audit and should be its own small, explicit verification task.

---

## Time Display Plan

- **KST primary, ET internal — confirmed as a pure frontend change.** No
  backend datetime, calendar, or session logic needs to move to KST.
  `backend/app/api/router.py:49-61` already serializes an aware UTC/ET
  datetime; `MarketCalendar`/XNYS stays `America/New_York`
  (`docs/ai/SAFETY_RULES.md` §10, unchanged).
- Add `kstTime()` to `frontend/lib/format.ts`, mirroring the existing
  `etTime()` (`format.ts:17`) pattern exactly:
  `Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", ... })` — same
  robust pattern (parses the ISO-with-offset string via `new Date(value)`,
  then converts via `Intl`, so DST/offset correctness for Korea, which has no
  DST, is not even a concern; ET's DST is already handled correctly by the
  existing `etTime` implementation and needs no change).
- Header (`frontend/components/app-shell.tsx:37`): primary text becomes
  `kstTime(state.data.system_time)`; keep `etTime(...)` as a secondary
  line or tooltip.
- Research "분석 완료" timestamp (`frontend/app/research/page.tsx:44`): same
  treatment — KST primary, ET secondary. This reuses the same two formatter
  functions; there is no other ad hoc date formatting to hunt down (confirmed
  — `etTime` is the only timestamp formatter used across
  `app-shell.tsx`/`research/page.tsx`/`trading/page.tsx`/`runtime/page.tsx`/`candidates/page.tsx`).
- **Trading date is a date, not a timestamp — do not run it through a
  timezone formatter at all.** `ScannerRun.trading_date` (already an ISO
  date string, ET/XNYS-derived) should continue to render as a plain date
  labeled "기준 거래일 YYYY-MM-DD", untouched by any KST conversion. This is
  the explicit guard against the "KST rollover shifts the trading date by a
  day" failure mode called out in the audit brief (§22) — a KST *timestamp*
  formatter must never be applied to a *date-only* field.

---

## Session Display Plan

**Confirmed: three independent, non-shared implementations of the same
open-ended PREMARKET/REGULAR/POSTMARKET shape:**

1. `backend/app/strategy/session_policy.py:32-43` (`SessionPolicy.session_at`)
   — the **frozen execution guard**, used only by
   `backend/app/strategy/runner.py` (ENTER/ADD gating) and
   `backend/app/dev/run_real_market_simulation.py`. **Do not touch.**
2. `backend/app/api/router.py:49-61` (`market_status()`) — an independent
   re-implementation that feeds `/api/v1/dashboard` → the frontend Header.
   Confirmed via `grep -rn "session_policy|SessionPolicy" backend/app/api/`
   returning zero hits: this function does not call, import, or share code
   with #1.
3. `backend/app/integrations/kiwoom/mapping.py:53-58` (`map_minute_bar`) — a
   third, hardcoded 9:30/16:00 comparison (no `MarketCalendar` awareness at
   all, not even holiday/early-close handling) used only to tag
   `MinuteBar.session` for strategy/monitoring consumption.

All three currently agree that `POSTMARKET` has no upper bound (extends to
23:59:59 ET) and, symmetrically, `PREMARKET` has no lower bound (reaches back
to 00:00:00 ET). This is confirmed as **documented, intentional
execution-permissioning design** (`docs/SESSION_POLICY_AUDIT.md:10-13`,
`docs/ai/FROZEN_CONTRACTS.md:343-361`) — appropriate for "is a new ENTER
allowed right now", not designed with a human-facing display badge in mind.
No document anywhere discusses what the Header badge *should* say late at
night; that gap is real, not assumed.

**Proposed design (display-only, does not touch #1):**

- Extend **only** `market_status()` (#2, `router.py:49-61`) to return a
  fourth value, `CLOSED`, for `as_of` outside an explicit extended-hours
  window — e.g., before 04:00 ET or after 20:00 ET. **These exact cutoff
  values are a product decision, not something this audit should assert as
  fact** — 04:00/20:00 ET is the common US-brokerage extended-hours
  convention and is offered as a default; Codex's prompt should ask the
  operator to confirm before implementing, per this audit's own §36
  no-assumption checklist.
- `frontend/lib/display.ts:21-23` (`formatMarketSession`) gains a `CLOSED:
  "거래 종료"` entry — today it has no fallback for `CLOSED` at all beyond the
  raw-string default, confirming this state is never currently surfaced to a
  user in Korean.
- `backend/app/strategy/session_policy.py` and
  `backend/app/strategy/runner.py` are **not imported, not modified, not
  re-exported** by this change — verified as separate code paths in this
  audit (§ Root Causes). This satisfies the "must not touch frozen execution
  policy" constraint precisely because the two were already independent
  before this stage, not because of anything this stage does.
- `backend/app/integrations/kiwoom/mapping.py:53-58`'s minute-bar session
  tagging is used for strategy/monitoring purposes, not the Header — leave it
  unchanged; do not conflate "does this minute bar count as extended-hours
  data for premarket/postmarket composition" (needs the existing 9:30/16:00
  split, which is what Premarket/Postmarket Plan above already relies on)
  with "what should a human see on the Header right now" (needs the new
  CLOSED state).

---

## Backend Changes

Expected files (all confirmed to exist at the cited paths; no new top-level
modules other than one new composition service):

- `backend/app/scanner/domain.py` — extend `RankedCandidate` with
  `company_name: str | None`, `exchange: str | None`, `previous_open: float
  | None`, `previous_high: float | None`, `previous_low: float | None`,
  `previous_return_pct: float | None`; extend `score_components()` to emit
  the new keys.
- `backend/app/scanner/scanner.py` — in `_normalize_and_rank`
  (lines 213-231), read `item.metadata.company_name`/`.exchange` and
  `latest.open`/`.high`/`.low`, compute `previous_return_pct` from
  `item.bars[-2].close`, and pass all of it into the `RankedCandidate`
  constructor.
- `backend/app/market/reference.py` — optionally add `industry: str | None =
  None` to `SymbolMetadata`, **only after** a live `usa10100` smoke confirms
  a usable raw field name.
- `backend/app/integrations/kiwoom/client.py` — no allowlist change needed
  (`usa10100` is already allowlisted); wire an actual call site into the real
  path (`universe.py` or a `get_metadata()` fallback) only if industry is
  pursued. Document the added request-budget cost (N extra `usa10100` calls
  per universe symbol) per `docs/KIWOOM_INTEGRATION_SPIKE.md`'s existing
  budget-accounting convention.
- `backend/app/integrations/kiwoom/mapping.py` — extend `map_metadata()` to
  read and map the confirmed industry field, once known.
- `backend/app/api/service.py` — `candidate_dict()` (lines 32-39): add
  `"exchange"`, `"previous_open"`, `"previous_high"`, `"previous_low"`,
  `"previous_return_pct"` (and `"industry"` if implemented) reads from
  `parts`. `research_candidate()` (lines 68-80): merge in the same snapshot
  fields (currently it only reads `quant.rank`/`quant.score` from
  `ScannerCandidate`, ignoring `score_components_json` entirely) so the
  Drawer's single endpoint can carry full market context if Codex chooses to
  collapse the frontend's current two-fetch pattern — optional, the existing
  two-fetch pattern documented in `docs/FRONTEND_V1.md` also still works
  once `candidate_dict()` is fixed.
- **New:** a market-context composition service (suggested:
  `backend/app/services/market_context.py`) implementing the Premarket/
  Postmarket Plan above — TTL-cached minute-bar lookups, the same-day
  regular-close fetch for the postmarket base, and typed missing-data reasons
  (`NOT_AVAILABLE`, `OUTSIDE_SESSION`, `UNIT_UNCONFIRMED`, `PROVIDER_ERROR`
  per §25 of the audit brief) rather than a single flat "unavailable" string.
- `backend/app/api/router.py` — `research_candidate()` route: call the new
  composition service for premarket/postmarket when appropriate; extend
  `market_status()` per the Session Display Plan (display-only `CLOSED`).
- `backend/app/api/schemas.py` — introduce a typed Pydantic response model
  for the research candidate detail endpoint (currently `dict[str, Any]` with
  no `response_model=`, confirmed at `router.py:128`) covering every field
  above, replacing the untyped dict. This is a strengthening of an existing
  contract, not a breaking change — no field this stage adds is currently
  relied upon as absent by any test.

---

## Frontend Changes

- `frontend/types/api.ts` — extend `Candidate` (line 9) with `exchange:
  string | null`, `industry: string | null` (if implemented),
  `previous_open/high/low: number | null`, `previous_return_pct: number |
  null`, `premarket`/`postmarket` nested objects (`{ price, return_pct,
  observed_at }` or a typed union including a `reason` field for the
  not-available case). Extend `Dashboard["market"]["session"]` to include
  `"CLOSED"`.
- `frontend/app/research/page.tsx` — replace the hardcoded literals at
  lines 56-57 (거래소, 업종, 전일 등락, 전일 고가/저가, 애프터마켓, 프리마켓)
  with real field reads plus null-safe fallback to the existing `unavailable`
  constant — the fallback logic pattern already used for `latest_close`/
  `latest_volume`/`rvol` at the same lines is the correct template to copy.
  Add a 전일 시가 row (currently not rendered at all, not even as a
  fallback).
- `frontend/lib/format.ts` — add `kstTime()` alongside `etTime()` (line 17);
  add a market-cap-safe formatter/wrapper per the Market Cap Plan (do not
  simply reuse `compactUsd` for this one field once the "단위 확인 중"
  treatment is added).
- `frontend/lib/display.ts` — `formatMarketSession` (lines 21-23): add
  `CLOSED: "거래 종료"`.
- `frontend/components/app-shell.tsx` — Header (line 37): switch primary
  time to `kstTime(...)`, keep `etTime(...)` as secondary/tooltip; the
  session badge automatically picks up the new `CLOSED` label once
  `display.ts` and the backend both support it.
- `frontend/components/research-detail.test.tsx` — currently asserts the
  literal `unavailable` string is present for these fields (per this audit's
  findings); update once real wiring lands, per `docs/ai/SAFETY_RULES.md`
  §11 ("never weaken an existing test to make something pass" — update the
  assertion to match new, correct behavior, don't just delete it).

---

## DB Changes

**NONE.** Every field enumerated in this stage fits inside the existing
`scanner_candidates.score_components_json` JSON column
(`backend/app/models/scanner.py:61`, `JSON NOT NULL`, confirmed unchanged
since `20260901_0001_scanner_snapshots.py`). Premarket/postmarket are
proposed as pure runtime composition, not persisted, so they add no column
either. If a future stage decides premarket/postmarket must be persisted for
reproducibility (Quant-snapshot-style), that is explicitly **out of scope**
here and would need its own migration + its own adoption-procedure decision
per `docs/ai/CURRENT_STATE.md`'s Blocker #1 (the real-market review DB has no
`alembic_version` row) — do not fold that decision into this stage.

---

## Tests

### Metadata

- **M1** company_name: given a `RankedCandidate` built from a `SymbolMetadata`
  with a non-null `company_name`, assert `score_components()["company_name"]`
  and `candidate_dict()["company_name"]` both round-trip the value (currently
  fails — always `None`).
- **M2** exchange: same shape as M1 for `exchange`.
- **M3** industry missing semantics: with no industry field/value available,
  assert the API returns `null`/absent (not a fabricated placeholder) and the
  Drawer renders "정보 없음" only in that specific case, not unconditionally.
- **M4** market-cap unit-unknown safe display: assert the Drawer shows the
  "단위 확인 중" qualifier for every market_cap value (since the unit is
  never confirmed in this stage) and never renders a bare `$X.XB` currency
  string.

### Daily Context

- **D1-D4** previous_open/high/low/close: given a fixture with a known 2-bar
  (or 21-bar) history, assert `RankedCandidate`/persisted JSON/API response
  carry the exact `open`/`high`/`low`/`close` of the latest bar.
- **D5** previous_return_pct: given `bars[-2].close = 100`, `bars[-1].close =
  105`, assert `previous_return_pct == 0.05`; given `bars[-2].close == 0`
  (degenerate fixture), assert the result is `None`, never a `ZeroDivisionError`
  or `inf`.
- **D6** previous_volume: regression — confirm existing `latest_volume`
  behavior is unchanged.
- **D7** trading_date alignment: assert adding these fields does not change
  `ScannerRun.trading_date` or any existing eligibility/ranking test outcome
  (pure additive change).

### Extended Sessions

- **E1** premarket derive: with a fixture minute-bar history containing a
  pre-09:30 ET bar, assert the composition service returns that bar's close
  as `premarket.price` with a correctly aware `observed_at`.
- **E2** postmarket derive: same shape for a post-16:00 ET bar.
- **E3** return base: assert premarket return is computed against the
  **persisted** `previous_close`, and postmarket return is computed against
  a **freshly fetched same-day regular close** — write a fixture where these
  two bases differ, to catch an implementation that wrongly reuses the
  persisted snapshot for postmarket (the nuance flagged in the Postmarket
  Plan above).
- **E4** outside-session missing semantics: at a REGULAR-session `as_of`,
  assert premarket/postmarket both return a typed `OUTSIDE_SESSION`-style
  reason, not `NOT_AVAILABLE` (these must be distinguishable per §25 of the
  audit brief).
- **E5** aware timestamp: assert every timestamp entering this new
  composition path rejects a naive datetime (matching the existing domain
  validator pattern in `backend/app/market/domain.py`).

### Time

- **T1** KST primary display: `kstTime("2026-09-03T21:43:00-04:00")` (an
  ET-instant ISO string) renders the correct Seoul wall-clock time (fixed
  input → fixed expected string, no `datetime.now()`).
- **T2** ET secondary optional: `etTime()` output is unchanged by this stage
  (pure regression check — this function must not be touched).
- **T3** trading_date unchanged: assert the "기준 거래일" label continues to
  render the raw ET-derived date string, never passed through `kstTime()`.
- **T4** DST: one fixture in EDT, one in EST, assert both `kstTime` and
  `etTime` produce correct offsets (Korea has no DST, so this mainly
  exercises the ET side, already correct today — regression only).
- **T5** midnight/date rollover: an ET timestamp just before local midnight
  that rolls to the next KST calendar date — assert the Header's date label
  changes correctly while `trading_date` (T3) does not.
- **T6** early close: a fixture using `MarketCalendar`'s early-close data —
  assert `market_status()`'s new `CLOSED` boundary still resolves sensibly
  relative to an early 13:00 ET close (i.e., the display cutoff should be
  relative to that day's actual close, not a hardcoded 16:00 assumption).

### Session Badge

- **S1-S4** premarket/regular/postmarket/closed: fixture `as_of` values
  squarely inside each window, assert `market_status()["session"]` and the
  Korean label match.
- **S5** 21:43 ET no longer incorrectly "애프터마켓" if the operator-approved
  policy says so past the cutoff — write this test to assert whatever cutoff
  the operator actually confirms (do not hardcode 20:00 into the test before
  that confirmation lands).

### Regression

- Strategy session guard unchanged: run
  `backend/tests/test_kiwoom_stage10a.py`,
  `backend/tests/test_real_market_scanner_stage10b.py`, and
  `backend/tests/test_real_market_simulation_stage10b2.py::test_sim_broker_is_process_local`
  (or equivalent current names) and confirm zero deltas in
  `session_policy.py`-dependent behavior.
- Kiwoom orders 0: re-assert `order_request_count == 0` after this stage,
  per `docs/ai/SAFETY_RULES.md` §3.
- Quant unchanged: `quant_v0` weights/eligibility/ranking must be
  byte-for-byte unchanged — this stage only *adds* fields, never touches a
  scored metric.
- Research schema unchanged: `gpt_research_v0`/`ACCEPTED_PROMPT_VERSIONS`
  untouched; the new Pydantic *response* schema is additive and does not
  touch the *import* schema (`GPTResearchResult`).
- HumanDecision unchanged: no new code path may write a `HumanDecisionRecord`.

---

## Safety

- No frozen contract listed in `docs/ai/FROZEN_CONTRACTS.md` §"Explicitly not
  frozen" boundary is crossed: Quant V0 weights/eligibility, Risk V0, Strategy
  V0, `session_policy.py`'s execution semantics, Shadow variant configs,
  `SimBroker`, the `kiwoom/simulation/market_data_only` gate, and Human
  Decision rules are all read-only in this plan — verified untouched by every
  file listed under Backend/Frontend Changes above.
- No new Kiwoom endpoint is added to `ALLOWED_ENDPOINTS`
  (`backend/app/integrations/kiwoom/client.py:23-35`) beyond calling an
  **already-allowlisted** `usa10100` from a new call site, and using the
  **already-implemented** `minute_chart()`/`daily_chart()` methods from a new
  call site. No order/account/FX endpoint is touched.
- No migration, no DB adoption-procedure decision, no schema change.
- Market cap unit is explicitly NOT asserted as confirmed dollars anywhere in
  this plan — the recommended fix is a disclosure ("단위 확인 중"), not a
  guessed multiplier.
- Premarket/postmarket are designed to fail safe (`NOT_AVAILABLE`/
  `OUTSIDE_SESSION` typed reasons) rather than fabricate a value if the live
  minute-coverage smoke comes back empty.
- The Header/session-badge fix is scoped to `router.py:49-61` and the
  frontend display layer only — `session_policy.py` (the frozen execution
  guard) is explicitly named as untouched in every relevant section above,
  and this was verified as a pre-existing separation (not something this
  stage needs to newly create).

---

## Audit Notes (documentation conflicts found)

- `docs/FRONTEND_V1.md` (lines 59) currently reads: "Frozen API가 제공하지
  않는 일간 등락률·고가/저가·Premarket·Postmarket 값은 Frontend에서 계산하거나
  GPT narrative에서 추출하지 않고 `데이터 미제공`으로 표시한다." This
  correctly describes **today's** behavior and the reason for it, but will
  become stale the moment this stage ships (the API will provide these
  fields). Per this audit's own instruction (§35), this document was **not**
  edited here; whoever implements Stage 10B-3.2 should update
  `docs/FRONTEND_V1.md` alongside the code change, not before.
- `docs/KIWOOM_INTEGRATION_SPIKE.md:83` classifies `usa10100`
  symbol/company/exchange mapping as "PASS/PARTIAL" — accurate for the fields
  it actually maps (company_name, exchange via `map_metadata`), but this
  audit found `usa10100` itself is never invoked outside `smoke.py`, which
  the existing doc does not explicitly call out. Worth a follow-up doc note
  when Stage 10B-3.2 lands, not changed here.
- No conflict found between `docs/ai/FROZEN_CONTRACTS.md`'s Session Policy
  table and the actual code — the table's "POSTMARKET: yes/no/no/yes/yes"
  row with no time-boundary caveat matches `session_policy.py` exactly. The
  "bug" is a UX gap in a *different, unfrozen* code path (`router.py`), not a
  documentation/code mismatch in the frozen artifact itself.

---

## Definition of Done

- [x] `RankedCandidate` carries company_name, exchange, previous_open/high/low,
      previous_return_pct; `score_components()` emits them.
- [x] `candidate_dict()` and `research_candidate()` in `api/service.py` expose
      all of the above (no `None`-by-construction fields remain for data that
      is actually available).
- [x] One supervised live `usa10100` smoke call ruled out a usable industry
      field for the bounded AAPL response; industry stays provider-unavailable
      with an honest UI label.
- [x] One supervised live minute-chart check confirmed 1,000 POSTMARKET
      canonical rows; PREMARKET remains explicitly unconfirmed and falls back
      to typed `NOT_AVAILABLE_FROM_PROVIDER` when no matching rows exist.
- [x] New market-context composition service implements the premarket vs
      persisted-previous-close and postmarket vs fresh-same-day-close return
      bases correctly and distinctly (E3 test passes).
- [x] Market cap Drawer cell shows a unit-unconfirmed qualifier; no code path
      applies a guessed multiplier to `mac`.
- [x] `kstTime()` exists in `frontend/lib/format.ts`; Header and "분석 완료"
      timestamp show KST primary / ET secondary; `trading_date` is never
      passed through a timezone formatter.
- [x] `market_status()` gains the requested 04:00/20:00 ET `CLOSED` cutoff,
      without
      importing or modifying `backend/app/strategy/session_policy.py` or
      `backend/app/strategy/runner.py`.
- [x] `formatMarketSession` in `frontend/lib/display.ts` maps `CLOSED` →
      "거래 종료".
- [x] All tests in the Tests section above pass; full backend
      (`PYTHONPATH=backend .venv/bin/python -m pytest`) and frontend
      (`npm run lint && npm run typecheck && npm test && npm run build`)
      suites pass with **no reduction** in passing count, per
      `docs/ai/SAFETY_RULES.md` §11.
- [x] `order_request_count` still asserts zero; no new endpoint added beyond
      already-allowlisted TRs; no migration created; no `.env` touched.
- [x] `docs/FRONTEND_V1.md` and `docs/KIWOOM_INTEGRATION_SPIKE.md` updated to
      reflect the new, non-stale behavior (post-implementation, not part of
      this audit).

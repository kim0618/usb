# H-V2 MethodJudgement IO Optimization P1 (result)

Date: 2026-10-16 KST. Infrastructure/performance only: no E1/E2/E3, intake order, MethodJudgement, D3-D6, paper
or forward semantics changed; no forward return used; not deployed, not pushed. Evidence (local):
`/home/tjd618/h_mj_p1/` (`baseline.json`, `after_shared.json`, `after_default.json`, `old_emulated.json`,
`scan_old.json`, `scan_new.json`).

Verdict: **PASS.** Outputs identical (canonical hashes, 22 issuers; full SCAN index 707/707 rows); total SCAN
reads 1.82 GB -> 0.67 GB, MethodJudgement screen reads 1,236 MB -> 94 MB, screen time 570 s -> 245 s; peak RSS
unchanged.

## 1. Why

The Hierarchical Intake R0 audit measured 68 % of a SCAN's 1.82 GB of reads in the MethodJudgement screen. The 1 GB
host stalls under memory + IO pressure; this is the largest single IO source.

## 2. Call graph (read from code)

```text
intake.scan_mode
  -> order(candidates)                               E3 class, filing recency, ticker
  -> screen_method_judgement(ordered, index, ...)
       -> SRC.series_for(need)                       one pass over the price store for the judged tickers
       -> per candidate (until `room` AUTO_READY or the verdict budget):
            IN.fetch_sec(cik, day)                   cached SEC files (submissions pages + companyfacts)
            read submissions doc (sic)
            write stub package (cik, cutoff)
            IN.Panel(ticker, SPY)
            IN.d5_binding(...)                        points the frozen D5 loaders at these inputs
              -> D5-D2 build_panel(ticker, closes, until)
                   -> audit_d5_p1.load_issuer        companyfacts + submissions (VALUATION_FIELD_SPECS)
                   -> for each session (~520):
                        resolve_session(...)
                          -> load_reference_snapshot(session)   <- re-read + re-parse CS_<as_of> every call
                          -> share_class.resolve_share_class(cik, session, snapshot)
                          -> every method's multiple
            MJ.derive(ticker, decision_row, sic)     AUTO_READY / NOT_READY, primary, reasons
```

The executor uses the same `d5_binding` + `build_panel` twice per evaluated item (`derive_judgement`, `_d5_row`).

## 3. Root cause (instrumented, one issuer and the full dry run)

One verdict (ADI) read 63 MB, 61 MB of it `reference_history/CS_*.json.gz`: 8 quarterly snapshots re-read 60-64
times each (501 reads), i.e. once per panel session, because `load_reference_snapshot` decompresses and parses the
chosen file on every call. Per candidate 520 loader calls; 22 issuers 11,108 calls and 1,336 MB. In the full SCAN
the reference_history directory accounted for 9,197 reads and 1,122 MB of the 1,816 MB.

Duplication class: **D (all)** for the reference snapshots: the same global files are re-read within one candidate
(every session) and across candidates. Price files are not re-read per candidate (`series_for` is one pass); the
benchmark rides in that pass.

## 4. Minimal context actually consumed

| Field | Consumer | Source | Depth | Shared? | Exact? |
|---|---|---|---|---|---|
| chosen snapshot per session (newest `as_of` <= session) | D5 `resolve_session` | `CS_<as_of>.json.gz` listing | all sessions | yes | same choice rule |
| `snapshot.as_of` | `resolve_share_class` (staleness) | parsed file | scalar | yes | yes |
| `snapshot.securities(cik)` for the judged CIK | `resolve_share_class` | parsed file | per CIK | yes | yes |
| `snapshot.path`, `rows` | record/provenance | parsed file | scalar | yes | yes |

Nothing else of the 5,300-row file is read by D5 for one issuer.

## 5. Design

Candidates compared: full raw store in memory (no: the store is not the problem), compact projection, mmap/index,
shared handle, per-ticker cache. Chosen: **compact projection, SCAN-scoped** (`inputs.ReferenceSnapshotCache`):

* the directory listing is built once; the choice rule is the loader's own (directories in order, files sorted,
  newest `as_of` on or before the session, first maximum wins);
* the original `share_class.load_reference_snapshot` is called **once per chosen file**, and the cache checks it
  chose the same file (else it raises);
* only the CIKs the run asked for are kept (`ciks`); a lookup for any other CIK raises `ReferenceCacheMiss`
  instead of answering `()` (which D5 would read as CIK_ABSENT_FROM_REFERENCE) - no silent change possible;
* `ReferenceSnapshot` stays frozen; the narrowed `by_cik` mapping is read-only;
* `d5_binding(reference_cache=..., reference_ciks=...)`: the screen builds one cache for all CIKs it may judge;
  the executor's two bindings narrow to the item's CIK; without arguments a per-binding cache is used. Directories
  must match the binding's (`d5_reference_dirs()`), else it raises.

Memory: all 8 snapshots fully parsed would retain 16.6 MB; the narrowed cache retains only the judged CIKs.

## 6. Equivalence

| Check | Result |
|---|---|
| 22 issuers (10 AUTO_READY + 9 NOT_READY from the full scan, FG / VRRM / ADI), canonical hash of D5 panel, decision row, coverage, MJ state, reasons and report, D5 row, D6 decision (FG, VRRM, ADI with their stored V3 D3/D4) | `ce53d68a...` identical: before, after (shared cache), after (default cache), and the pre-P1 emulation |
| Full SCAN dry run (same store copy and clock, fetch cap 700) | intake index 707/707 rows equal, 19/19 MethodJudgement verdicts and reasons equal, `new_candidates.json` equal (same 10, same order) |
| Mismatches | 0 |

The pre-P1 emulation (loader re-read on every lookup) reproduced the frozen baseline exactly (same hash, 1,336 MB,
11,108 calls), so the before/after SCAN comparison uses it.

## 7. Resource measurements

Issuer set (22, shared cache as in the screen):

| | before | after |
|---|---|---|
| bytes read | 1,336 MB | 7.8 MB |
| reference loader calls | 11,108 | 10 |
| time | 540-553 s | 214 s |
| peak RSS | 217.3 MB | 217.4 MB |

Full SCAN (production-copy store, decision time 2026-10-15 01:00 UTC; the "before" run overlapped a few minutes of
another local job, so its time is inflated; the clean pre-P1 run of 2026-10-16 was 1,055 s):

| | before | after |
|---|---|---|
| total bytes / files read | 1,816 MB / 15,475 | 674 MB / 6,117 |
| duplicate reads | 13,478 | 4,120 |
| MethodJudgement screen bytes / files / time | 1,236 MB / 10,048 / 570 s | 94 MB / 690 / 245 s |
| reference_history reads | 9,197 (1,122 MB) | 8 (1 MB) |
| minor page faults (major) | 309,773 (0) | 254,833 (0) |
| process peak RSS | 309 MB | 314 MB (peak is in the series/companyfacts stages, not the screen) |
| total time | 1,268 s (1,055 s clean) | 964 s |

Stage times after: SEC download 661 s, price series 13 s, companyfacts parse 30 s, E1-E3 10 s, screen 245 s.

## 8. What is left (not P1)

* Price store passes: 3 per SCAN (market gate + series for scanned rows + series for judged rows), 234 MB, 13 %
  before / 35 % after. This morning's production stall began before the market-pass log, i.e. in this phase.
* SEC download (661 s) dominates wall time; companyfacts files are read twice (download check + parse).
* Submissions pages re-read about 9 times per judged candidate by `fetch_sec` (a few MB).
* Peak RSS (~310 MB) is set by the series/companyfacts stages and is unchanged.

## 9. Operational effect (estimate, not a production result)

The screen phase drops from about 1.2 GB of decompress-and-parse over ~10 minutes to under 0.1 GB over ~4 minutes:
much less page-cache churn and CPU during that window. It does not address the price-store passes where the
morning stall began, nor the peak RSS. Whether crypto latency is acceptable needs the production controlled run
with the same monitor.

## 10. P2 decision

**Recommended: YES (after the production A/B of P1).** An incremental price state (market gate + series updated by
the newest session file) would remove most of the remaining 234 MB and the phase where the stall began; it carries
more correctness risk (persistent derived state) and must be preregistered with its own equivalence test.

## 11. Code

`backend/app/strategies/h_auto/inputs.py` (`ReferenceSnapshotCache`, `ReferenceCacheMiss`, `d5_binding`
arguments), `intake.py` (screen builds one cache), `executor.py` (two bindings narrowed to the item's CIK).
Tests: `backend/tests/strategy_h_v2/forward/test_h_mj_reference_cache.py` (12).

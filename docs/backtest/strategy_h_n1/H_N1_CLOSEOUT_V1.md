# H-N1 CLOSEOUT V1

2026-10-19 KST. This document is the current-state authority for H-N1. Results: `H_N1_OUTCOME_CENSUS_V1.md`.

```text
H-N1 STATUS                      CLOSED / FAIL
why closed                       signal non-replication across Development and Holdout
sample sufficiency               SUFFICIENT (PRIMARY N: DEV 967; HOLDOUT 619 / 616 / 341 at 5/21/63D)
research integrity               PASS
contract frozen before outcome   YES
Holdout isolation                PASS
Contrast result                  does not support the fundamental-change thesis
rescue                           PROHIBITED
runtime                          PROHIBITED
Paper                            PROHIBITED
GPT alpha                        PROHIBITED
H-N2                             NOT STARTED
future research                  separately preregistered study only
```

## 1. Closeout statement

The preregistered F+ ∩ WEAK signal did not reproduce across Development and Holdout.
Fundamental improvement did not demonstrate incremental predictive value versus F- ∩ WEAK.
No rescue analysis is authorized under H-N1.
Any further hypothesis requires separately versioned research, a new preregistration, and no reuse of
H-N1 outcomes for threshold or feature selection.

**한국어:** 사전등록한 F+ ∩ WEAK 신호는 Development와 Holdout에서 재현되지 않았다. 매출·순이익 개선은
F- ∩ WEAK 대비 추가 예측력을 보이지 못했다. H-N1 안에서 어떤 rescue 분석도 허용되지 않는다. 다른 가설은
별도 version, 새 사전등록이 필요하며, H-N1 결과를 임계값·feature 선택에 재사용할 수 없다.
표본 부족이 아니며 비용만의 문제도 아니다.

## 2. Verdict evidence (read from the frozen artifacts, not recomputed)

Primary = 10-Q, F+ ∩ WEAK. Contrast = F- ∩ WEAK. Mean excess vs SPY at 0 bp, OBSERVED delisting variant.

| horizon | PRIMARY DEV | PRIMARY HOLDOUT | CONTRAST DEV | CONTRAST HOLDOUT |
|---|---:|---:|---:|---:|
| 5D | -0.27% | -0.31% | +0.03% | -0.88% |
| 21D | +0.81% | -0.32% | +1.58% | -1.26% |
| 63D | -1.22% | +3.44% | -0.13% | +1.83% |

- In DEV, F- ∩ WEAK beats F+ ∩ WEAK at all three horizons.
- Gate matrix: 5D 0/8, 21D 1/8 (seasons only; shorter-horizon consistency FAIL), 63D 0/8.
- HOLDOUT 63D: 266 / 341 = 78% of windows contain the next 10-Q; top 5 events = 50.0% of net at 10 bp; median about 0.
- Delisting: OBSERVED and CONSERVATIVE agree (0-1 true delistings per PRIMARY horizon).
- Verdict order: INSUFFICIENT no, FAIL_SIGNAL no, FAIL_COST_BOUND no, PASS no, FAIL_HOLDOUT no, BORDERLINE no -> **FAIL**.
- PASS = NO, BORDERLINE = NO, INSUFFICIENT = NO.

## 3. Provenance

| item | value |
|---|---|
| pre-amendment contract | `superseded_e08f3d8c/` (bytes unchanged; canonical e08f3d8cab90ac4917cfbf91b7e0f9fb5a2ade9826b1ed25007a68f5bd3545e7, file b83bf917…b976, MD 9d31bcc4…0fd2) |
| superseded marker | `superseded_e08f3d8c/SUPERSEDED.txt` = SUPERSEDED_BY_PRE_OUTCOME_AMENDMENT |
| final contract | `h_n1_contract_v1.json` status FROZEN_BEFORE_OUTCOME, revision PRE_OUTCOME_AMENDMENT_1 (A-E) |
| canonical checksum | 5353017cdf4d59388a46c7882b4f95c78a4d6c88d2d33de7ad4f63c1d560d9f8 |
| JSON sha256 | e990dc1c151077397d50c60e0b49007050ce98be0ab01dd94cba117acc66fe71 |
| MD sha256 | b5a9c1b3c094a005f0da7f4f40ed4f8ac592c48f24b76c917b2fe3041f008a5c |
| freeze timestamp | 2026-10-19 15:40:35 KST (06:40:35Z) |
| state at freeze | outcomes 0, q33/q67 NOT CALCULATED, Holdout NOT INSPECTED |
| Dev cutoff | q33 -3.03% (-0.030345), q67 +3.61% (+0.036054), N 6,944, canonical digest dd182483f0f970a16b35d3d7c399934d586d7075ef4a00760a1c79ecf24d37d6 |

Run order (`data/runtime/strategy_h_n1/run_log.txt`, UTC):
```text
06:40:35Z CONTRACT_FROZEN canonical=5353017c
06:46:46Z PHASE1_ABORTED (code error, no artifact)    06:51:21Z PHASE1_ABORTED (JSON bool, partial file removed)
06:55:22Z PHASE1_DONE events=24115
06:55:38Z PHASE2_DONE q33/q67 frozen (DEV rows only)
06:55:43Z PHASE3_START first forward price read
06:56:20Z PHASE3_DONE verdict=FAIL
```

## 4. Runtime artifacts (gitignored, not committed)

Path `data/runtime/strategy_h_n1/`:

| artifact | sha256 |
|---|---|
| h_n1_events_phase1.json.gz | 6fb84cfb34fc8fe0252a8f179e813e1c500d6491adb0b78a15c72e429fa57f17 |
| h_n1_dev_reaction_cutoff_v1.json | 507bfeafdd7a3ec9552c8b076e15e22eeddd40cd9d3648d3c4aaa14e38550c4b |
| h_n1_outcomes_phase3.json.gz | cfa1ce4ffe3cdfe17bb5b352a3293b59eef9f9ba7a21d908f1e3b42e0d76714e |
| h_n1_funnel_v1.json | 7eb8020438d7b2e67277d065de5419472e704c00304f71d253308e6692aa3b05 |
| h_n1_outcome_summary_v1.json | d0b98e39353117005a747a21d34fff977d85ddd2c5c5c7ac32bfbf945b9a6181 |
| run_log.txt | order evidence above |

## 5. No-rescue policy (H-N1, permanent)

Prohibited under H-N1: GPT rescue, AI thesis layer, new feature mining, adding EPS / margin / FCF / acceleration,
changing the reaction cutoff or q33/q67, small-cap only, large-cap only, sector subset, filing-season subset,
21D-only or 63D-only rescue, new horizons (90D etc.), price or ADV threshold changes.
Studying any of these requires a separate version and a separate preregistration.

## 6. Code and tests

`backend/app/backtest/strategy_h_n1/` (common, events, cutoff, outcomes, run, report) and
`backend/tests/test_strategy_h_n1.py`: 25 passed at closeout (20 at census time + 5 synthetic price-path tests added
at closeout without code change: entry on the next session open, SPY window alignment, split adjustment,
OBSERVED vs CONSERVATIVE delisting, CIK rename continuity, exit gap). Phase 1 and Phase 2 refuse to run when their
artifact already exists (no cutoff recomputation); Phase 3 runs only after the contract checksum and cutoff digest verify.

## 7. H production isolation

H-N1 never read the legacy Strategy H ledger and changed nothing in operations:
H Research SUSPENDED, Forward ACTIVE (usb-h-forward unchanged), Paper history PRESERVED, Autonomous Evaluation OFF
(usb-h-evaluation stays disabled), server changes 0.

## 8. Next state

```text
H-N1                       CLOSED / FAIL
H-N2                       NOT STARTED (not an automatic next step)
H Production Research      SUSPENDED
Forward Observation        ACTIVE
Historical Replay          DEFERRED
New research               separate preregistration only
```

H-N1 = FAIL

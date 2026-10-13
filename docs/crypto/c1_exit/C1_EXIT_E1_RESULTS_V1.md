# US-B CRYPTO C1 Exit Research E1 결과 V1 - Executable 4H Baseline

작성 2026-10-02. 사전등록 `C1_EXIT_E1_BASELINE_CONTRACT_V1.md`
(sha256 `3b2f188fee1e40484242f2a3b325621112bc2de676ef652335ebcd7fcc0d7a35`)을 그대로 실행했다. 동결 뒤 계약 변경 0회.
원본 `data/research/crypto/c1_exit/e1_results_v1.json`, trade 원장 `e1_trades_v1.parquet`(1,603행).

## 0. 결론

| 항목 | 값 |
|---|---|
| **E1 Status** | **E1_INCONCLUSIVE** |
| **E2 Authorization** | **EXPLORATORY_ONLY** |
| 4H BASE net mean | **+10.27bp**, 7일 블록 95% CI **[-9.30, +29.99]** |
| Secondary warnings | **0개** (W1~W6 전부 미발동) |
| 계약 외 관찰 | top 5% 제거 시 -17.44bp로 부호 반전 (warning 정의 밖, 기록만) |

C1을 실제 거래 단위로 재구성하면 점추정은 양수로 남지만 CI가 0을 넓게 걸친다. D5.2 bar 단위 +19.28bp의 약 절반이다.
**이 결과는 기존 연구 데이터에서 실행 가능한 거래 단위 baseline을 재구성한 것이며, 새 out-of-sample confirmation이 아니다.**

## 1. Entry event 정의

| 단위 | 수 |
|---|---|
| raw C1 TRUE bar (전체 / OOS) | 29,695 / 18,499 |
| state episode (전체 / OOS 결정) | 2,584 / 1,603 |
| E1 executable trade | **1,603** (제외 0: 데이터 끝 0, 가격 결측 0, 최소수량 0) |
| 2021 episode (미사용) | 981 |
| trade가 있는 UTC 날짜 | 179 |
| episode 길이 (사후 정보) | 중앙 5분, p90 25분, 최대 175분 |
| 4H 안에 다음 entry가 온 비율 | 1,388 / 1,602 간격 |
| single-position 참고 (판정 불가) | 417 trade, net +11.59bp |

Production C1 lifecycle 계약은 repository에 없었다 → provisional FALSE→TRUE 정의. **parity 아님**(운영 계약 동결 시 재계산).
D5.2 parity: OOS C1 TRUE bar 18,498 (D5.2 마스크) 일치, vol cutoff 일치.

## 2. 4H BASE / STRESS

| | BASE | STRESS |
|---|---|---|
| gross mean | +21.31 | +21.31 |
| net mean | **+10.27** | +7.74 |
| 7일 블록 CI (primary) | [-9.30, +29.99] | [-11.83, +27.46] |
| day-cluster CI (보조) | [-11.67, +31.19] | - |
| iid CI (참고) | [-0.46, +21.00] | - |
| net median | +1.25 | -1.27 |
| win rate | 50.4% | 49.5% |
| PF | 1.146 | 1.108 |
| total net (bp 합) | 16,463 | 12,415 |
| avg win / avg loss | +159.5 / -141.4 | +160.0 / -141.3 |
| P5 / P10 / worst | -329.7 / -231.7 / -1,191.0 | -332.3 / -234.2 / -1,193.5 |

비용 분해(평균 bp): fee 11.01, spread 0.04, funding -0.01, 합 11.04. 회계 항등식 위반 0.
iid CI만 하한이 거의 0에 닿는다. 하루 평균 9건이 겹쳐 있어 iid는 신뢰구간을 과소평가한다(계약대로 참고만).

## 3. MFE / MAE (4H, 경로 극값, stop 시뮬레이션 아님)

MFE 평균 +174.0 / 중앙 +126.9bp, MAE 평균 -158.0 / 중앙 -103.5bp. 극값 도달 시각 중앙 MFE 100분, MAE 87분.
4시간 안의 경로 진폭(±100bp대)이 최종 net 평균(+10bp)보다 한 자릿수 크다.

## 4. 참고 horizon (benchmark 교체 금지)

| horizon | gross | net | median | win | PF | STRESS | 7일 블록 CI |
|---|---|---|---|---|---|---|---|
| 30m | +6.80 | -4.24 | -6.37 | 44.4% | 0.86 | -6.77 | [-9.50, +1.64] |
| 1h | +12.13 | +1.10 | -4.06 | 47.2% | 1.03 | -1.43 | [-6.08, +8.26] |
| 2h | +13.84 | +2.81 | -0.80 | 49.6% | 1.05 | +0.28 | [-9.69, +14.81] |
| **4h** | +21.31 | **+10.27** | +1.25 | 50.4% | 1.15 | +7.74 | [-9.30, +29.99] |
| 8h | +25.49 | +14.45 | +10.68 | 51.8% | 1.15 | +11.93 | [-22.10, +52.35] |

보유가 길수록 net 점추정이 오르지만 CI도 같이 넓어진다. D6-C의 "비용 고정 → 긴 보유 유리" 산술과 같은 모양이다.

## 5. Top-trade 제거 (BASE)

| 제거 | 개수 | net mean |
|---|---|---|
| 없음 | 0 | +10.27 |
| top 1 | 1 | +9.67 |
| top 3 | 3 | +8.57 |
| top 1% | 17 | +2.13 |
| top 5% | 81 | **-17.44** |

## 6. Fold

| fold | 기간 | trades | net mean | median | sign |
|---|---|---|---|---|---|
| F1 | 2022H1 | 455 | +11.10 | -11.02 | + |
| F2 | 2022H2 | 212 | **-54.54** | -22.86 | - |
| F3 | 2023H1 | 79 | +73.78 | +30.63 | + |
| F4 | 2023H2 | 45 | +25.68 | -1.56 | + |
| F5 | 2024H1 | 300 | +8.73 | -2.53 | + |
| F6 | 2024H2 | 137 | +37.11 | +36.58 | + |
| F7 | 2025H1 | 194 | +33.36 | +28.39 | + |
| F8 | 2025H2 | 31 | -22.26 | -44.33 | - |
| F9 | 2026 ~ 9/23 | 150 | +16.69 | -8.48 | + |

**positive folds 7/9.** SMALL(<20) fold 없음. 9개 중 6개 fold(F1·F2·F4·F5·F8·F9)의 median이 음수다.

## 7. Lookahead audit

| 항목 | 결과 |
|---|---|
| 계약 truncation test (2개 시점) | 불일치 0. **단 두 창 모두 C1 TRUE가 0개라 TRUE 보존은 검증하지 못함** |
| 보조 truncation test (계약 외, 결과 뒤 추가) | 각 fold 첫 신호 3분 뒤 9개 시점, 창마다 TRUE 4~14개, **불일치 0** (`e1_truncation_supplementary_v1.json`) |
| S1 bucket | `features.bucketize`: 날짜 D cutoff = [D-30, D) 값만 |
| OI | `dataset.OI_DELAY_MS` = 5분, close 시각 기준 asof |
| 24h vol | t까지 1,440개 1m 수익률의 trailing std, cutoff는 F1 train(2021) 고정 |
| entry / exit | open[t+1] / open[t+1+h] |
| funding | 보유 중 8h 정산만 (D6-C 규칙) |
| fold 귀속 | entry 시각 |

미래 정보 사용 0건.

## 8. 해석

1. **실행 가능한 거래 단위에서도 점추정은 양수다.** D6-C FDN(-5.66bp)과 달리 C1 정의 그대로는 +10.27bp다. 다만 CI가 [-9.3, +30.0]이라 양의 edge를 주장할 근거가 되지 못한다.
2. **수익은 오른쪽 꼬리에서 나온다.** median +1.25bp, PF 1.15, 평균 이익과 평균 손실 크기가 거의 같다(+160 / -141). 상위 5%(81건)를 빼면 부호가 뒤집힌다. 정의된 warning(top 3, top 1%)은 통과했지만 오른쪽 꼬리 의존은 강하다.
3. **표본은 trade 수보다 훨씬 작다.** 1,603건이 179일에 몰려 있고, 연속 entry의 87%가 4시간 안에 겹친다. 블록 CI 폭(39.3bp)이 iid CI(21.5bp)의 약 1.8배인 이유다.
4. **약한 구간은 2022H2(F2)와 2025H2(F8)다.** F2는 trade 212건에 -54.5bp로 가장 크게 기여한 음수 fold다.
5. **Exit 연구 관점**: 4시간 안의 경로 진폭(MFE 중앙 +127, MAE 중앙 -103bp)이 net 평균보다 훨씬 커서, exit 규칙이 결과를 크게 바꿀 수 있는 구조다. 이 사실은 개선 가능성과 동시에 과최적화 위험을 뜻한다.

## 9. E2

**EXPLORATORY_ONLY.** E2를 하더라도 어떤 exit도 "수익 전략 확정"으로 결론 내리지 않고, 유망 후보는 forward shadow로만 확인한다. 이번 단계에서 E2는 실행하지 않았다.

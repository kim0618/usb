# US-B CRYPTO / C1 Post-Entry Lifecycle Research V1

작성 2026-10-03. 연구 전용. 동결 E1 executable C1 trade 1,603건을 그대로 재사용했다. C1 Entry,
C1 strength, regime filter, 운영/PAPER/LIVE 코드는 변경하지 않았다. 4H는 비교 benchmark이며 아래 상태의
고정 exit로 채택하지 않는다. 원본은 `c1_lifecycle_snapshots_v1.parquet`(8,015행)과
`c1_lifecycle_results_v1.json`이다.

## 0. 판정

**WEAK_LIFECYCLE_SIGNAL**. Premium normalization에는 반복되는 약한 정보가 있지만, 세 feature 조합은
시점·fold에 따라 뒤집히고 C1x보다 개선되지 않았다. **USABLE_FOR_FORWARD_SHADOW 후보 없음.**

## 1. PIT snapshot과 상태 정의

- 시점: entry 후 +5/+10/+20/+30/+60분. 해당 시점 직전까지 끝난 1분봉과 가용 derivative data만 사용.
- premium normalization: 기존 C1x와 같은 당일 PIT bucket `>= B3`; cutoff는 `[D-30,D)`만 사용.
- OI recovery: 현재 Bybit OI 1h log change `>= 0`; 원 데이터의 5분 availability delay 유지.
- recent high/low renewal: 마지막 5분 구간이 그때까지의 running extreme을 새로 포함하는지.
- 이후 수익: snapshot 다음 executable open부터 +30/+60/+120/+240분 open까지의 gross remaining return.
- net: 원래 C1 entry 체결부터 해당 horizon exit까지 기존 E1 비용 엔진으로 계산.

Threshold sweep은 하지 않았다. 후보는 다음 세 개뿐이며 나머지는 `UNCLASSIFIED`로 남겼다.

| 상태 | 정의 |
|---|---|
| HOLD | price >= entry AND 최근 5분에 running high 갱신 |
| EXIT_WATCH | price < entry AND premium 정상화/OI 회복 중 정확히 하나 |
| THESIS_BROKEN | price < entry AND premium 정상화 AND OI 회복 |

## 2. 관찰 시점별 핵심 결과

아래는 상태별 표본과 이후 **remaining gross mean(bp)** 순서 30m/1h/2h/4h다.

| snapshot | HOLD | EXIT_WATCH | THESIS_BROKEN |
|---|---|---|---|
| +5m | 834: +6/+10/+13/**+19** | 246: -2/+5/+15/**+35** | 24: +11/+1/-59/**-72** |
| +10m | 626: +2/+11/+11/**+18** | 294: +10/+18/+19/**+27** | 33: -11/-6/-32/**+15** |
| +20m | 427: +6/+14/+17/**+26** | 346: +13/+15/+18/**+24** | 56: +3/-2/-16/**-25** |
| +30m | 328: +4/+10/+12/**+20** | 328: +7/+12/+5/**+17** | 72: -6/0/-23/**-22** |
| +60m | 231: +1/-3/-5/**+5** | 357: +5/+1/+5/**+16** | 120: +7/+5/-7/**+9** |

- THESIS_BROKEN 4H clustered CI: +5m `[-112,-4]`(n=24), +10m `[-62,+54]`, +20m
  `[-70,+17]`, +30m `[-85,+35]`, +60m `[-22,+47]`. +5m만 0 아래이나 표본이 매우 작고 이후
  시점에서 재현되지 않는다.
- HOLD 4H clustered CI: +20m `[+7,+45]`, +30m `[0,+48]`로 가장 좋지만 +60m
  `[-21,+34]`로 소멸한다. +20m HOLD의 PF 1.43, win 56%, P5 -298bp이며 top 5% 제거 후
  mean -1bp다. 큰 winner 의존은 여전히 남는다.
- EXIT_WATCH는 조기 loser 상태가 아니었다. 모든 snapshot에서 4H mean이 +16~+35bp였고 CI는 0을
  걸쳤다. 따라서 이름은 경계 상태를 뜻할 뿐 exit 근거가 아니다.
- +5m의 recent high/low는 관찰창 자체가 5분이라 모두 true인 구조적 한계가 있다. 이 시점의 HOLD는
  사실상 price-above-entry 구분이다.

## 3. Price / Premium / OI 정보력

**Price: 약함.** Price-above의 이후 4H mean은 +5/+10/+20/+30/+60m에서 각각
19/20/19/22/10bp, below는 18/16/17/10/15bp다. +30m 외에는 분리가 작고 +60m에는 방향이 뒤집힌다.

**Premium: 세 축 중 가장 일관적이나 단독 exit 근거로는 부족.** normalized vs not-normalized의 이후
4H mean은 +5m `9 vs 21`, +10m `5 vs 22`, +20m `-3 vs 27`, +30m `7 vs 20`,
+60m `-3 vs 19bp`다. 방향은 5개 시점 모두 같지만 clustered CI는 넓다. C1x가 보여준 약한
정보와 일치한다.

**OI: 예상과 반대이며 thesis-break 신호가 아님.** recovered vs not-recovered의 이후 4H mean은
27 vs 17, 22 vs 17, 25 vs 15, 27 vs 10, 16 vs 9bp다. 모든 시점에서 OI 회복군이 더 좋았다.
따라서 `premium 정상화 + OI 회복`을 기계적으로 악화로 해석할 수 없다.

## 4. 조합 후보와 정의 가능 여부

1. **HOLD**: +20/+30m에서 유용한 winner-continuation 힌트이나 +60m decay와 tail 의존 때문에
   forward action으로 정의 불가.
2. **EXIT_WATCH**: 관찰/기록용 중간 상태로는 정의 가능하지만 청산 근거는 없음.
3. **THESIS_BROKEN**: +5/+20/+30m 방향은 나쁘지만 +10/+60m 및 fold에서 뒤집혀 확정 정의 불가.

즉 세 label은 연구 taxonomy로는 유지할 수 있으나, 현재 데이터로 HOLD/EXIT action mapping은 승인할 수 없다.

## 5. 단일 policy sanity와 C1x 비교

추가 threshold 없이 최초 THESIS_BROKEN snapshot에서 next open exit, 미발동은 E0 4H를 유지한
반사실 policy만 확인했다(발동 14.1%, 발동 평균 34분).

| | E0 4H | TB policy | C1x |
|---|---:|---:|---:|
| net mean | +10.27 | **+11.08** | **+15.35** |
| PF / win | 1.15 / 50.4% | 1.18 / 45.7% | 1.35 / 48.9% |
| clustered CI | [-9.3,+30.0] | [-7.0,+29.2] | [-2.6,+33.7] |
| P5 / worst | -329.7 / -1191.0 | -294.1 / -1191.0 | -201.3 / -844.7 |
| top 5% winner preservation | 100% | **94.9%** | 48.8% |
| positive folds | 7/9 | 8/9 | 8/9 |

- TB policy vs E0: +0.81bp, clustered CI `[-4.19,+6.05]`, bootstrap P(delta>0)=0.59.
- TB policy vs C1x: **-4.26bp**, CI `[-23.04,+13.72]`, P(delta>0)=0.33. **C1x보다 개선 없음.**
- TB policy는 C1x보다 큰 winner를 훨씬 잘 보존하지만 tail 개선은 더 작다. Worst loss는 E0와 동일해
  loser를 안정적으로 조기 구분하지 못했다.
- TB policy fold net은 F1~F9 중 F2만 음수이나, 2022 연간은 -7.2bp다. 연간 net은
  2023 +57.2, 2024 +15.4, 2025 +27.4, 2026 +17.2bp로 양수다. 조건부 상태 자체는 작은 표본과
  fold별 부호 반전이 많아 안정적이지 않다.

## 6. 최종 답변

- loser 조기 구분: **부분적 패턴만 있음**. +5/+20/+30m THESIS_BROKEN은 나쁘지만 지속성과 표본이 부족.
- large winner 보존: TB policy 94.9%로 양호. 다만 그 대가로 C1x의 tail 개선과 net을 따라가지 못함.
- C1x 대비 추가 정보: premium 외 price/OI 조합의 안정적 incremental value 없음.
- Forward shadow 부착: **없음**. 기존 C1x 진단을 대체하거나 새 action 상태를 추가하지 않는다.
- 다음 단계: 운영 변경 없이 STOP. 새로운 독립 기간의 forward 데이터가 쌓인 뒤, threshold를 바꾸지 않고
  동일 snapshot/state taxonomy를 그대로 재검증한다. 특히 premium normalized vs non-normalized 분리와
  +20/+30m HOLD, THESIS_BROKEN의 방향 재현 여부만 확인한다.

## 7. 한계

- 같은 E1/E2 1,603건 재사용으로 진정한 OOS confirmation이 아니다.
- 1,603 trade가 179 UTC days에 군집되어 clustered CI가 넓다.
- 상태별 표본, 특히 이른 THESIS_BROKEN이 작다.
- 상태는 설명용 taxonomy이며 causal exit 효과가 아니다.

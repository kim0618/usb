# C-ATTACK-V0 결과 V1

작성 2026-09-21.

```text
GATE-C-ATTACK-V0 = FAIL

C Stable / Directional   = CLOSED  (C-1..C-4, 변경 없음)
C Attack Right-Tail      = FAIL
Strategy C long-side 연구 = 종료
```

표본·PIT·모호성 모두 정상이므로 INCONCLUSIVE가 아니다. 가설이 기각된 FAIL이다.

## 0. 재현 정보

| 항목 | 값 |
| --- | --- |
| run | `catk0-55aacfcc4f4470a6a79e` (`data/runtime/strategy_c_attack/runs/`) |
| 선언 | `c_attack_rules_v1.json` canonical sha256 `0e3b4aa52ea757b625dc2b0bb7292eae176e34ab9934a829c0c1ecdb9e252785` (실행 직전 재계산 일치) |
| C-M0 후보표 | digest `64e57203a5942633f83eb0ad28fc2dd382c9e289ae58ec3e978f626a47d96ccb` 재현, 6,680행 |
| M_ONLY 출처 | 재현본 `candidate_status.parquet` sha256 `43a2cf09…`, `--status-source B`, ACCEPTED_B |
| bootstrap | block 10 / 10,000회 / seed 20260921 |
| 소요 | 39.8초 |

실행 전 체크(모두 PASS 후에만 수익 계산):

```text
Frozen rule checksum           = PASS
Candidate source               = PASS  (C_ATTACK_SOURCE_GATE)
C-M0 reproduction              = PASS
M_ONLY reconciliation          = PASS
PIT                            = PASS  (위반 0, 양성대조 검출)
Simulator scalar/vector parity = PASS  (400건 불일치 0)
Determinism                    = PASS
```

## A. Source 복원 (B안: 동결 C-E0 결정적 재현)

원본 PC에 접근할 수 없어 사용자 결정으로 SEC를 재수집했다(User-Agent는 사용자가 지정한 연락처).

| 항목 | 값 |
| --- | --- |
| 수집 | 기존 fetcher 그대로, 5 req/s, 캐시·재시도 로직 무변경 |
| HTTP | 2,384건 전부 200, 재시도 0, 287MB (원본 2,381) |
| CIK | 2,339/2,339 OK, 커버리지 미달 0 |
| 페이지·행 | 2,344 / 1,770,876 (원본과 동일) |
| 저장소 digest | run 기록 `c449deaa…` (원본 `bf4d744c…`와 바이트가 다르므로 다름) |

### 타임존: 동결 결론(UTC)을 고정했다

재수집 저장소로 감사를 다시 돌리니 40건 중 UTC 38건, ET 2건이 일치해 zone이 미결정이 됐다. 이때 C-E0 코드는 ET로
떨어진다. 원인은 둘이다.

1. 9/18 이후 공시가 표본 풀에 추가돼, 같은 seed로도 원본과 다른 40건이 뽑혔다.
2. 새 표본 중 2건(`0001104659-24-116299` 2024-11-12 06:55, `0000950170-25-105472` 2025-08-08 07:35)은
   `acceptanceDateTime`에 **ET 벽시계 시각이 찍혀 있다.** EDGAR 쪽 예외로 보인다.

그대로 두면 status가 EM 2,524 / M_ONLY 3,579가 되어 공표값과 어긋난다. 지시대로 동결 C-E0 결론(UTC, C_E0_RESULTS §2)을
고정했다. `strategy_c_e0` 코드는 한 줄도 바꾸지 않았다. 감사 판정 함수만 래퍼
(`data/runtime/strategy_c/e0/repro/run_frozen_utc.py`)에서 UTC를 반환하게 했고, 새 감사 결과는 summary에
`pinned_from_frozen_c_e0`와 함께 남겼다.

**새로 기록할 사실:** 표본의 5%에서 ET 스탬프 예외가 보인다. C-E0 판정에는 영향이 없다(아래 대조가 완전 일치).
그러나 이후 EDGAR를 쓰는 연구는 "전부 UTC"를 가정하지 말고 예외 검출을 넣어야 한다.

## B. C-E0 대조

status 8종이 공표값과 정확히 일치한다.

| status | 공표 | 재현 |
| --- | --: | --: |
| M_ONLY | 3,552 | 3,552 |
| EM | 2,550 | 2,550 |
| EM_NEGATIVE_RISK | 222 | 222 |
| EXCLUDED_MA_TARGET | 58 | 58 |
| UNKNOWN_FORM | 292 | 292 |
| UNKNOWN_MAPPING | 6 | 6 |
| UNKNOWN_PIT / UNKNOWN_COVERAGE | 0 / 0 | 0 / 0 |

행 단위 status digest는 원본 run에 기록된 적이 없다. 그래서 identity는 두 가지로 대조했다.
첫째, (signal_date, ticker) 집합이 동결 C-M0 6,680행과 완전히 같다.
둘째, 문서에 남은 수치 65개가 전부 일치한다.

- 매칭 N과 고유 종목: EM 2,465/1,498, M_ONLY 3,355/1,588, ENR 196/142
- 승률, 5D·10D 종가 초과와 95% CI(예: M_ONLY +0.319 [-0.588, +1.075], EM -0.256 [-0.726, +0.117])
- MFE10≥15% 적중률(30.28/20.07, 15.79/14.23)
- EM-M_ONLY 증분 -0.575 [-1.353, +0.295]
- 4개 구간의 EM/M_ONLY 건수, 클래스 건수(E3 2,007 / E5 1,093 / E1 183 / E2 13), EM 상위 5종목(UMAC, DELL, ADEA, APPS, AMN)
- W_D5/W_D1/W_D0 건수, EQM 모집단 표(EM 1,535종목, E3b 1,133행/807종목, E3b∩희석 25, E1 193, E2 14)
- `GATE-CE0 = FAIL`

설명한 차이는 1건이다. EQM 문서 §7의 "C-M0 고유 ticker 2,300"은 동결 원본 C-M 파일 자체가 2,362이므로, C-E0 주 표의
"Matched Control 고유 2,300"을 옮겨 적은 **문서 오기**다. 재현 차이가 아니다.

앞으로 쓸 canonical status digest(signal_date, ticker, status 정렬 CSV):
`b46ed8627b352e5e2ee23c32512cb4df1619914967bbcb85961ec76073af327d`

```text
C_ATTACK_SOURCE_GATE = PASS
```

## C. 복원 산출물과 Drive 게시

로컬 `data/runtime/strategy_c/e0/runs/ce01-234478e8f7ff27570e13/`와
Drive `1_US-B/research_artifacts/strategy_c/ce01-234478e8f7ff27570e13/`에 같은 파일을 두었고, 게시 후 `sha256sum -c`로
전부 OK를 확인했다.

| 파일 | sha256 |
| --- | --- |
| candidate_status.parquet | `43a2cf09b29ff0dba59a216aca874b4a4ee4bff48d563fa51b89684f5dc42054` |
| summary.json (재현 run `ce01-f7a521d7f50ffaffd0b0`) | `37e0eddad90a7d3c2d359d6e32acc87ddbcc8fa7d52407aebec740e12806f327` |
| run_identity.json | `ff6ec53b48d20b3030dfb58906f7ab63472058bccf28951da92ecc9e87003e3a` |
| manifest.json (SEC 저장소 manifest) | `db32a579659de004210f8c69781e29d0e58aecf6c5887c8d146da83bf02a0fe5` |
| rules.json / taxonomy.json | `b727f362…` / `5acb3c31…` (C-E0 선언 파일 바이트) |
| checksums.sha256 | 위 6개 |

폴더 이름은 원본 run id지만 내용은 **검증된 재현본**이다. `run_identity.json`이 그 사실과 run id가 다른 이유를 적는다.
C-E0 run id에는 SEC 저장소 digest가 들어가므로 재수집 저장소로는 같은 id가 나올 수 없다. 그래서 C-ATTACK의 A안
검증기(원본 run id 요구)는 이 파일을 거부하고, B안(재현본)으로만 받는다. 의도한 동작이다. SEC 원시 저장소(69MB)는
로컬에만 있다.

## D. Candidate / Control

| | N | 비고 |
| --- | --: | --- |
| M_ONLY 후보 | 3,552 | no_entry_bar 0, label_ca_suspect 10 제외 |
| 거래 유효 후보 | **3,542** | 1,642 종목, H1 모집단 |
| 매칭 후보 (셀 통제 ≥ 5) | **3,359** | H2 모집단, 미매칭 183 |
| 매칭 셀 통제 거래 | 108,021 | 3,400 종목, 후보 1건당 통제 중앙값 36 |

## E. First-hit (보수: 모호 = SL)

| Cohort | N | TP First | SL First | Neither | Ambiguous |
| --- | --: | --: | --: | --: | --: |
| **M_ONLY, C 15/7 (Primary)** | 3,542 | **21.46%** | **51.44%** | 26.34% | 0.76% |
| 매칭 통제 (셀 가중) | 3,359 | 16.81% | 43.35% | - | 0.11% |
| 매칭 통제 (풀링, 기술) | 108,021 | 12.47% | 37.42% | 50.06% | 0.05% |
| M_ONLY, A 10/5 | 3,542 | 28.15% | 57.37% | 12.45% | 2.03% |
| M_ONLY, B 15/5 | 3,542 | 18.27% | 61.97% | 18.58% | 1.19% |
| M_ONLY, D 20/7 | 3,542 | 14.29% | 53.13% | 32.10% | 0.48% |
| M_ONLY, E 20/10 | 3,542 | 16.88% | 40.03% | 42.74% | 0.34% |

TP_FIRST는 통제보다 4.7%p 많다. 그러나 SL_FIRST도 8.1%p 많다. **TP가 늘어난 것보다 SL이 더 늘었다.**
"셀 가중"은 후보마다 그 셀 통제의 평균을 붙인 값(H2와 같은 가중)이다. "풀링"은 매칭 셀 통제 거래를 한 번씩 센 값이다.

## F. Economics

| Cohort | Gross | Net Expectancy | Median | 승률 | Avg Win | Avg Loss | PF | MDD | 최장 연패 |
| --- | --: | --: | --: | --: | --: | --: | --: | --: | --: |
| **M_ONLY C (Primary)** | +0.230% | **-0.260%** | -7.45% | 38.7% | +10.59% | -7.12% | **0.940** | 9.42% | 42 |
| 매칭 통제 C (셀 가중) | +0.425% | -0.043% | - | - | - | - | - | - | - |
| 매칭 통제 C (풀링, 기술) | +0.361% | -0.044% | -1.39% | 44.2% | +7.67% | -6.16% | 0.987 | 6.97% | 160 |
| M_ONLY A 10/5 | +0.136% | -0.368% | -5.45% | 36.3% | +8.66% | -5.51% | 0.895 | 9.70% | 49 |
| M_ONLY B 15/5 | +0.235% | -0.276% | -5.45% | 31.9% | +10.90% | -5.52% | 0.926 | 8.43% | 39 |
| M_ONLY D 20/7 | +0.355% | -0.137% | -7.45% | 37.0% | +11.72% | -7.10% | 0.969 | 8.19% | 42 |
| M_ONLY E 20/10 | +0.578% | +0.112% | -2.54% | 43.1% | +11.83% | -8.77% | 1.022 | 8.83% | 39 |

- Primary H1: -0.260%, 95% CI [-1.105%, +0.463%].
- 비용을 빼기 전 gross도 +0.23%로 통제(+0.43%)보다 낮다. 비용이 없어도 후보가 통제를 이기지 못한다.
- MDD는 10-슬리브 비복리 포트폴리오 기준이고, 누적 -6.11%다.
- E(20/10)만 net이 양수(+0.112%)다. 사전등록상 진단용이고, 통제 대비 +0.093%p [-0.668, +0.669]로 우위도 없다.
  PF 1.022 < 1.10이며, 상위 10건이 이익의 130%를 차지한다. **E로 갈아타 PASS시키지 않는다.**
- 공격형 포트폴리오 시뮬레이션은 H1·H2 미통과라 사전등록대로 계산하지 않았다.

## G. Candidate minus Control (Gate 통계)

```text
H2 = candidate net - matched control net   (C 15/7, 보수, n = 3,359)
point  = -0.122%p
95% CI = [-0.711%p, +0.397%p]
99% CI = [-0.897%p, +0.560%p]
```

점추정이 음수이고 CI 하한이 0 아래다. 조건 2·3 모두 FAIL.

| 민감도 (C, 보수) | H1 | H2 point | H2 95% CI |
| --- | --: | --: | --- |
| Gross (비용 0) | +0.230% | -0.106%p | [-0.691, +0.408] |
| 비용 2배 | -0.749% | -0.137%p | [-0.731, +0.386] |
| 상폐 haircut 0% | -0.224% | -0.114%p | [-0.688, +0.385] |
| 상폐 haircut 100% | -0.344% | -0.140%p | [-0.796, +0.435] |
| 낙관 순서 (모호 = TP) | -0.090% | -0.001%p | [-0.553, +0.500] |
| CA 의심 10건 최악 포함 | -0.294% | - | - |

어떤 가정에서도 H2 CI 하한이 0 위로 올라가지 않는다.

## H. Gap Risk / Loss tail (C, 보수)

| 항목 | 후보 | 통제 (풀링) |
| --- | --: | --: |
| stop형 청산 | 1,849 (52.2%) | - |
| 갭으로 SL 관통 | **173 (4.9%)** | 4,120 (3.8%) |
| 갭 관통 평균 gross | -9.92% | - |
| 명목 SL 초과 손실 평균 | -2.92%p | -3.21%p |
| 명목 SL 초과 손실 합계 | -5.05 거래단위 (거래당 -0.14%p) | - |
| 최악 거래 (net) | -46.7% | -57.0% |
| 1% / 5% 분위 (net) | -12.0% / -8.0% | -11.0% / -7.8% |
| 상폐 청산 | 4 | 99 |

갭 손실만으로 거래당 기대값이 약 0.14%p 깎인다. 그래도 갭이 없었다고 가정한 H1은 여전히 음수(-0.12% 수준)다.

## I. Time Stability (C, 보수)

| Block | 기간 | N | Net Exp | PF | TP First | SL First | MDD | H2 excess |
| --- | --- | --: | --: | --: | --: | --: | --: | --: |
| 1 | 2025-09-18 ~ 12-11 | 900 | -1.08% | 0.77 | 19.7% | 54.0% | 8.0% | -1.02%p |
| 2 | 2025-12-12 ~ 2026-03-11 | 825 | -0.36% | 0.91 | 19.5% | 50.3% | 3.3% | +0.32%p |
| 3 | 2026-03-12 ~ 06-05 | 907 | -0.30% | 0.94 | 23.5% | 57.8% | 4.0% | -0.28%p |
| 4 | 2026-06-08 ~ 09-01 | 910 | **+0.69%** | 1.19 | 23.0% | 43.6% | 4.1% | +0.51%p |

H2 양수 구간은 2/4다(기준 ≥ 3). 순이익이 양수인 구간은 4번 하나뿐이고, 그 구간을 빼면 H1은 -0.59%다.
C-E0에서 본 "M_ONLY가 뒤로 갈수록 좋아진다"는 패턴이 여기서도 보이지만, 한 구간만으로는 전략이 되지 않는다.

## J. Winner / Loss Concentration

- 총 순손익이 음수(-9.2 거래단위)라 top-k 기여도는 정의되지 않는다(조건 9 FAIL의 직접 원인).
- 이익 상위 5종목(MXL, UCTT, APPS, BAND, FTAI)을 빼면 H1 -0.39%.
- 동시 보유: 신호일 237일, 하루 평균 14.9건(중앙값 12, 최대 242), 동시 포지션 중앙값 63, 95% 155.6, 최대 335.

## K. 기술통계 (Gate 아님)

- 보유 3D: net -0.56%, PF 0.83, H2 -0.24%p. 보유 5D: net -0.39%, PF 0.90, H2 -0.11%p.
- 전체 C-M0(C 15/7, n 6,663): net -0.69%, PF 0.84, H2 -0.32%p [-0.72, -0.01]. status별 net은 M_ONLY -0.26%,
  EM -1.14%, EM_NEGATIVE_RISK -0.84%, UNKNOWN_FORM -1.39%다. EM은 공격형 구조에서도 M_ONLY보다 나쁘다.

## L. Gate

| 조건 | 기준 | 실측 | 결과 |
| --- | --- | --- | --- |
| 1 H1 net expectancy | > 0 | -0.260% | **FAIL** |
| 2 H2 point | > 0 | -0.122%p | **FAIL** |
| 3 H2 95% CI low | > 0 | -0.711%p | **FAIL** |
| 4 Profit Factor | > 1.10 | 0.940 | **FAIL** |
| 5 Sleeve MDD | ≤ 25% | 9.42% | PASS |
| 6 Gap-aware | 구성상 | 반영 | PASS |
| 7 보수적 모호성 | 구성상 | 반영 | PASS |
| 8 Time stability | H2>0 ≥ 3/4 블록, 최고 블록 제외 H1>0 | 2/4, -0.59% | **FAIL** |
| 9 Concentration | top10 ≤ 50%, 상위 5종목 제외 H1>0 | 정의 불가(총손익 < 0), -0.39% | **FAIL** |
| 10 Sample | ≥ 300 / ≥ 300 / ≥ 100 | 3,542 / 3,359 / 1,642 | PASS |
| 11 PIT | 0 | 0 | PASS |

INCONCLUSIVE 규칙은 해당하지 않는다. 모호 비중이 0.76%로 25% 기준보다 훨씬 낮고, 낙관 순서에서도 조건 1~4·8·9가 모두
FAIL이다. 분봉으로 순서를 정밀화해도 판정은 바뀔 수 없다.

```text
GATE-C-ATTACK-V0 = FAIL
```

## M. 해석

질문은 "TP_FIRST의 큰 이익이 SL_FIRST와 갭 손실을 모두 치르고도 순기대값을 양수로 만드는가"였다. 답은 아니다.

M_ONLY는 통제보다 TP에 더 자주 닿지만(+4.7%p) SL에는 더 많이 닿는다(+8.1%p). 상방 꼬리가 두꺼운 만큼 하방
꼬리도 두껍다. C-V2B의 "변동성 확장, 방향 없음"이 실현 손익으로 그대로 옮겨졌다. 비대칭 exit는 변동성을 방향으로
바꾸지 못한다.

## N. 종료

```text
C Stable / Directional = CLOSED
C Attack Right-Tail    = FAIL
```

사전등록 `after_fail`에 따라 Strategy C의 long-side 연구를 종료한다. 금지 사항은 TP/SL/보유기간 변경, E pair 승격,
M_ONLY 재선별, 이벤트 subset, 기간·종목 제외, 분봉 정밀화로 살리기다. 변동성 중립이나 옵션 전략은 완전히 새로운
Strategy ID로만 연구할 수 있다.

## O. 산출물

| 대상 | 위치 |
| --- | --- |
| run | `data/runtime/strategy_c_attack/runs/catk0-55aacfcc4f4470a6a79e/` (summary.json, candidate_trades.parquet, rules.json) |
| 통제군 기술통계 | 같은 폴더 `control_descriptive_pairC.json` (스크립트 `data/runtime/strategy_c_attack/control_descriptive.py`) |
| C-E0 재현 | `data/runtime/strategy_c/e0/repro/runs_utc/ce01-f7a521d7f50ffaffd0b0/`, 대조 스크립트 `repro/reconcile.py` |
| 복원본 | 로컬 `data/runtime/strategy_c/e0/runs/ce01-234478e8f7ff27570e13/`, Drive `1_US-B/research_artifacts/strategy_c/ce01-234478e8f7ff27570e13/` |
| SEC 저장소 | `data/runtime/strategy_c/e0/raw/` (로컬 전용) |
| 이전 run | `catk0-5052cb48…` engineering only, `catk0-4fcdd000…` BLOCKED (코드 digest가 달라 run id가 다름) |

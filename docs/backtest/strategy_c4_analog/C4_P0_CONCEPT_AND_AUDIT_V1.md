# Strategy C-4 CONTEXTUAL_ANALOG_MOMENTUM_V0: P0 Concept Freeze + Read-only Audit

- 작성 2026-09-20 (C4-P0). 역할: Quant Research Lead + PIT Auditor.
- 상태: **CONCEPT FROZEN / RULES NOT YET FROZEN**. 규칙 JSON과 checksum은 C4-P2 종료 시점에 만든다.
  이 문서의 수치 중 "P2에서 확정"이라고 적힌 것 외에는 P0에서 고정한다.
- 이 문서는 어떤 C-4 수익률도 계산하기 전에 작성됐다. 본 것: 기존 C-1/C-2/C-3/D 문서·데이터 존재 여부·
  coverage·건수 분포. 보지 않은 것: C-4 형태의 아날로그 예측값, 그 어떤 부분집합의 forward return.

```text
STRATEGY C-1 (C-M)   = CLOSED, Directional FAIL, Volatility observed   (변경 없음)
STRATEGY C-2 (C-E0)  = CLOSED, FAIL                                     (변경 없음)
STRATEGY C-3 (EQM-V0)= CLOSED, FAIL                                     (변경 없음)
STRATEGY C-4         = NEW HYPOTHESIS, research_id = c4-analog-v0
STRATEGY D           = ACTIVE (GATE-D-ALPHA 실행 중), 규칙·코드 불변
```

C-4는 C-1~3의 재개가 아니다. `C_STATUS_CLOSED_V1.md` §2와 `EQM_V0_PREREGISTRATION_V1.md` §12의 금지 항목
(임계값·window·horizon 재조정, composite score, 사후 class/feature 선별, 트레이딩 백테스터)은 C-4 안에서도
그대로 유효하다. C-4가 기존 산출물을 쓰는 방식은 **읽기 전용 입력**뿐이다.

---

## A. C-4 Concept

핵심 가설:

> 현재 종목의 시장행동(모멘텀·거래량·변동성), 이벤트 존재, 이벤트 품질, 거래량 흐름(OBV), 시장 맥락을 한 벡터로
> 놓고, 과거에 이 벡터가 가장 비슷했던 K개 상황의 실제 5D 초과수익 분포를 예측값으로 쓰면, 고정 Rule보다
> 향후 종가 방향을 더 잘 선별하는가.

```text
Query = C-M0 후보 (D, ticker)
        ↓ Context vector x_D  (Layer A~E, 전부 [0,1] 스케일)
Library = D-20 이전의 base-eligible (d, ticker) 전체, 각 행에 x_d 와 realized excess_5/10
        ↓ Euclidean 최근접 K=50 (same ticker 제외, ticker cap 1, date cap 5)
Forecast S_5(D, ticker) = median( neighbor excess_return_5 )
        ↓
평가 = 같은 날 후보들을 S_5로 정렬했을 때 실제 excess_return_5 와의 Spearman IC (일별 평균)
```

Feature는 판단 규칙이 아니라 좌표다. `momentum AND event AND growth → BUY` 형태의 AND 규칙은 만들지 않는다.
`analog_score = weighted sum(...)`도 만들지 않는다. V0의 예측값은 이웃 outcome의 **중앙값 하나**다.

검증 대상은 오직 **Contextual Analog Selection Alpha의 존재 여부**다. Entry/Exit/Risk/Cost/Backtester는 범위 밖.

### 연구 ID·네임스페이스 (D와 물리적으로 분리)

| 항목 | 값 |
| --- | --- |
| strategy_id | `CONTEXTUAL_ANALOG_MOMENTUM_V0` |
| research_id | `c4-analog-v0` |
| 문서 | `docs/backtest/strategy_c4_analog/` |
| 코드 (P3에서 생성) | `backend/app/backtest/strategy_c4_analog/` (D 패키지 밖. D의 `code_digest`는 패키지 전체 `*.py` 해시라 파일 하나만 넣어도 D run identity가 깨진다) |
| 데이터 | `data/runtime/strategy_c4/` |
| run prefix | `c4feat1` / `c4lib1` / `c4nb1` / `c4sig1` / `c4eval1` |
| 판정 이름 | `GATE-C4-V0 = PASS / FAIL / INCONCLUSIVE` |

---

## B. Difference vs C-1 / C-2 / C-3

| | C-1 (C-M) | C-2 (C-E0) | C-3 (EQM-V0) | **C-4** |
| --- | --- | --- | --- | --- |
| 판단 메커니즘 | 고정 임계값 AND | 이벤트 존재 코호트 비교 | 매출 YoY bucket 코호트 비교 | **과거 유사 상황 K개의 실제 결과 분포** |
| Feature 역할 | 규칙 입력 | 코호트 라벨 | 코호트 라벨 | **거리 좌표** |
| 예측값 | 없음(후보/비후보) | 없음 | 없음 | 연속값 `S_5` (후보 내 순위 가능) |
| 평가 | 매칭 통제군 대비 초과 | 코호트 차이 | 코호트 차이 | **순위 상관(IC)·분위 스프레드** |
| 결론 재사용 | 유지(FAIL) | 유지(FAIL) | 유지(FAIL) | 세 결과의 Feature와 "무엇이 안 됐는가"를 좌표로만 재사용 |

C-1~3에서 확인된 사실 중 C-4 설계에 직접 반영한 것:

1. C-1: 종가 초과 ≈ 0, MFE·MAE 동반 확대 → 게이트에 **MAE 조건과 "MFE만 개선이면 FAIL"** 조건을 넣는다(§I).
2. C-1 V2B: C-M 후보는 방향이 아니라 변동성 군집 → **D일 True Range를 좌표에 포함**해 이웃이 같은 변동성 상태에서
   뽑히게 한다. `C_STATUS_CLOSED_V1.md` §2가 변동성 재연구 최소요건으로 요구한 "D일 TR 매칭"과 같은 방향이다.
3. C-2: 이벤트 존재는 오히려 M_ONLY보다 나빴다 → `event_present`를 positive로 해석하지 않는다. 좌표에는 넣되,
   baseline B2를 **"이벤트 없음"** 순위로 둔다(§I).
4. C-3: 8-K에는 XBRL fact가 0건, 규모는 정기보고서 매출 YoY만 → Layer C는 `revenue_yoy` 계열만 쓰고 계약금액·계약기간·
   텍스트 파싱은 V0에서 제외한다.

---

## C. Difference vs Strategy D

```text
D   = CHART ANALOG      (가격 시계열 창의 모양이 비슷했던 과거)
C-4 = SITUATION ANALOG  (상황을 요약한 스칼라 벡터가 비슷했던 과거)
```

| 축 | D (`HISTORICAL_ANALOG_V1`, 규칙 `680bf113…`) | C-4 |
| --- | --- | --- |
| 매칭 대상 | 길이 W+1 z-경로 / 길이 W 누적로그수익 경로 (W=20/40/60) | 길이 F의 스칼라 벡터(모멘텀·변동성·거래량·이벤트·품질·OBV·시장맥락) |
| 거리 | A: Pearson, B: Euclidean (경로 위) | Euclidean (rank 벡터 위), cosine은 secondary |
| Query 모집단 | 매일 eligible 유니버스에서 해시 무작위 300종목 | **C-M0 후보 행** (모멘텀 상황에 조건부) |
| Library | stride 5 세션, 라벨 horizon별 별도 | 매 세션, 5D·10D 라벨 유효 행 전체 |
| Embargo | `d + h <= D - W` | `d <= D - 20` (h_max=10 < 20이므로 이 하나로 라벨 embargo까지 충족) |
| 시장 외 정보 | 없음 (가격·거래량만) | SEC 이벤트, XBRL 매출, OBV, SPY/breadth |
| 질문 | "이 차트 다음엔 무엇이 왔나" | "이 상황(맥락 포함) 다음엔 무엇이 왔나" |

**중첩 경고.** D의 baseline `N2a`는 5개 스칼라 feature(`return_W, return_1d, return_5d, realized_vol_W,
distance_to_W_high`)의 per-date percentile rank 위에서 같은 cap·embargo로 kNN을 돈다. C-4의 **F0(Market Only)
family는 이것과 구조적으로 거의 같다.** 따라서 C-4가 F0만으로 PASS하고 F1~F3가 F0를 넘지 못하면 그 결과는
"D의 N2a 변형"이지 C-4의 새 정보가 아니다. 이를 게이트 조건 10(§I)으로 명문화한다: **F3 IC가 F0 IC보다 커야 한다.**

D 코드 재사용 원칙 (`D_REUSE_MATRIX_V1.md` 방식 준용):

- **수정 금지·추가 금지**: `backend/app/backtest/strategy_d_analog/` 전체. 특히 지금은 D4 `GATE-D-ALPHA` run
  (`deval1-99574380a3d0`, PID 18736, 12:54 시작, 7개 조합 중 2개 완료)이 이 패키지의 `code_digest` 위에서 실행 중이다.
  파일 하나를 바꾸면 run id가 바뀌어 D1→D2→D3→D4 parent chain이 끊긴다. **D4가 끝나기 전에는 C-4의 무거운 작업도
  실행하지 않는다**(D4 RSS 2GB, OPENBLAS 8 threads).
- **import만 허용(무수정)**: `resample.{block_indices, interval, paired_difference}`, `metrics.{spearman, quintile_baskets,
  time_blocks, concentration, DailySeries}`, `similarity.{score_block, library_sq_norm, Metric.EUCLIDEAN}`
  (스칼라 행렬 `(q,F)×(L,F)`에 그대로 동작, N2a가 이미 이 경로를 쓴다), `neighbor_search.{greedy_caps, select,
  select_full_sort, drop_same_symbol}`, `pit.{EmbargoView, assert_finite, assert_library_index, assert_neighbors,
  InvariantLog}`, `artifacts.*`(digest·COMPLETE.json), `identity.{canonical_bytes, code_digest(package_dir=C4)}`,
  `source.{load_daily_history, read_set_digest, AsOfView}`, `label_extension.compute_validity`,
  `labels.{compute_labels, compute_extremes, gather}`, `features.percentile_rank`.
- **복사해서 자체 보유**: `features._average_rank`(15줄; `metrics`→`features`→C `Panel`→`app.market.calendar`→kiwoom
  client로 이어지는 import 사슬 회피), `library.FigiCoder`(인스턴스 공유 시 code가 삽입순서 의존).
- **패턴만 따르고 새로 작성**: rules loader + `DECLARED_RULES_CHECKSUM`(불일치 시 거부), gate 조건표, run prefix,
  출력 디렉터리, S 집계기, encoder. `resample.both_levels`는 `FAMILY_SIZE=14` Bonferroni가 박혀 있어 `interval(alpha=…)`를
  직접 쓴다. `identity.query_sample/n1_seed`는 seed `20260917` 리터럴이 박혀 있어 쓰지 않는다.
- **의존 digest 기록**: C-4 run identity에 `strategy_d_analog` 패키지의 `code_digest`를 `dependency_digests`로 기록한다.
  D가 나중에 `labels.py`를 바꾸면 C-4 재현성이 깨지므로 그 사실을 identity가 드러내야 한다.

---

## D. Data Depth Audit (read-only, 2026-09-20)

### D-1. 일봉 깊이: 정확히 2년, 그 이전은 어디에도 없다

| 항목 | 값 | 근거 |
| --- | --- | --- |
| 공급자·플랜 | Massive Stocks Basic(무료), **롤링 2년**, T-1, `adjusted=false` | `docs/MASSIVE_DATA_SOURCE_SPIKE.md:20`, `COMMON_HISTORICAL_STORE_V2.md` §1·§7 |
| usable grid | **2024-09-17 ~ 2026-09-16, 501 XNYS 세션**, 내부 gap 0 (주중 결측 21일 전부 휴장일) | `strategy_c/runs/cmsel1-…/summary.json data_audit` |
| 창 밖 증거 | `2024-09-16.json.gz` = HTTP 403 `NOT_AUTHORIZED` stub | `C_DATA_FEASIBILITY_V1.md:42` |
| 2024-09 이전 가격 | **로컬·Drive·API 어디에도 없음.** 파일명 날짜 전수 스캔 최소값 2024-09-16, Drive 연도 prefix 2024/2025/2026뿐 | `/mnt/g/내 드라이브/1_US-B/market_data` 스캔 |
| API 재확보 | 불가. 창 시작 이전 요청은 403 또는 **무음 truncation** | V2 §1 |
| 유료 5~10년 | "네 전략 검증 후 구매 예정" | V2 §7 |
| 캐시 = 정본 | 매 ET 일자마다 가장 오래된 세션이 API에서 사라진다. 로컬 `strategy_c/raw/grouped`(172MB)와 Drive `grouped_daily`(170MB)가 유일한 복사본 | `D_PIT_CONTRACT_V1.md:160` |

→ 사전등록 초안 §5의 **Option A(2020~2024 Library / 2025~2026 OOS)는 불가능하다.**

### D-2. 기존 2년 안에 "안 본 구간"은 없다

| 연구 | outcome을 본 신호일 범위 | 모집단 |
| --- | --- | --- |
| C-M V1 primary | 2025-09-18 ~ 2026-09-01 (240일, idx 251..490) | C-M0/M1/M2 후보 |
| C-M V1 secondary | 2024-12-11 ~ 2026-09-01 (431일, idx 60..490) | C-M0/M1 후보 |
| C-E0 / EQM-V0 | 2025-09-18 ~ 2026-09-01 | C-M0 후보 코호트 |
| D (D4 진행 중) | 2025-10-01 ~ 2026-08-18 (221일) | 유니버스 무작위 300/일 |

C primary가 idx 490에서 끝나는 이유가 `N-10`(10D 라벨)이므로 idx 491..500(2026-09-02~16)에는 라벨이 없다.
즉 **기존 grid 안에 C-4 게이트에 쓸 수 있는 미관측 신호일은 0일이다.** 기존 240일은 `RESEARCH / ENGINEERING /
SANITY CHECK ONLY`로만 쓴다(§H).

### D-3. 재사용 가능한 저장소

| 저장소 | 범위·규모 | C-4 재사용 |
| --- | --- | --- |
| C raw grouped daily | 501세션, 세션당 5,053~5,242 ticker, `T,o,h,l,c,v,n,vw,t` (**`vw`·`n`은 어떤 feature도 안 씀**) | Layer A·D·E 전부 여기서 계산 |
| CS reference snapshot | 분기 8개(2024-10-01 ~ 2026-07-01), `cik, primary_exchange, composite_figi` 포함 | 유니버스·CIK 매핑·FIGI |
| splits | 3,328건 | 가격 factor `F(t)` |
| `features_base_eligible.parquet` | 1,111,641행 × 26열, 431 signal_date(2024-12-11~2026-09-01), 4,062 ticker, 라벨 없음 | Layer A 원천(그러나 P3에서 as-of 재계산; 이 파일은 대조용) |
| C-E0 SEC submissions store | **2,339 CIK**(C-M0 primary 후보의 CIK만), 1,770,876 filing rows, 접수 1994~2026-09-18, digest `bf4d744c…` | Layer B. **Library 전체(패널 6,341 ticker)를 덮으려면 CIK 약 4,000개 추가 수집 필요** |
| `candidate_status.parquet` | 6,680행(primary C-M0), status·event_types·negative_risk 등 21열 | Layer B 대조용 |
| EQM XBRL companyfacts | 834 CIK, 136MB gz(원본 2.09GB), digest `fcdd2e5f…` | Layer C. Library 전체를 덮으려면 CIK 수천 개 추가(추정 원본 15GB, gz 1GB; Drive 여유 8.6GB라 **로컬 전용**) |
| `quality_rows.parquet` | 2,772행(EM+EM_NEGATIVE_RISK), OBSERVABLE 1,100 | Layer C 대조용 |
| SPY | grouped에 501/501 존재, `Panel.column("SPY")` | Layer E |
| 분봉 | 30종목×2년(A 유니버스+SPY), B scope 3,883종목×4개월(2026-05-18~, 수집 8.6% 진행) | CVD_PROXY(F4)에 부족, §E-5 |
| D analog library | `dneigh1-fe0362523739` 1.2GB, 가격경로 행렬 | **재사용 불가**(경로 행렬, C-4는 스칼라). 라벨 정의만 차용 |

### D-4. 저장소 상태 경고 (C-4와 무관하게 존재하는 위험)

- `git status`: `strategy_d_analog/`, `strategy_eqm_v0/`, D1~D3 결과 문서, EQM 문서, B 코드가 **전부 untracked**다.
  로컬 HEAD = 원격 `00f8233`으로 push 문제는 없다. A전략에서 겪은 "다른 PC에 코드가 없다" 함정과 같은 구조이므로
  C-4를 시작하기 전에 사람이 commit/push해야 한다(git 작업은 자동화하지 않는다, `WORKFLOW.md` §5).
- `docs/backtest/research_universe_v2.json`은 여전히 git 미추적(C-4에는 불필요, A 정산 분봉에만 필요).
- 티커 `CON`은 Windows 예약어라 Drive/WSL에 디렉터리를 만들 수 없다. C-4 Library에서 `CON`은 grouped daily에서만
  읽으므로 영향 없음. 단 per-symbol 저장이 필요해지면 `unsupported_path.json` 규약을 따른다.

---

## E. Feature Feasibility

모든 Feature는 D일 이전 데이터만으로 계산되고, 가격 수준·규모를 직접 쓰지 않는다. 표기: `P=close/F`, `H,L,V` 동일 규약,
`TR(t)=max(H,P(t-1))-min(L,P(t-1))`.

### E-1. Layer A: Market Behavior (C-1 재사용, `strategy_c_selection/features.py` 정의 그대로)

| feature | 정의 | 기존 여부 | C-4 처리 |
| --- | --- | --- | --- |
| `return_1d` `return_3d` `return_5d` | `P(D)/P(D-k)-1` | 있음 | rank |
| `rvol_20` | `V(D)/mean V(D-20..D-1)` | 있음 | rank |
| `volume_z_20` | z-score(D-20..D-1, ddof1) | 있음 | rank |
| `dollar_volume_change` | `dollar_volume/adv20-1` | 있음 | rank |
| `adv20_dollar` | mean(close·volume, D-20..D-1) | 있음 | rank (규모 맥락; raw 금액은 쓰지 않는다) |
| `atr_pct` | mean TR(D-14..D-1)/P(D-1), **D 제외** | 있음 | rank |
| `true_range_pct_d` | `TR(D)/P(D-1)` | **없음(신규, C-V2B가 사후기술로만 계산)** | rank. C-1 V2B 변동성 결론을 좌표에 반영 |
| `atr_expansion` | `true_range_pct_d / atr_pct` | 없음(신규) | rank |
| `gap_d` | `open(D)/P(D-1)-1` (split factor 적용) | 없음(C-V2 `overnight_gap`은 D+1 outcome이라 다른 것) | rank |
| `rs_spy_1d` `rs_spy_5d` | `return_kd - spy_return_kd` | 있음 | rank |
| `distance_60d_high` | `P(D)/max H(D-59..D)-1` | 없음(신규) | rank. **52W 대체**: `distance_52w_high`는 m2 warmup(251세션) 때문에 secondary 창 42%가 NaN이라 Library 깊이를 절반 이하로 줄인다 |
| `distance_52w_high` | `P(D)/max H(D-251..D)-1` | 있음 | **F0 본체에서 제외**, ablation `F0+52W`(m2 부분집합)에서만 |

Layer A 합계 13개(52W 제외). 전부 기존 캐시에서 API 호출 0으로 계산 가능.

### E-2. Layer B: Event Context (C-E0 재사용, taxonomy `6ee5a16a…`, window `W_PRIMARY=[open(D-2), close(D))`)

| feature | 정의 | 처리 |
| --- | --- | --- |
| `event_present` | W_PRIMARY에 E1/E2/E3/E5 ≥1 | 0/1 |
| `event_e1` `event_e2` `event_e3` `event_e5` | class one-hot | 0/1 각각 |
| `event_count_capped` | `min(event_count, 3)/3` | [0,1] |
| `negative_risk_flag` | W_PRIMARY에 E4a~E4d | 0/1 |
| `recent_dilution_20` | `[open(D-20), close(D))`에 E4 | 0/1 |
| `event_age_sessions` | **V0 제외.** W_PRIMARY가 3세션뿐이라 age ∈ {0,1,2}로 정보량이 없고, 창을 넓히는 것은 C-E0 window 변경에 해당해 하지 않는다. 구현체도 없다(`EQM_V0_CONCEPT_V1.md` §4 희망사항뿐) | 제외 |
| `event_type`(범주) | one-hot로 위에 흡수 | |

행 처리: C-E0 cohort 규칙과 동일하게 `EXCLUDED_MA_TARGET`, `UNKNOWN_MAPPING`, `UNKNOWN_COVERAGE`, `UNKNOWN_PIT`,
`UNKNOWN_FORM`(6-K/20-F/40-F) 행은 **Query와 Library 양쪽에서 제외**한다(C-M0 primary 기준 6,680 중 356행, 5.3%).
이벤트 존재는 positive로 해석하지 않는다(C-2 결론).

Feasibility 제약: 현재 SEC store는 C-M0 primary 후보의 CIK 2,339개만 덮는다. Library(base-eligible 전체, 패널 6,341
ticker)에 Layer B를 붙이려면 **패널 전체 CIK의 submissions 수집이 P1 선행조건**이다(EDGAR 무료, 5 req/s 운용, 기존
2,381요청/286MB 기준으로 약 4,000요청 추가, 1시간 이내 추정). 기존 store는 재수집하지 않고 **증분 store**로 붙인다.

### E-3. Layer C: Event Quality (EQM-V0 재사용, E3b 한정)

| feature | 정의 | 처리 |
| --- | --- | --- |
| `quality_observable_flag` | `quality_status == OBSERVABLE` | 0/1 |
| `revenue_yoy_rank_past` | `revenue_growth_yoy`의 **과거 전용 expanding percentile**: 날짜 d < D인 observable Library 행 분포 안에서의 백분위 | [0,1]; 비관측이면 **0.5** (flag=0이 구분) |
| `revenue_yoy_negative` | `revenue_growth_yoy < 0` | 0/1; 비관측 0 |
| `periodic_event_flag` | W_PRIMARY에 10-Q/10-K/10-KT | 0/1 |
| `financing_event_60d` | `[D-60 cal, D]`에 E4a~E4d | 0/1 |

같은 날 cross-section rank를 쓰지 않는 이유: observable 행이 하루 평균 4.6개(1,100/240)라 일별 rank가 무의미하다.
과거 전용 expanding 분포는 PIT를 만족한다(미래 평균/표준편차 사용 금지 원칙 충족).
계약 금액·계약 기간·실적 서프라이즈·consensus는 데이터가 없으므로 제외(C-3 확정 사실, 재조사하지 않는다).

Feasibility 제약: Library 행에 Layer C를 붙이려면 정기보고서 이벤트가 있는 CIK의 companyfacts가 필요하다. 현재 834 CIK.
P1에서 **필요 CIK 수와 바이트를 먼저 측정**하고(추정 최대 약 5,000 CIK / 원본 15GB / gz 1GB), Drive 용량(8.6GB 여유)
때문에 로컬 전용 store로 두되 digest를 문서에 기록한다.

### E-4. Layer D: Volume Flow (신규, 전부 Rule-based, 일봉 캐시만 사용)

저장소 전체 grep 결과 OBV/CVD/money flow 구현은 **0건**. 아래는 신규 정의다.

```text
OBV(t) = OBV(t-1) + sign(P(t) - P(t-1)) * V(t)        (sign(0) = 0)
obv_slope_k     = (OBV(D) - OBV(D-k)) / sum V(D-k+1..D)        k in {5, 10, 20}, 값역 [-1, 1]  (부호 있는 거래량 비율)
obv_zscore_20   = (OBV(D) - mean OBV(D-20..D-1)) / std_ddof1 OBV(D-20..D-1)
price_obv_divergence_20 = rank(P(D)/P(D-20)-1) - rank(obv_slope_20)   (같은 날 cross-section rank 차, [-1,1] → (x+1)/2)
clv_d           = (2P - H - L)/(H - L) at D          (H == L 이면 0)
vwap_rel_d      = P(D)/vw(D) - 1                    (grouped daily의 vw 필드; 결측이면 행 제외)
```

`obv_breakout_flag`는 임계값 발명이 필요해 V0에서 제외. `obv_slope_k`는 거래량 합으로 나눠 규모 중립이다.
P1에서 `vw` 결측률을 감사한다(결측률 > 1%면 `vwap_rel_d` 제외를 P2 동결에 반영).

### E-5. CVD / CVD_PROXY

```text
REAL_CVD = UNAVAILABLE
```

Massive Basic에는 체결 방향(trade classification) 데이터가 없다. 분봉 기반 `CVD_PROXY`(분봉 종가 vs 시가 부호로 up/down
volume 분류)는 정의 가능하나, 분봉 coverage가 A 유니버스 30종목×2년 + B scope 3,883종목×4개월(2026-05-18~, 수집 8.6%)뿐이라
C-M0 후보 2,362 ticker의 Library 기간을 덮지 못한다. **F4는 Forward Shadow 구간에서만 기술통계로 후속**하며 게이트
입력이 아니다. 실제 CVD라고 부르지 않는다.

### E-6. Layer E: Market Context

| feature | 정의 | PIT | 처리 |
| --- | --- | --- | --- |
| `spy_return_1d` `spy_return_5d` | SPY 종가 수익률 | 가능 | 과거 전용 expanding percentile (날짜 수준 값) |
| `spy_atr_pct` | SPY mean TR(D-14..D-1)/P(D-1) | 가능 | 동일 |
| `breadth_1d` `breadth_5d` | 그날 base-eligible 유니버스 중 `return_kd > 0` 비율 | 가능(as-of D 패널만 사용) | 동일 |
| `sector_return` | | **PIT sector 매핑 없음**(C PIT 계약 §3-4와 동일 사유) | 제외 |
| `market_breadth` 외 지표 | | 다른 원천 없음 | 제외 |

날짜 수준 feature는 같은 날 모든 query에 동일하므로, 이웃 선택에서 "비슷한 시장 국면의 날"을 고르는 역할만 한다.

### E-7. Feature Family (사전등록)

| family | 구성 | 차원 |
| --- | --- | --- |
| **F0** Market Only | Layer A (52W 제외) | 13 |
| **F1** | F0 + Layer B | 13 + 8 = 21 |
| **F2** | F1 + Layer C | 21 + 5 = 26 |
| **F3** Full Context (**Primary**) | F2 + Layer D + Layer E | 26 + 6 + 5 = 37 |
| F0+52W | F0 + `distance_52w_high`, m2 부분집합 | 14 (ablation only) |
| F4 | F3 + CVD_PROXY | Forward 기술통계 only |

Family 목록과 각 feature 정의는 P2 동결 후 결과를 보고 넣고 빼지 않는다.

### E-8. Normalization (전부 [0,1], 등가중)

- 종목 수준 연속 feature: **같은 날 base-eligible 유니버스 안에서의 percentile rank** `(avg_rank-1)/(n-1)` (D의 `percentile_rank`
  재사용). 그날 cross-section만 쓰므로 미래 통계량이 들어갈 수 없고 가격 수준·규모에 불변이다.
- 날짜 수준 feature(Layer E): 과거 날짜 전용 expanding percentile.
- 이진 flag: 0/1 그대로. one-hot도 0/1.
- 결측 규칙: Layer C 비관측 = 0.5 + flag 0. 그 외 결측은 행 제외(`VECTOR_UNDEFINED` 카운트).
- 가중치: 모든 차원 1. **feature weighting·learned metric은 금지**(ML 금지 원칙).

---

## F. PIT Contract (C-4)

C `C_PIT_CONTRACT_V1.md`, C-E0 `pit` 블록, EQM `pit_audit`, D `D_PIT_CONTRACT_V1.md`를 그대로 상속하고 아래를 추가한다.

1. **읽기 경계.** feature 계산은 `AsOfView(D)`를 통해서만 패널을 읽는다(D `source.AsOfView` 재사용). 라벨 코드는 feature
   코드와 모듈 분리, AST 테스트로 강제(C 방식).
2. **이벤트 시각.** EDGAR `acceptanceDateTime`은 UTC(40/40 대조). 매 실행 저장된 40건으로 재확인(C-E0 P1).
   `effective_time >= close(D)`인 filing은 어떤 feature에도 못 들어간다. CIK는 `as_of <= D` 스냅샷.
3. **XBRL.** fact의 `accn`은 그 행의 W_PRIMARY 정기보고서 accession과 일치해야 한다. 다른 제출물의 fact 사용 0건.
4. **Library 자격.** 이웃 후보 `(d, ticker)`는 `d <= D - 20`(세션 index). `D - 20 < d`인 이웃 1건이라도 나오면 위반.
   같은 ticker 전 기간 제외, `composite_figi` 동일(둘 다 non-null)이면 제외(D `same_symbol_policy` 그대로).
5. **Normalization PIT.** 종목 rank는 날짜 D cross-section만, 날짜 rank와 `revenue_yoy_rank_past`는 `d < D` 분포만 사용.
6. **라벨.** D `d-label-value-v1` 계약 그대로: `P0 = open(D+1)/F(D+1)`, 창 `D+1..D+h`, `close_return_h` clip [-1,1],
   `excess_return_h = close_return_h - median(같은 날 라벨 유효 eligible 전체)`, `mfe_h/mae_h`는 clip 없음, 유효성
   `compute_validity`(D+1 bar, D+h bar, CA suspect). Query와 Library에 **동일 정의**. 매칭 통제군 셀(C 방식)은 쓰지 않는다:
   이웃의 outcome이 이미 "조건부 기대값"이므로 셀 매칭을 겹치면 정의가 이중이 된다.
7. **감사(mutation test, 12개 감사일, D `pit_audit` 패턴).**
   - truncation: `truncate(D)`로 재계산한 feature·이웃 목록·S가 cell-by-cell 일치
   - future_bars / future_splits: D 이후 봉·분할 훼손 시 D행 불변
   - later_disappeared_removed: 마지막 세션에 봉 없는 ticker 삭제 시 D행 불변(생존편향 0)
   - event shift +1 세션: window 경계 행만 변함, `unexplained = 0`
   - positive control: `close(D)+1s` 합성 filing 25건 주입 → 전부 검출; D-10 high×100 → `distance_60d_high` 변함
   - embargo positive control: `d = D-19` 합성 Library 행 주입 → 이웃에서 배제됨
   - determinism: 두 번 실행 digest 동일
8. **위반 카운터.** `pit_violations_total = 불일치 셀 + (determinism 실패) + (positive control 미검출)`. 게이트 조건 1은 `== 0`.
   outcome 계산 후 위반이 발견되면 이 선언의 게이트는 FAIL이고 재시도는 새 선언으로만.
9. **Forward Shadow 특칙.** OOS 구간 데이터는 사후 일괄 수집이 허용된다(retroactive fetch). PIT는 as-of view와 acceptance
   time으로 보장되며, 위 mutation test가 OOS 감사일에도 동일하게 적용된다. 단 CS 스냅샷은 분기 시점에 실제로 받아 둔다
   (reference endpoint는 창 제한이 없지만 관행 유지).

---

## G. Analog Method Preregistration

P0에서 고정(굵게)과 P2에서 수치 확정(기울임)을 구분한다. **어느 쪽도 결과를 본 뒤 바꾸지 않는다.**

| 항목 | 값 | 고정 시점 |
| --- | --- | --- |
| Query 모집단 Q | **C-M0 primary 후보 행**(`return_1d>=0.03 ∧ return_5d>=0.05 ∧ rvol_20>=2.0`, C-M0 규칙 `c769aea5…`을 모집단 정의로만 재사용, 재조정 없음) − §E-2 제외 status | P0 |
| Library L | **base-eligible ∧ ¬ca_excluded ∧ 5D·10D 라벨 유효 ∧ 벡터 정의됨** 인 모든 `(d, ticker)`, 매 세션, idx ≥ 60 | P0 |
| Embargo | **`d <= D - 20`** (세션) | P0 |
| Same-ticker | **전 기간 제외 + FIGI 동일 제외** | P0 |
| Caps | **ticker cap 1, library date cap 5** (D 값 그대로) | P0 |
| 거리 Primary | **Euclidean on [0,1] 벡터** | P0 |
| 거리 Secondary | cosine (기술통계, 게이트 아님) | P0 |
| **K Primary** | **50** | P0 (D와 동일; Library 규모 1.1M행이면 coverage 문제 없음. P1 coverage 감사에서 INSUFFICIENT ≥5%로 나오면 K를 **낮추지 않고** INCONCLUSIVE 조건으로 처리) |
| K Secondary | 25, 100 (기술통계) | P0 |
| INSUFFICIENT | 이웃 < 50 → query 제외, 비율 기록 | P0 |
| Forecast S | **`S_5 = median(neighbor excess_return_5)`** | P0 |
| 보조 요약 | mean_5, positive_rate_5/10, median mfe_5/10, median mae_5/10, `mfe10_ge_15` hit rate, `S_10` | P0 |
| Horizon Primary | **5D** (C 계열과 동일) | P0 |
| Primary family | **F3** | P0 |
| Primary metric | **mean daily Spearman IC(S_5, realized excess_return_5)**, 유효 query ≥ 8인 날만 IC 계산 | P0 |
| 분위 | 일별 S_5 5분위(동률은 ticker 오름차순) | P0 |
| Bootstrap | 신호일 moving block, *block 10, 10,000회*, 양측 95% | seed는 P2에서 신규 부여 (C-M 20260917, C-E0 20260918, EQM 20260920, D 20260917 재사용 금지) |
| 다중검정 | Primary 검정 1개(F3·5D·IC). 조건 3~10은 같은 검정의 부수 조건, F0~F2·K·cosine·10D는 기술통계 | P0 |
| Baselines (같은 Q 위에서 IC) | B1 `return_5d` rank(단순 모멘텀) / B2 `event_present == 0`(이벤트 없음; C-2 결과 방향) / B3 EQM `material` flag / **B4 F0-only analog `S_5`** (≈ D N2a) | P0 |

일별 IC 하한 8의 근거(결과가 아닌 건수 분포): C-M0 primary 후보 일별 건수 mean 27.8 / median 23 / min 2 / p10 8,
`>= 8`인 날 92.5%, `>= 10`인 날 86.7%.

---

## H. OOS Split

### H-1. 결정: Option B Forward Shadow (Option A 불가)

```text
ANALOG LIBRARY (development)   2024-09-17 .. 2026-09-16   (501 sessions, USB-HIST-V1/V2, 이미 관측된 구간)
IN-SAMPLE SANITY ONLY  (P4)    query 2025-09-18 .. 2026-09-01 (C primary 240일)  → 게이트 판정 금지
OOS FORWARD SHADOW     (P6)    query 2026-09-17 .. (Library는 매 D마다 d <= D-20 로 확장)
```

- Library에 이미 본 구간을 쓰는 것은 허용된다. 누설 위험은 **query outcome을 보고 규칙을 고르는 것**이지 과거 outcome을
  이웃 라벨로 쓰는 것이 아니다. 규칙은 P2에서 동결되고 P4 이후 변경 불가.
- Forward 구간 query에는 T-1 지연으로 D+1 04:00 ET 이후 feature가 확정되고, 라벨은 D+10 세션 종가 다음 날 확정된다.

### H-2. 게이트 시점 (달력, XNYS 휴장 근사)

| 누적 신호일 | 마지막 신호일 | 라벨·T-1 완결 | 용도 |
| --- | --- | --- | --- |
| 60 | 2026-12-10 | 2026-12-28 | 중간 점검(기술통계, `SHADOW_CHECKPOINT_60`, 판정 없음, 규칙 변경 없음) |
| **120** | **2027-03-10** | **2027-03-25** | **GATE-C4-V0 1차 판정 시점** |
| 180 | 2027-06-04 | 2027-06-22 | 120일에서 P-검사만 미달(INCONCLUSIVE)이면 **1회 연장** 후 최종 |
| 240 | 2027-08-31 | 2027-09-16 | 사용 안 함 |

판정은 120일 시점에 **한 번** 한다. 120일 시점의 결과가 조건 3~10 중 하나라도 FAIL이면 180일까지 기다려 다시 보지 않는다
(그것은 결과를 보고 표본을 늘리는 행위다). 연장은 §I의 P-검사(건수) 미달일 때만 허용된다.

### H-3. Option A'(유료 과거 데이터)의 지위

유료 5~10년 일봉이 도입되면 2024-09 이전 구간은 C-1~3·D 어느 연구도 보지 않은 구간이다. 이는 **다른 regime(2022 약세장 포함)
을 담은 replication**으로 가치가 있으나, 이 선언의 Primary 게이트를 대체하지 않는다. 사용하려면 별도 선언
`C4_V0_HIST_REPLICATION`(라이브러리·query 연도 경계는 데이터 감사 후 고정)을 **그 데이터의 어떤 C-4 outcome도 계산하기 전에**
만든다. Primary가 PASS이고 replication이 FAIL이면 결론 문장에 두 결과를 병기하고 regime 의존을 명시한다.

### H-4. OOS 데이터 동결 (C4-P5) 요구사항

- 새 공통 스냅샷(예: `USB-HIST-V3`, 2024-09-17 ~ 게이트 종료 세션)을 sha256과 함께 동결. grouped daily는 매일(또는 주 단위)
  수집 지속: 롤링 창이라 밀린 날은 API에서 사라진다. 로컬과 Drive 양쪽에 보관.
- CS 스냅샷 2026-10-01, 2027-01-04(첫 거래일), 2027-04-01 수집.
- SEC submissions: 패널 전체 CIK 증분 재수집(게이트 직전 1회), acceptance UTC 대조 40건 재확인.
- XBRL companyfacts: Layer C 필요 CIK 증분 수집.
- OOS query가 될 C-M0 후보는 P5 동결 스냅샷 위에서 `app.dev.run_strategy_c_selection` 규칙(`c769aea5…`)을 **그대로** 실행해
  얻는다. C-M0 규칙 파일·checksum은 바꾸지 않는다.

---

## I. Gate Criteria (GATE-C4-V0)

### I-1. 결과를 보기 전 검사 P (하나라도 미달이면 INCONCLUSIVE, 기준을 낮추지 않는다)

| | 조건 | 값 |
| --- | --- | --- |
| P1 | timezone 감사 UTC | 40/40 |
| P2 | PIT 위반 | 0 |
| P3 | OOS 신호일 | ≥ 120 (H-2) |
| P4 | 유효 query(이웃 ≥ 50, 라벨 유효) | ≥ 2,500 |
| P5 | IC 계산 가능 날(유효 query ≥ 8) | ≥ 100 |
| P6 | unique ticker | ≥ 800 |
| P7 | INSUFFICIENT_NEIGHBORS 비율 | ≤ 5% |
| P8 | 4개 시간 block(신호일 균등 분할) 각각 IC 날 | ≥ 20 |
| P9 | Library Layer B/C coverage: Query 행 중 event status UNKNOWN 비율 | ≤ 10% |

P3~P6 근거: C-M0 후보 일 27.8건 × 120일 ≈ 3,300 query, 2,362 unique ticker/240일(기존 실측)에서 보수적으로 잡은 값.
결과와 무관하게 건수만으로 정했다.

### I-2. PASS 조건 (10개 전부)

| # | 조건 | 판정 기준 |
| --- | --- | --- |
| 1 | PIT 위반 | `= 0` |
| 2 | P1~P9 | 전부 충족 |
| 3 | **F3 mean daily IC(S_5, excess_5)** | 점추정 > 0 **and** 95% CI 하한 > 0 |
| 4 | Q5 − Q1 `excess_return_5` (일별 분위 평균의 신호일 평균) | 점추정 > 0 **and** 95% CI 하한 > 0 |
| 5 | 10D 방향 일치 | Q5 − Q1 `excess_return_10` 점추정 > 0 |
| 6 | MAE 악화 없음 | (Q5 median `mae_5`) − (Q1 median `mae_5`) ≥ −0.02 |
| 7 | MFE-only 금지 | 조건 4가 FAIL이면 MFE 스프레드가 아무리 커도 FAIL. C-1의 `MFE↑ MAE↑ Close≈0` 패턴 재현 시 FAIL |
| 8 | 시간 안정성 | 4 block 중 ≥ 3에서 IC 점추정 > 0 |
| 9 | 집중도 | 단일 ticker ≤ 5%, 상위 5 ticker ≤ 15%, 단일 날짜 ≤ 8%, 상위 10일 ≤ 40% (query 기준); 상위 5 ticker 제외 / 상위 10일 제외 후에도 조건 3·4 점추정 > 0 |
| 10 | **Incremental / D 구별** | `IC_F3 − IC_F0` 점추정 > 0 **and** `IC_F3 > max(IC_B1, IC_B2, IC_B3)` 점추정 |

날짜 집중도 8%/40%는 EQM-V0와 같은 값이다(실적 시즌 군집, C-M0 최대 하루 259건=3.9% 실측).

### I-3. FAIL / INCONCLUSIVE / PASS의 의미

- **FAIL**: P 통과 후 조건 1~10 중 하나라도 실패. 조건 10만 실패하면 결론 문장에 `C4_NOT_DISTINCT_FROM_D_N2A`를 병기한다
  (F0 kNN이 이미 정보를 다 담고 있다는 뜻이며, 그 경우 후속은 D 라인에서만 다룬다).
- **INCONCLUSIVE**: P 미달. 결과를 보지 않은 채 판정. H-2의 1회 연장 규칙 적용.
- **PASS** 의미: "Contextual Historical Analog에 5D 종가 방향 Selection Alpha가 존재한다". 트레이딩 전략이 아니다.
  Entry/Exit/Risk/Cost/Backtester는 별도 선언.

### I-4. 종료 규칙

`GATE-C4-V0 = FAIL`이면 이 가설을 종료한다. 이후 금지: K 변경, 거리함수 교체, feature family 재구성, horizon 교체,
normalization 변경, Q/L 모집단 재정의, S 집계기 변경(mean/weighted), 기존 C 임계값 조정. Secondary 기술통계
(K=25/100, cosine, F0~F2, 10D)가 좋게 나와도 그것만으로 PASS를 만들지 않는다. 다른 가설은 새 연구 ID와 새 선언으로만.

### I-5. 범위 선언 (결론 문장에 반드시 붙인다)

```text
SCOPE = 5D close-direction selection alpha of situation-analog forecasts, within C-M0 momentum candidates,
        single-provider daily bars (2-year rolling), SEC/XBRL rule-based context
NOT   = chart analog (D), trading strategy, event semantics, sector/peer context, real CVD
```

---

## J. Immediate Recommendation

```text
C4_RESEARCH_READY   (조건부: OOS = Option B Forward Shadow, 1차 게이트 2027-03-25 이후)
```

- `C4_BLOCKED_DATA_DEPTH`가 아닌 이유: 사전등록 초안 §5가 Forward Shadow를 명시적으로 허용하고, Library(2년)·이벤트(1994~)·
  XBRL 저장소와 계산 경로가 전부 존재한다. 막힌 것은 Option A뿐이다.
- `C4_NOT_DISTINCT_FROM_D`가 아닌 이유: 매칭 대상(스칼라 상황 벡터 vs 가격 경로), query 모집단(모멘텀 후보 vs 무작위 유니버스),
  정보원(이벤트·품질·OBV·시장맥락)이 다르다. 다만 F0 family는 D의 N2a와 사실상 같으므로 조건 10이 그 경계를 지킨다.

### 진행 순서와 선행조건

| 단계 | 내용 | 착수 조건 |
| --- | --- | --- |
| C4-P0 | 이 문서 | 완료 |
| (사람) | untracked D/EQM/B 코드·문서 commit + push (§D-4) | D4 완료 후 |
| C4-P1 | 데이터 feasibility 실측: (a) 패널 전체 CIK submissions 증분 수집 건수·바이트, (b) Layer C 필요 CIK 수·companyfacts 바이트, (c) `vw` 결측률, (d) Library 행 수·`VECTOR_UNDEFINED`·K=50 coverage(이웃 수 분포, INSUFFICIENT 비율), (e) 1.1M 행 이벤트 분류 계산비용 | **D4 `GATE-D-ALPHA` 종료 후**(메모리·CPU 경합, 패키지 digest 보호) |
| C4-P2 | Feature·PIT 감사 + `c4_analog_rules_v1.json` 동결(canonical sha256, C/D/EQM 동일 recipe) + bootstrap seed 부여 | P1 수치만 보고, 어떤 outcome도 계산 전 |
| C4-P3 | 엔진 구현(`strategy_c4_analog/`, D 유틸 import, 자체 identity·gate) + 테스트(import 경계 AST, alpha firewall, mutation test) | P2 동결 후 |
| C4-P4 | In-sample sanity (2025-09-18~2026-09-01): coverage, PIT 감사, determinism, 기술통계 IC(`IS_SANITY_ONLY` 라벨). **판정 금지, 규칙 변경 금지** | P3 |
| C4-P5 | OOS 스냅샷 동결(`USB-HIST-V3`), CS/SEC/XBRL 증분, C-M0 규칙 그대로 후보 생성 | 2027-03-25 이후(120 신호일) |
| C4-P6~P8 | OOS IC·분위·baseline·ablation·안정성·집중도 → GATE-C4-V0 | P5 |

### 지금 당장 하지 않는 것

- 코드 작성(P3 이전), 규칙 JSON 생성(P2 이전), D 패키지 접근, 무거운 계산(D4 종료 전), git 자동 조작, C-M0 임계값 접촉.

### P1까지 남는 열린 질문 (결과와 무관, 건수로만 답한다)

1. Library에 Layer B/C를 붙이는 비용이 감당되는가(§E-2, §E-3). 안 되면 F2/F3는 "Query 행에만 Layer B/C, Library는 F0"이
   아니라 **F3 자체를 INCONCLUSIVE(데이터 부족)로 선언**한다. 비대칭 벡터(Query만 맥락 있음)는 거리 정의가 깨지므로 금지.
2. K=50에서 INSUFFICIENT 비율. 5%를 넘으면 K를 낮추지 않고 P7 INCONCLUSIVE.
3. `vw` 결측률(§E-4).

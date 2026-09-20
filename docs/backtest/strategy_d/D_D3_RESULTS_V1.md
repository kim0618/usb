# Strategy D D3 Results V1 (Forward Distribution / Signal Construction)

실행 2026-09-19~20 (집 PC). 판정 **GATE-D3 = PASS**.

- run id `dsig1-eaeb6df9dea6`, identity digest `eaeb6df9dea6805b8b568914a41ec275da3295e203ea829e678d077ff1c2959e`
- parent D2 run `dneigh1-fe0362523739`, identity `fe03625237395c5a96fd4c824b85a4f5a1587f00b8ea1171cef9ba2f519225b7`
- 산출물 `data/runtime/strategy_d/runs/dsig1-eaeb6df9dea6/` (로컬, git-ignored, 83 MB)
- 규칙 정본 `d_analog_rules_v1.json`, canonical sha256 `680bf113…0cd3` **MATCH**
- 새 코드: `strategy_d_analog/`에 `labels`, `signal`, `evaluation`, `d3`,
  CLI `app.dev.run_strategy_d_signal`, 테스트 `backend/tests/strategy_d/test_strategy_d_d3.py` 26개
- A/B/C 코드 변경 0. D0/D1/D2 문서 변경 0. 커밋/푸시/배포 0.

> **D3는 Alpha를 판정하지 않는다.** IC, quintile, N1, N2, bootstrap, PASS/FAIL은 전부 D4 몫이고
> 이 단계에는 그것을 계산하는 코드가 존재하지 않는다(§9).

---

## 1. START GATE (10/10)

| # | 조건 | 결과 |
| --- | --- | --- |
| 1 | D0 canonical checksum MATCH | **OK** `680bf113…0cd3` |
| 2 | D1 freeze identity MATCH | **OK** D1 `data` 블록 == D2 `data` 블록 |
| 3 | D1 grid digest MATCH | **OK** `830744f6…66c9` |
| 4 | D2 `COMPLETE.json` 존재 | **OK** verdict PASS |
| 5 | D2 run identity MATCH | **OK** `fe036252…25b7` (요청문 값과 동일) |
| 6 | D2 artifact digest MATCH | **OK** 14개 neighbor parquet을 디스크에서 다시 읽어 열 digest 재계산, 전부 일치 |
| 7 | D2 Alpha Blindness 기록 존재 | **OK** `pit_runtime.json` hard_failures 0 / violations 0, truncation audit passed |
| 8 | D2 input freeze == 현재 저장소 | **OK** read-set digest `7e790a8d…7d55` 재계산 일치 |
| 9 | D2 artifact에 forward return 없음 | **OK** 19개 parquet 스키마에 return/MFE/MAE/win 계열 열 0 |
| 10 | 다른 세션의 D artifact write 없음 | **OK** `d_identity` 스탬프 19개 전부 동일, D 경로 mtime 이상 없음 |

---

## 2. Parent Binding

D3는 D2를 재검색하지 않는다. D2 산출물은 immutable parent이며, 읽는 것이 곧 검증이다:
각 `neighbors_<test>.parquet`을 읽을 때 D2가 기록한 열 digest를 재계산해 대조하고, 하나라도
다르면 `F1`로 멈춘다.

| 필드 | 값 |
| --- | --- |
| `parent_d2_run_id` | `dneigh1-fe0362523739` |
| `parent_d2_identity` | `fe03625237395c5a96fd4c824b85a4f5a1587f00b8ea1171cef9ba2f519225b7` |
| `parent_d2_complete_digest` | `5bcfdc7a7a4e3c333df635aee58de52af7ddc0b37d0deab0462b76a1b4fbfc00` |
| `rules_checksum` | `680bf113…0cd3` |
| `freeze_id` / `freeze_digest` | `STRATEGY_C_RAW_FREEZE_V1` / `9ebd6c29…eafe` |
| `grid_digest` | `830744f6…66c9` |
| `label_validity_contract` | `d-label-validity-v1` (D2가 쓴 것과 동일) |
| `label_value_contract` | `d-label-value-v1` (D3 신규) |
| `code_digest` | `4c1903d8a43351b39e0e7c49d6dcfafb41e2241c2aafbeee1d56d8af7530c726` |

재검색/재계산 금지 항목(similarity, encoder, Top-K, K, M, cap, neighbor filtering)은 D3 코드
경로에 아예 없다. `signal.py`와 `evaluation.py`는 `library`, `similarity`, `neighbor_search`,
`encoder`를 import하지 않는다.

---

## 3. Label 계약 (D0 그대로)

```text
close_return_h  = P(D+h) / P0 - 1,  P0 = O(D+1),  [-1, 1]로 clip
excess_return_h = close_return_h - (같은 날짜 전체 적격·label-valid 유니버스의 median)
validity        = D+1 봉 && D+h 봉 && ~label CA 의심 (h=20은 D+11..D+20 확장 포함)
```

- 가격은 각 세션의 `x(t)/F(t)`다. D+1과 D+h 사이에 분할이 실행되면 비율에 반영되며, 이것이
  label이 필요로 하는 조정이자 feature가 절대 봐서는 안 되는 조정이다(C `labels.py`와 같은 기준).
- 기준가를 종가→시가 등으로 바꾸지 않았다. D0 `labels.reference_price` 그대로다.
- 벤치마크는 **그날의 전체 적격 유니버스 median**이지 query 표본이 아니다. 표본을 쓰면 한 query의
  벤치마크가 "그날 또 누가 뽑혔는가"에 의존하게 되고, 이는 D0가 선언한 정의가 아니다.
- sector excess는 만들지 않았다. D0에 없다.

### 3.1 label 값 규모

| h | label-valid 적격 ticker-date | 유니버스 median이 존재하는 날짜 |
| --- | --- | --- |
| 1 | 1,121,041 | 440 |
| 3 | 1,115,086 | 438 |
| 5 | 1,109,115 | 436 |
| 10 | 1,094,174 | 431 |
| 20 | 1,064,053 | 421 |

median이 없는 날짜는 seasoning(index 60) 이전과 데이터셋 끝에서 h일 이내다. h=1이면
`501 - 60 - 1 = 440`으로 정확히 맞는다. 이 날짜들은 query 날짜도 라이브러리 종료일도 아니다.

---

## 4. Label Join

| 항목 | 값 |
| --- | --- |
| 이웃 label join 행 | **46,410,000** (14검정 x 66,300 query x 50) |
| valid | **46,410,000** |
| invalid | **0** |

invalid가 0인 것은 D3가 무엇을 걸러냈기 때문이 아니다. D0 `library.definition`이
"has a valid label for the tested horizon h"를 라이브러리 정의에 포함하고 있어서, D2가 창을
라이브러리에 넣는 시점에 이미 `valid_h` mask가 적용됐다. 따라서 D2가 채택한 이웃은 구성상
label-valid이고, D3는 그중 하나도 잃지 않는 것이 정상이다. 테스트가 이 불변식
(`neighbor_valid == neighbor_total`)을 행 단위로 고정한다.

**K를 채우기 위해 51위 이후 이웃을 가져오는 경로는 구현하지 않았다.** 그렇게 하면 D2 결과가
바뀐다. query는 D2가 준 50개를 그대로 유지하고, 무효 이웃이 있었다면 집계에서만 빠진다.

### 4.1 Insufficient valid neighbors

D0가 선언한 문턱은 `insufficient_neighbors` 하나뿐이다: "fewer than top_k neighbors after all
policies". label 유효성이 그 policy에 포함되므로 D3의 문턱도 **top_k = 50**이고, 별도의 비율
문턱은 D0에 존재하지 않는다. 결과를 보고 문턱을 만들지 않았다.

| 검정 | OK | INSUFFICIENT_VALID_NEIGHBORS |
| --- | --- | --- |
| 14검정 전부 | 66,300 | **0** |

---

## 5. S(q) / sigma(q)

D0 `signal` 블록 그대로 구현했다.

```text
S(q)      = median of neighbour excess_return_h over the accepted top_k
sigma(q)  A: mean rho over the accepted top_k
          B: -mean(d) / sqrt(W) over the accepted top_k
```

- S는 median이다. mean으로 바꾸지 않았고, 결과를 본 뒤 유리한 쪽으로 교체하지 않았다.
  테스트가 `S == distribution_q50`이면서 `S != distribution_mean`인 fixture로 이를 고정한다.
- sigma는 V1에서 descriptive이며 gate 입력도 filter도 아니다(D0 `role`). A의 rho와 B의 거리는
  스케일이 달라 직접 비교하지 않고, 해석은 TestId 내부에서만 한다.
- 분포 요약의 표준편차는 ddof=1이다. D0가 정의하는 유일한 표준편차(N2 `realized_vol_W`)가
  ddof=1이라 같은 관례를 따랐고, identity에 `std_ddof`로 기록했다.

### 5.1 Representation 분리

14 TestId가 각각 독립적으로 `S`, `sigma`, `n_valid`, 분포 요약을 만든다. A와 B를 합치거나
앙상블하는 경로는 없다.

---

## 6. Forward Distribution

query x TestId 한 행에 저장한 값:

```text
neighbor_total  neighbor_valid
S  sigma
distribution_mean  distribution_median  distribution_std
positive_count  negative_count  zero_count  hit_rate
distribution_q10  q25  q50  q75  q90
signal_status
```

D0/D2 계약에 없는 추가 percentile은 만들지 않았다. MFE/MAE는 **D3에서 계산하지 않았다**:
요청문 §12는 "계약에 없으면 D4로 미룬다"이고, §24가 정한 D4 필수 입력 목록에도 없다. D0가
secondary metric으로 언급하는 이웃 median MFE/MAE는 D4가 같은 `labels.py`로 계산할 수 있다.

---

## 7. Query Realized Label (§11)

D4의 정답지로 **별도 테이블**에 저장했다. D3는 이 값을 해석하지 않는다.

| h | 행 | valid | invalid |
| --- | --- | --- | --- |
| 1 | 66,300 | 66,255 | 45 |
| 3 | 66,300 | 66,202 | 98 |
| 5 | 66,300 | 66,162 | 138 |
| 10 | 66,300 | 66,062 | 238 |
| 20 | 66,300 | 65,848 | 452 |

horizon이 길수록 무효가 느는 것은 `D+h` 봉 결손과 CA 의심 창이 길어지기 때문이다. D4가 이
행들을 제외하고 집계한다.

`evaluation_labels.parquet`에는 `S`도 `sigma`도 없고, `signal_rows.parquet`에는
`query_forward_return`도 `query_label_valid`도 없다. 두 테이블은 D4가 `(query, horizon)`으로
join한다.

---

## 8. PIT

| 항목 | 결과 |
| --- | --- |
| 이웃 label join embargo (`d + h <= D - W`) 재검사 | 위반 **0** (D2가 이미 보장하지만 D3가 다시 검사) |
| encoder/universe as-of 위반 | 0 |
| input read-set PRE == POST | `7e790a8d…7d55` **MATCH** |
| HARD FAIL | 0 |

embargo를 D3에서 다시 보는 이유는, 이웃의 **label**이 이웃 자체보다 나중에 확정되는 사실이라
부모 산출물이 어긋났을 때 실제로 미래가 새어 들어올 수 있는 첫 지점이 이 join이기 때문이다.

---

## 9. Alpha Firewall (§16)

| 항목 | 결과 |
| --- | --- |
| Spearman / Pearson IC | **계산 안 함** |
| quintile, Q5-Q1 | **계산 안 함** |
| bootstrap CI, Bonferroni | **계산 안 함** |
| N1 / N2 비교 | **계산 안 함** |
| PASS/FAIL 판정 | **계산 안 함** |

세 가지로 확인한다.

1. **코드.** D 패키지 전 모듈(규칙 리더 `config.py` 제외)을 AST로 파싱해 실행되는 식별자와
   문자열 리터럴에서 `spearman`, `pearson_ic`, `quintile`, `bootstrap`, `bonferroni`,
   `baseline_n1`, `baseline_n2`, `pass_fail`, `ic_point`을 찾는다. 0건.
   (`config.py`는 D0 선언값을 노출하는 것이 임무라 `bootstrap_seed`를 이름으로 갖는다. 그 값은
   `load_rules`가 query 해시 seed와 대조하는 데만 쓰이고 bootstrap을 돌리지 않는다.)
2. **스키마.** `signal_rows.parquet`에 IC/quintile/baseline 계열 열이 없다.
3. **summary.** 행 수, valid/invalid 수, 상태별 수, 실행 시간, 산출물 크기만 담는다. 평균 S,
   S 양수 비율, 실현수익 방향 일치율 같은 Alpha 암시 집계는 넣지 않았고 테스트가 이를 고정한다.

---

## 10. 결정성

같은 parent D2, 같은 freeze, 같은 code로 2회 실행해 대조했다. **3개 content digest가 전부
일치**했고, identity digest와 run id(`dsig1-eaeb6df9dea6`)까지 같았다. 두 run이 같은 디렉터리를
가리키므로 run 2는 `--runs-dir`로 분리해 실행했다.

| 산출물 | digest | 2회차 |
| --- | --- | --- |
| `signal_rows` | `e8b78a3c9b5d752ad52099b3d689c61417ba13f6c22d2574a776779109892e10` | 일치 |
| `evaluation_labels` | `1072250d6ecb8849d1ac1aa337c8ec6f3e7e2cb07fb73d1cb1caa29a2bae0024` | 일치 |
| `status_counts` | `f0d98e132c3dc972cacad1435c95f15b3709ba1315fc7fd449ca0f8b1a1d9da9` | 일치 |

`signal_status_by_test`, `neighbor_label_join`, `query_label_rows_by_horizon`,
`label_value_counts`, `freeze_identity`, `artifact_identity`, `parent_d2_*` 블록도 모두
동일했다. 달라진 값은 `elapsed_seconds`(295.5 / 324.1)와 `peak_rss_mb`(1,563.5 / 1,633.7)뿐이며,
둘 다 설계대로 결정적 digest에서 제외된 runtime 값이다.

두 실행 사이에 WSL의 Google Drive 마운트(`/mnt/g`)가 한 번 사라져 2회차가
`BACKTEST_WORKSPACE_NOT_FOUND`로 중단됐고, 마운트 복구 후 다시 실행했다. run 1은 영향을 받지
않았다: 실행 전후 read-set digest가 같았으므로(§8) 입력은 run 1 내내 고정돼 있었고, 복구 후
2회차가 같은 read-set digest로 같은 결과를 냈다.

---

## 11. 성능

| 항목 | 값 |
| --- | --- |
| 실행 시간 | **295.5초 (4.9분)** |
| peak RSS | **1,563.5 MB** (목표 2.0 GB 이하) |
| 읽은 이웃 행 | 46,410,000 |
| label join | 46,410,000 |
| 검정당 시간 | 17.4 ~ 18.3초 |
| 산출물 | `signal_rows.parquet` 76 MB + `evaluation_labels.parquet` 6.5 MB = 83 MB |

similarity 재검색이 없으므로 D2(36.7분)의 1/7 시간이다. 이웃 parquet은 검정마다 한 번만 읽고
(열 10개만), label은 `(T, N)` 행렬에서 vectorized gather로 뽑은 뒤 query별로 `(q, K)` 배열
하나로 집계한다. 전체 neighbor dataframe 사본은 만들지 않는다.

---

## 12. 테스트

`backend/tests/strategy_d/test_strategy_d_d3.py` **26개**, D 전체 **122개** 통과.

| 묶음 | 내용 |
| --- | --- |
| 분포 집계 | mean/median/std(ddof=1)/positive·negative·zero/hit_rate/q10~q90 기대값 고정 |
| S 정의 | median이며 mean이 아님, 자기 분포의 q50과 일치 |
| sigma 정의 | A = mean rho, B = -mean(d)/sqrt(W), W=20과 60에서 각각, 잘못된 representation은 hard fail |
| 무효 이웃 | 집계에서만 제외, 재충전 없음, `n_total`은 그대로 |
| insufficient | 문턱은 top_k, 미달 시 S/sigma는 NaN |
| label 값 | `P(D+h)/O(D+1)-1`, clip, 유니버스 median 차감, 부적격 종목은 벤치마크에서 빠지되 excess는 받음 |
| label PIT | `d+h` 이후 가격을 바꿔도 label 불변(+ 같은 변경이 horizon이 닿는 행은 바꾼다는 양성 대조) |
| embargo | join 시 재검사, 경계 `d+h == D-W`는 허용, +1은 hard fail |
| **query future firewall** | query 날짜 이후 전 종목 봉을 변조해도 14검정 S가 **bit 단위 동일**, 동시에 evaluation label은 5개 horizon 전부 변함 |
| 경로 분리 | `signal.py`가 `evaluation`을 import하지 않음(AST), D2 모듈이 `labels`/`signal`/`evaluation`/`d3`를 import하지 않음 |
| parent binding | COMPLETE 없음 거부, 이웃 parquet 1개 값 1e-9 변경 시 거부, 다른 freeze 거부 |
| 산출물 | 전 parquet·json에 `d_identity` 스탬프, COMPLETE 마지막, D4 필수 열 존재, 금지 열 부재 |
| 결정성 | 같은 입력 2회 실행 digest 동일 (합성) |

### 12.1 핵심 테스트

`test_the_signal_cannot_see_the_query_future`는 마지막 query 날짜 이후 모든 봉에
`U(1.1, 2.5)`를 곱한 뒤 14검정을 다시 돌려 `S`가 **바이트 단위로 같음**을 요구하고, **동시에**
같은 변조로 evaluation label이 5개 horizon 전부 바뀌는 것을 요구한다. 앞쪽만 통과하는 구현은
point-in-time한 것이 아니라 아무것도 계산하지 않는 것이므로, 두 조건을 한 테스트에 묶었다.

---

## 13. D3 Gate

| # | 조건 | 결과 |
| --- | --- | --- |
| 1 | parent D2 identity MATCH | **PASS** |
| 2 | freeze / grid / rules MATCH | **PASS** |
| 3 | label join PIT violation 0 | **PASS** |
| 4 | signal path와 evaluation label path 분리 | **PASS** (AST + 별도 테이블) |
| 5 | future query mutation에도 S/sigma 동일 | **PASS** |
| 6 | invalid label 처리 계약 일치 | **PASS** (집계 제외, 재충전 없음) |
| 7 | insufficient-valid 정책 일치 | **PASS** (문턱 = top_k = 50) |
| 8 | 14 TestId 모두 signal table 생성 | **PASS** (928,200행 = 14 x 66,300) |
| 9 | two-run digest identical | **PASS** (§10) |
| 10 | N1/N2/IC/quintile 계산 0 | **PASS** |
| 11 | A/B/C 전략 파일 변경 0 | **PASS** |

**11개 조건이 모두 충족됐다. `GATE-D3 = PASS`.**

D3 PASS는 "이웃의 미래를 PIT-safe하게 결합해 S(q)와 sigma(q)를 결정적으로 만든다"는
뜻뿐이다. **그 신호에 예측력이 있는지에 대해서는 아무것도 말하지 않는다.** 그 판정은
D4 GATE-D-ALPHA 몫이다.

---

## 14. D4 준비

D4는 다음 두 테이블만 읽으면 된다. Pattern Library나 Similarity Engine을 다시 부를 필요가 없다.

```text
signal_rows.parquet
  test_id  query_date_idx  query_date  query_ticker  sample_rank
  window  horizon  representation
  neighbor_total  neighbor_valid
  S  sigma
  distribution_mean/median/std  positive/negative/zero_count  hit_rate
  distribution_q10/q25/q50/q75/q90
  signal_status

evaluation_labels.parquet
  horizon  query_date_idx  query_date  query_ticker  sample_rank
  query_forward_return  query_close_return  query_label_valid
```

join key는 `(query_date_idx, sample_rank, horizon)`이다. D4는 시작 시 D3
`run_identity.json`의 `parent_d2_*`, `rules_checksum`, `freeze_digest`, `grid_digest`가 자기
입력과 같은지 확인하고 다르면 거부해야 한다.

---

## 부록: 재현

```bash
cd ~/usb
OPENBLAS_NUM_THREADS=8 PYTHONPATH=backend .venv/bin/python -u \
  -m app.dev.run_strategy_d_signal --snapshot-id USB-HIST-V1 \
  --parent-run dneigh1-fe0362523739
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/strategy_d -q
```

2회차는 `--runs-dir`로 분리해 실행한다(같은 입력이면 run id가 같아 같은 디렉터리를 가리킨다).
`--test-limit N`은 앞 N개 검정만 도는 smoke 옵션이다.

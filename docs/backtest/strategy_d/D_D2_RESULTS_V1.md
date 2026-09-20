# Strategy D D2 Results V1 (Pattern Library / Top-K Neighbor Search)

실행 2026-09-19 (집 PC). 판정 **GATE-D2 = PASS**.

- run id `dneigh1-fe0362523739`, identity digest `fe03625237395c5a96fd4c824b85a4f5a1587f00b8ea1171cef9ba2f519225b7`
- parent D1 run `dpit1-2ec7d609bf56` (freeze/grid identity가 그 run의 `data` 블록과 완전 일치)
- 산출물 `data/runtime/strategy_d/runs/dneigh1-fe0362523739/` (로컬, git-ignored, 1.2 GB)
- 규칙 정본 `d_analog_rules_v1.json`, canonical sha256 `680bf113…0cd3` **MATCH** (D0 이후 변경 0)
- 새 코드: `strategy_d_analog/`에 `pit`, `encoder`, `sampling`, `label_extension`, `library`,
  `similarity`, `neighbor_search`, `artifacts`, `pit_audit`, `d2`, CLI `app.dev.run_strategy_d_neighbors`,
  테스트 `backend/tests/strategy_d/test_strategy_d_d2.py` 62개 (D 전체 95개)
- A/B/C 코드 변경 0. D0/D2 설계/Pre-flight 문서 변경 0. 커밋/푸시/배포 0.

> **D2는 forward return 값을 한 번도 읽지 않는다.** 이 단계가 label에서 쓰는 것은 유효성 bool
> mask 하나뿐이고, 산출물 스키마에는 결과를 담을 열 자체가 없다(§8).

---

## 1. Gate 판정

| # | 조건 | 결과 |
| --- | --- | --- |
| 1 | D0 checksum MATCH | **PASS** `680bf113…0cd3` |
| 2 | D1 identity / freeze / grid MATCH | **PASS** (§2) |
| 3 | D2 test vector 100% | **PASS** V1~V25 + T1/T4/T6/T9/T12~T15 |
| 4 | PIT runtime violation 0 | **PASS** hard failure 0, violation 0 |
| 5 | Input PRE/POST digest 동일 | **PASS** `7e790a8d…7d55` |
| 6 | Pattern Library 결정적 | **PASS** (§9) |
| 7 | Query sampling 결정적 | **PASS** |
| 8 | Similarity 결정적 | **PASS** |
| 9 | Neighbor Top-K 결정적 | **PASS** |
| 10 | 최적화 검색 = full sort | **PASS** (§7) |
| 11 | Truncate equivalence | **PASS** 12일 / 50,400 query / 2,520,000 이웃, 불일치 0 |
| 12 | 2회 run digest 동일 | **PASS** (§9) |
| 13 | RSS <= 2 GB | **PASS** 1,731.7 MB |
| 14 | Alpha data 열람 = NO | **PASS** (§8) |
| 15 | A/B/C 전략 파일 변경 0 | **PASS** |

---

## 2. 데이터 바인딩

D1이 확정한 `FreezeIdentity`를 `expected`로 넘겨 로드했고, 11개 필드가 모두 일치했다.

| 필드 | 값 |
| --- | --- |
| `snapshot_id` / `snapshot_sha256` | `USB-HIST-V1` / `e2a8e5d6…07be7` |
| `freeze_id` / `freeze_digest` | `STRATEGY_C_RAW_FREEZE_V1` / `9ebd6c29…eafe` |
| `source_digest` | `adc4f191…32c02` |
| `d_read_digest` | `7e790a8d…7d55` |
| grid | 2024-09-17 .. 2026-09-16, N = **501**, digest `830744f6…66c9` |
| 평가 구간 | index [260, 480], **221일**, 날짜당 300 표본 |

---

## 3. Pattern Library

W별로 상한이 다르다. 가장 늦은 query가 볼 수 있는 마지막 종료일은 `eval_end - W - min(h)`이고
(D2 설계 §13), stride는 grid index `d % 5 == 0`, 최소 `d = 60`이다.

| W | 행 수 | stride 날짜 | 종료 index | `defined_A` / `defined_B` |
| --- | --- | --- | --- | --- |
| 20 | **202,940** | 80 | 60..455 | 202,940 / 202,940 |
| 40 | **191,982** | 76 | 60..435 | 191,982 / 191,982 |
| 60 | **178,611** | 71 | 60..410 | 178,611 / 178,611 |
| 합계 | **573,533** | | | |

- D1 예상 216,243창(상한 480까지 85 stride 날짜)과 비교하면 W20이 80일로 줄어든 만큼 작다. 예상과 어긋난 것이 아니라, D1은 상한을, D2는 W별 실제 값을 센 것이다.
- `VECTOR_UNDEFINED`는 **세 W, 두 표현 모두 0건**이다. 적격 창에 상수 가격 경로가 없다는 뜻이고, D2 설계 §7이 대비한 `np.ptp == 0` 경로는 실데이터에서 발동하지 않았다. 그래도 테스트 V3가 이 경로를 고정한다.

검정별 사용 가능 행(적격 & 벡터 정의 & `valid_h`):

| 검정 | 행 | 검정 | 행 | 검정 | 행 |
| --- | --- | --- | --- | --- | --- |
| W20 h1 | 202,815 | W40 h5 | 191,644 | W60 h10 | 178,040 |
| W20 h3 | 202,689 | W40 h10 | 191,372 | W60 h20 | 177,503 |
| W20 h5 | 202,575 | | | | |

label 무효로 빠진 창은 h가 길수록 많다(W20 h1 125 → W60 h20 1,108). 20D가 가장 많은 것은
`d+20` 봉 존재 조건과 D+11..D+20 CA 확장이 함께 걸리기 때문이다.

---

## 4. Query 표본

| 항목 | 값 |
| --- | --- |
| 표본 window | **66,300** (221일 x 300) |
| 고유 ticker | **3,331** |
| 적격 < 300인 날짜 | **0** |
| 적격 < 100(`min_valid_queries_per_date`)인 날짜 | **0** |
| 해시 | `Q|20260917|{D}|{ticker}`, 날짜는 ISO |
| content digest | `9e040649ad5f48833500f3cc0f3ea4471fce9b7544965e4314440d236505fbdc` |

D1이 상한으로 기록한 66,300과 3,331이 그대로 실현됐다. 표본은 14검정 공통이며 label을 보기 전에
뽑힌다(테스트 T15: label mask를 전부 무효로 만들어도 표본이 바뀌지 않는다).

---

## 5. Neighbor Search

`K = 50`, 초기 pool `M0 = 400` (= 8K), `QUERY_CHUNK = 50`, `OPENBLAS_NUM_THREADS = 8`.

| 검정 | OK | VECTOR_UNDEFINED | INSUFFICIENT_NEIGHBORS | M=400 | M=800 |
| --- | --- | --- | --- | --- | --- |
| W20_H1_A | 66,300 | 0 | 0 | 66,284 | 16 |
| W20_H1_B | 66,300 | 0 | 0 | 66,298 | 2 |
| W20_H3_A | 66,300 | 0 | 0 | 66,283 | 17 |
| W20_H3_B | 66,300 | 0 | 0 | 66,298 | 2 |
| W20_H5_A | 66,300 | 0 | 0 | 66,283 | 17 |
| W20_H5_B | 66,300 | 0 | 0 | 66,298 | 2 |
| W40_H5_A | 66,300 | 0 | 0 | 66,282 | 18 |
| W40_H5_B | 66,300 | 0 | 0 | 66,299 | 1 |
| W40_H10_A | 66,300 | 0 | 0 | 66,281 | 19 |
| W40_H10_B | 66,300 | 0 | 0 | 66,299 | 1 |
| W60_H10_A | 66,300 | 0 | 0 | 66,268 | 32 |
| W60_H10_B | 66,300 | 0 | 0 | 66,300 | 0 |
| W60_H20_A | 66,300 | 0 | 0 | 66,262 | 38 |
| W60_H20_B | 66,300 | 0 | 0 | 66,299 | 1 |

- **`INSUFFICIENT_NEIGHBORS` 0건.** 14검정 928,200 query 전부가 정책 적용 뒤 50개를 채웠다. 이웃 행 합계는 928,200 x 50 = **46,410,000**이다.
- M 확장은 최대 1회(400 → 800)뿐이고, 928,200건 중 166건(0.018%)에서만 일어났다. `M0 = 8K`가 넉넉하다는 뜻이며, M은 조정 손잡이가 아니라 성능 진단값이다(확장해도 결과는 같다, §7).
- 표현 A가 B보다 확장이 잦다. A는 rho 상위가 1.0 근처에 몰려 동률 pool이 커지고, 종목 cap 1과 날짜 cap 5를 더 많이 소모하기 때문으로 보인다. **이 관찰은 규칙 변경 사유가 아니고, D2는 이웃의 성과를 보지 않으므로 여기서 더 해석하지 않는다.**

D0 `insufficient_neighbors` 판정(조건 9의 0.05 문턱)은 label 유효성까지 붙는 **D4 몫**이다. D2는
후보가 구조적으로 모자라지 않는다는 것만 기록한다.

---

## 6. PIT 런타임

| 불변식 | 검사 횟수 | 위반 |
| --- | --- | --- |
| R1 규칙 checksum | 1 | 0 |
| R2 dataset digest (load + POST 재검증) | 2 | 0 |
| R3 grid G1~G8 | 1 | 0 |
| R4 as-of view | 924 | 0 |
| R6 후보 prefix embargo | 3,094 | 0 |
| R7 채택 이웃 embargo | 928,200 | 0 |
| R9 same ticker / same FIGI | 928,200 | 0 |
| R10 cap / rank / 중복 | 928,200 | 0 |
| R11 라이브러리 정렬·유일·stride·최소 index | 3 | 0 |
| R12 score 유한 | 18,564 | 0 |

**HARD FAIL 0건, violation 0건.** 정상 제외(R14~R16)는 §3, §5 표에 사유별로 집계돼 있다.

### 6.1 Input immutability

```text
PRE  7e790a8dcf1db8cee336aa281f5b9b7b8481402bbc1d828f27d19cccb0dd7d55
POST 7e790a8dcf1db8cee336aa281f5b9b7b8481402bbc1d828f27d19cccb0dd7d55
MATCH
```

POST는 freeze가 나열한 510개 파일을 **다시 해시해서** 계산한다. run 도중 D namespace 밖 writer
(B 분봉 수집 등)가 워크스페이스에 파일을 추가하는 것은 허용되지만, D의 read-set이 움직이면
`R2 INPUT_MUTATED_DURING_D2`로 산출물을 폐기한다. 테스트가 이 동작을 고정한다.

### 6.2 Truncate equivalence (D0 PIT mutation #1)

12개 감사일(260, 278, …, 458)에서 `truncate(panel, D)`로 물리적으로 잘린 데이터셋에 같은 코드
경로를 다시 태웠다.

```text
비교 query      50,400  (12일 x 14검정 x 300)
비교 이웃    2,520,000
불일치              0   (end_idx, ticker, metric_value 전부 bit 단위 동일)
```

라이브러리 prefix slice와 query 청크 모양이 데이터 내용과 무관하게 `(W, h, D)`만으로 정해지므로
gemm 입력 바이트가 같고, 따라서 bit 단위 비교가 가능하다(D2 설계 §9.3).

---

## 7. 최적화 동치 (Full-sort equivalence)

Top-M 확장은 최적화이므로 결과를 바꿀 수 없어야 한다. `neighbor_search.select_full_sort`를
**테스트가 아니라 배포 모듈에** 두고, 두 경로가 같은 입력에 같은 tie-break를 적용하도록 했다.

- 합성 케이스 6종(동률 강제 포함): `select` == `select_full_sort`
- 실데이터 fixture 전 14검정: 동일
- `M0 = 50`, `M0 = 400`, `M0 = 전체`, 입력 행 순서 셔플 5종: 이웃 목록과 metric 바이트 동일 (V20)

경계 동률은 "M번째 값 **이상** 전부"로 pool에 넣어 잘리지 않게 한다.

---

## 8. Alpha Blindness

| 질문 | 답 |
| --- | --- |
| future return을 조회했는가 | **NO** |
| future return을 출력했는가 | **NO** |
| future return을 산출물에 저장했는가 | **NO** |
| MFE / MAE를 계산했는가 | **NO** |
| IC를 계산했는가 | **NO** |
| N1 / N2를 계산했는가 | **NO** |

세 가지 방식으로 확인한다.

1. **스키마.** 산출물 parquet 어느 열에도 return/MFE/MAE/win 계열 이름이 없다. D3가 `EmbargoView`를 거쳐 따로 join한다.
2. **코드.** D 패키지 전 모듈을 AST로 파싱해 실행되는 식별자와 문자열 리터럴에서 `close_return`, `excess_return`, `forward_return`, `compute_labels`, `mfe`, `mae`, `spearman`, `quintile`, `win_rate`, `baseline_n1`을 찾는다. 0건(docstring 산문은 코드가 아니므로 제외).
3. **경로.** `encoder`, `similarity`, `universe`, `sampling`, `neighbor_search`가 label 모듈을 import하지 않는 것을 AST로 고정한다(R13 / T13).

`label_extension.py`는 유효성 mask만 만든다. C `labels.compute_labels`는 **합성 fixture 위 테스트 T12
한 곳에서만** 호출하며, 그것도 `label_ca_suspect`와 `no_entry_bar` 두 mask의 동일성 확인 용도다.
실데이터에서는 한 번도 호출하지 않는다.

---

## 9. 결정성

같은 입력으로 full run 2회를 서로 다른 디렉터리에 실행해 대조했다. **19개 content digest가 전부
일치**했고, identity digest와 run id(`dneigh1-fe0362523739`)까지 같았다. 두 run이 같은 디렉터리를
가리키므로 run 2는 `--runs-dir`로 분리해 실행했다.

`library`, `status_by_test`, `pool_m_by_test`, `insufficient_by_test`, `label_validity`,
`query_manifest`, `artifact_identity` 블록도 모두 동일했다. 달라진 값은 `elapsed_seconds`
(2,203.4 / 2,177.6)와 `peak_rss_mb`(1,731.7 / 1,708.6)뿐이며, 둘 다 설계대로 결정적 digest에서
제외된 runtime 값이다.

| 산출물 | digest |
| --- | --- |
| `query_samples` | `9e040649ad5f48833500f3cc0f3ea4471fce9b7544965e4314440d236505fbdc` |
| `query_results` | `12f95ca19fd630536cfdacc36ef54f198933a1f993f6ec46112b7b4ce87789d2` |
| `library_meta_W20` | `8abadeb784353ede26650ac391c1a8662a1faf07bc63bedb0b18ea2a020e9f06` |
| `library_meta_W40` | `14ff63ef1524059330d4b903841bfdf0a355e12d412cf864970e3bb5a4301f3a` |
| `library_meta_W60` | `5bedc167d45c723b121285b5754d9c234b8e3cc5a8da0f7317ac244310c70220` |
| `neighbors_W20_H1_A` | `9ec6f6211d54cecdd65a4e85e1e914353d794b7df97b7d646558e8d439c1c780` |
| `neighbors_W20_H1_B` | `c2abdb66ca682d2036e04d58017947ecb5e8b81a90901a74ffb62ed04a0a466e` |
| `neighbors_W20_H3_A` | `f9eab63b1daab19604edc58f35c7012cda901ad39c6ee4bd28eb570d4157bc91` |
| `neighbors_W20_H3_B` | `2b09aefc96e76fd608fb1cefa7e4b5c4d0195afa0ed6f89067d5a73a1f82fee9` |
| `neighbors_W20_H5_A` | `326c7de2101ed649bc19e6d6fbc0c3289499bd3e454eb17f52450bf315b50201` |
| `neighbors_W20_H5_B` | `3be6e62375c6da47b69612a3a9ae71fa1bc6bacaa3969f1fa46263fb7676c4b1` |
| `neighbors_W40_H5_A` | `9e3d6b0a25d3b174f4e2e81fd246c848aa483d41148e624cbf531c08f00b218a` |
| `neighbors_W40_H5_B` | `51018bd3b16f07fe611976d3637ade0be539f74209e93333a0656fbf1a2879d0` |
| `neighbors_W40_H10_A` | `64f702aad66f7b175f99b861a4c237df70ac8eda42560fbdcc55c94b420d986f` |
| `neighbors_W40_H10_B` | `6e0c4132ccf902c943a2289f27f01e4efa2d1e1e74401feec09e247992100bc4` |
| `neighbors_W60_H10_A` | `0c2561f6a8fc2855cdf6a135c07df78135fd0cbb39c9c7d48a5251e9d554bafe` |
| `neighbors_W60_H10_B` | `a2c8bb32de8d19c89f99b6461dfa6f14b34ce0124a3ae712cd7e1e5a9c57de5c` |
| `neighbors_W60_H20_A` | `3ee4f8a0abb5558a39a1516f4b6aa0479f50f365e8b806904d01a46ba383390c` |
| `neighbors_W60_H20_B` | `ad635f3499d8aa3d83a13c24ddebca8206b72d2fdc437c9df50e3d04f1d32754` |

digest는 열 바이트 기준(`<i4`/`<f8` 승격 후 sha256)이라 parquet writer 버전이나 압축 설정에
영향받지 않는다. 작은 표(`query_samples`)는 C `table_digest`와 같은 CSV `%.17g` recipe를 쓴다.
run id는 identity digest에서 나오므로 두 run의 run id도 같다(그래서 서로 다른 디렉터리에 썼다).

---

## 10. 성능 (실측)

| 항목 | 값 | D1 추정 |
| --- | --- | --- |
| 총 실행 시간 | **2,203.4초 (36.7분)** | 약 9분(검색 gemm만) |
| peak RSS | **1,731.7 MB** | 약 1.0 GB, 한도 2.0 GB |
| 라이브러리 행 | 573,533 (3 W 합) | 216,243 (상한, W 구분 없음) |
| query | 66,300 x 14검정 = 928,200 | 66,300 |
| 이웃 행 | 46,410,000 | - |
| 산출물 | 1.2 GB | 0.5~0.7 GB |

검정당 119~181초이고, 표현 B가 A보다 일관되게 30~40초 느리다(Euclidean 전개의 norm + sqrt).
D1 추정이 9분이었던 것은 gemm FLOP만 센 값이기 때문이다. 실제 지배 항목은 D2 설계 §11이 예고한
대로 query별 `argpartition`이다. **느리다는 이유로 규칙(K, stride, 표본 수, 정확 검색)을 바꾸지 않았다.**

---

## 11. D1 이후 고친 계약 누락 3건

실행 전에 Pre-flight 계약을 다시 대조해 D2 구현의 누락을 찾아 고쳤다. 전부 **규칙 변경이 아니라
선언된 계약의 구현**이다.

| # | 내용 | 근거 |
| --- | --- | --- |
| 1 | R11이 정렬·유일성만 보고 stride(`d % 5 == 0`)와 최소 index 60을 검사하지 않았다 | Pre-flight §11.1 R11 |
| 2 | 산출물에 `d_identity` 스탬프가 없었다. parquet은 schema metadata 키 `d_identity`, JSON은 최상위 4필드(`freeze_id`, `freeze_digest`, `grid_digest`, `rules_checksum`) | Pre-flight §9.2 |
| 3 | input mutation을 `F1`로 올렸는데, F1은 "다른 freeze 산출물 혼합" 전용이다. `R2`로 옮겼다 | Pre-flight §11.1 |

추가로 `query_manifest.json`을 넣어 요청문 §30의 산출물 목록을 채웠다(D2 설계 §20.1은 이 내용을
`query_samples.parquet`으로만 두고 있었다).

run identity에도 Pre-flight §10.3이 요구한 항목을 풀어 적었다: `library_policy`(stride, anchor,
min_end_idx, ticker_cap, date_cap, same_symbol, embargo), `top_k`, `label_validity_contract =
d-label-validity-v1`, `implementation`(M0, QUERY_CHUNK, OPENBLAS_NUM_THREADS, numpy, pyarrow).

---

## 12. 테스트

`backend/tests/strategy_d/` **95개 전부 통과** (D1 33개 + D2 62개). 합성 데이터만 쓰고 네트워크,
Drive 접근은 0이다.

| 묶음 | 내용 |
| --- | --- |
| Encoder V1~V7 | 선형 상승/하락 z와 c 값, flat 창(10.0/10.07/7.77), 가격 수준 불변, query=라이브러리 bit 동일, 진폭, 평행 이동, 분할 창, 결측 봉 hard fail |
| Similarity V8~V12 | 자기 자신, 반대 벡터, 진폭, 상수 벡터 hard fail, 동률 순서 |
| Neighbor V13~V20 | same ticker, same FIGI(null 포함), embargo 경계와 +1, 종목 cap, cap 순서 의존(V17b), 날짜 cap, K 미달, 결정적 순서 |
| PIT V21~V25 | 미래 봉 변조, 미래 분할, 미래 재개, raw digest 불일치, 규칙 checksum |
| Mutation T1~T15 | 절단 재실행, 종목 열 삭제(음·양성 대조), embargo 양성 대조, 창 내부 종가 변경, 20D CA 확장 = C 동일성, 경로 분리, import 경계, label 무관 표본 |
| 계약 | artifact identity, read-set 불변, R11 stride/최소 index, full-sort 동치, 2회 run digest |

기대값 25개는 `D_D2_TEST_VECTORS_V1.md`의 값을 그대로 옮겼고, 코드를 쓰기 **전에** numpy로 따로
재현해 문서값과 일치하는 것을 확인했다.

### 12.1 V3 보충

문서는 flat 창의 `np.std(ddof=0)`이 0이 아니라고 적었는데(10.07 x 61 -> 5.3e-15), 값이 부동소수점으로
정확히 표현되는 10.0 x 21에서는 std가 **정확히 0**이다. 테스트는 세 경우를 모두 넣고 std가 0인지
여부를 케이스별로 구분해 확인한다. 어느 쪽이든 `np.ptp == 0`이 `VECTOR_UNDEFINED`를 잡는다는 D2
설계 §7의 논지는 그대로다.

---

## 13. 판정과 다음 단계

**GATE-D2 = PASS.** §1의 15개 조건이 모두 충족됐다.

D2 PASS는 "이웃을 정확하고 결정적으로 PIT-safe하게 찾는다"는 뜻뿐이다. **이웃이 쓸모 있는지에
대해 아무것도 말하지 않는다.** 그 판정은 D4 GATE-D-ALPHA 몫이다.

D3가 받는 것:

```text
data/runtime/strategy_d/runs/dneigh1-fe0362523739/
  COMPLETE.json          <- 이 파일이 없으면 D3는 읽지 않는다
  run_identity.json      <- rules checksum, freeze/grid, code digest, 정책, 구현 상수
  library_manifest.json  query_manifest.json  summary.json  pit_runtime.json
  library_meta_W{20,40,60}.parquet
  query_samples.parquet  query_results.parquet
  neighbors_<test_id>.parquet x 14
```

D3가 할 것: `EmbargoView`를 거쳐 이웃 label(`excess_return_h`, `mfe_h`, `mae_h`)을 계산하고
`S(q)`, `sigma(q)`, N1/N2 기준선을 만든다. 시작 전에 D2 run identity의 rules checksum,
dataset digest, grid가 자기 입력과 같은지 확인하고 다르면 거부한다.

D3가 하지 않을 것: 이웃을 다시 고르기, K나 cap 바꾸기.

---

## 부록: 재현

```bash
cd ~/usb
OPENBLAS_NUM_THREADS=8 PYTHONPATH=backend .venv/bin/python -u \
  -m app.dev.run_strategy_d_neighbors --snapshot-id USB-HIST-V1 \
  --parent-run dpit1-2ec7d609bf56 --audit-dates 12
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/strategy_d -q
```

`--eval-date-limit N`은 앞 N개 평가일만 도는 smoke 옵션이다(라이브러리 상한도 함께 줄어든다).
`OPENBLAS_NUM_THREADS`는 gemm 블록 분할에 영향을 주므로 run identity에 기록되며, 값을 바꾸면
다른 run id가 된다.

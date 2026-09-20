# Strategy D V2-A D2 Results (Structure Analog Engine, GATE-D-V2A-2)

실행 2026-09-20 (집 PC). 단계 **D-V2A-2**. 부모: D1 run `dv2a1-cec5cc9bc674`.
선언 문서: `D_V2A_SCREENING_CONTRACT_V1.md`, `d_v2a_rules_v1.json`(canonical `2b060ceb3474…fe229`).

```text
GATE-D-V2A-2 = PASS

이번 단계가 계산한 것: 구조 라이브러리, 10차원 등가중 Euclidean 거리, Top-50 이웃,
                      full-sort 동치·절단 동치·미래변조 동치, 결정성, 성능
이번 단계가 계산하지 않은 것: future return 값, IC, delta IC, 분위, bootstrap,
                              PASS/BORDERLINE/FAIL 스크리닝 판정
V1 변경 0 · A/B/C/E 변경 0 · 커밋 0 · 푸시 0 · 배포 0
```

---

## 1. START GATE

| # | 확인 | 결과 |
| --- | --- | --- |
| 1 | V2-A rules checksum | `2b060ceb3474…fe229` **MATCH** (`.sha256` 파일에서 읽음) |
| 2 | D1 COMPLETE 존재 | `dv2a1-cec5cc9bc674/COMPLETE.json`, verdict PASS |
| 3 | D1 identity MATCH | COMPLETE token = `run_identity.json` = `cec5cc9bc674eea0…33c0` |
| 4 | freeze / grid / read digest | `9ebd6c29…eafe` / `830744f6…66c9` / `7e790a8d…7d55` 전부 **MATCH** |
| 5 | D1 feature/rank/B0 digest | **9종 전부 재현 일치**(§3) |
| 6 | V1 files/results unchanged | V1 code digest `a8bb64aa…` = V1 D4 run 기록값 (§10) |
| 7 | 다른 세션의 `strategy_d_v2` write | 없음 (전 파일 mtime이 이 세션) |
| 8 | common Store read-set | PRE/POST 동일 (§8) |

---

## 2. 실행 정보

| 항목 | 값 |
| --- | --- |
| HEAD / branch | `4e84ec12a1fa176a19807f0d966f45925623763b` / `main` |
| run id | **`dv2a2-bf9ceb93dd23`** |
| identity digest | `bf9ceb93dd233d08874dc67d3749424fde8a32ba8e8a83e20625f2f9a0ddc96b` |
| parent (D1) | `dv2a1-cec5cc9bc674` / `cec5cc9bc674eea0…33c0` |
| code digest (V2-A 패키지) | `9fc59c5356e58b4f…f41b` |
| feature schema digest | `0b222cf96cccfed2c92a728a4ea133e1685bb851a045939ea02454ac0b59c9ae` |
| snapshot / grid | `USB-HIST-V1`, 2024-09-17~2026-09-16, N=501 |

부모는 CLI 인자로 **명시**한다(`--parent-run-id`). "디렉터리에서 가장 최근 run"에 자동으로 붙는
설계는 무관한 run 하나가 생기는 순간 연구 정체성이 바뀌므로 채택하지 않았다.

---

## 3. 부모 바인딩 (D1 재현 검증)

D1은 행렬이 아니라 **digest**를 발행했다. 따라서 D2는 좌표를 재계산하고, 그 위에 무엇을 쌓기 전에
부모가 기록한 값과 정확히 같은지 확인한다(요청문 §18의 bit-identical 경로).

| 부모 digest | 재현 |
| --- | --- |
| `raw_feature_matrix` `75f6810864c2a9e4…` | 일치 |
| `rank_feature_matrix` `2749496384c2d675…` | 일치 |
| `validity_mask` `c1e02937ab66bc39…` | 일치 |
| `vector_status` `3da68b41adcc0a15…` | 일치 |
| `label_validity_primary` `5e3e426c483d0378…` | 일치 |
| `query_sample` `02b74f44b1640…:0175d3d093aee…` | 일치 |
| `library_eligibility` `43c22b259f37c5e2…:2b0932ccd87598…` | 일치 |
| `b0_rows` `b06da81e77782d61…` | 일치 |
| `b0_strong_rows` `546b99ff24108165…` | 일치 |

**9/9 일치.** 하나라도 어긋나면 R2로 즉시 정지하며, 그 경우 "D1이 검증한 좌표 파이프라인이
아니다"라는 뜻이다.

B0는 이 확인에만 등장하고 값은 함수 밖으로 나가지 않는다. 검색 경로는 B0를 import하지 않는다(§9).

---

## 4. Historical Structure Library

```text
h = 5 · lookback L = 60 · stride 5 · embargo d + 5 <= D - 60
```

| 항목 | 값 |
| --- | --- |
| stride 날짜 | **72** (index 60 ~ 415, 마지막 = `eval_end(480) - 60 - 5`) |
| library rows | **180,947** |
| 제외 | NOT_ELIGIBLE 275,210 / VECTOR_UNDEFINED **0** / LABEL_INVALID 323 |
| 벡터 차원 | 10, 값 범위 [0, 1] (R12로 강제) |
| 고유 composite FIGI | 3,159 / FIGI 없는 행 16,251 |
| 행 순서 | `(end_idx, ticker)` 오름차순 = 선언된 tie-break |
| query 날짜에서 보이는 행 | 최소 **67,604** / 중앙값 123,559 / 최대 180,947 |

D1이 기록한 stride 72·180,947행과 **정확히 같다**(`library_eligibility` digest 일치). 기대값을
코드에 하드코딩하지 않고 매니페스트에서 다시 계산했다.

라이브러리에는 **미래 수익 값이 없다.** 행이 존재한다는 사실 자체가 "이 창의 h=5 라벨이 결정
가능하다"만 말하며, 그 라벨이 얼마인지는 이 단계가 읽지 않는다.

---

## 5. Query 표본

| 항목 | 값 |
| --- | --- |
| 평가 날짜 | 221 (index 260~480) |
| query | **66,300** (날짜당 300) |
| 고유 ticker | 3,331 |
| 벡터 유효 | 66,300 (미정의 0) |
| 표본 digest | D1과 **동일** |

D2는 재샘플링하지 않고 D1의 결정적 표본을 그대로 재구성했다.

---

## 6. 거리와 이웃 선택

| 항목 | 값 |
| --- | --- |
| metric | equal-weight Euclidean, 10차원 |
| rank_score | `-distance` (전 행에서 항등 확인) |
| K | 50 |
| initial M | 400 |
| pool 확장 | **0회** (전 query가 M=400에서 K를 채움) |
| 이웃 행 | **3,315,000** = 66,300 x 50 |
| query당 수락 | 최소=중앙값=최대 **50** |
| embargo 통과 후보 | 최소 67,604 / 중앙값 123,559 |
| 동일종목·FIGI 제외 후 | 최소 67,576 / 중앙값 123,509 (query당 평균 42개 제외) |
| 거리 분포 | 최소 0.0125 / p25 0.2731 / 중앙값 0.3123 / p75 0.3533 / 최대 0.8880 |

`QUERY_CHUNK`는 V1의 50에서 **25로 낮췄다**. 블록 커널의 임시배열이 `chunk x library` double
다섯 벌이라 이 단계의 최대 전이 메모리이기 때문이다. query별 선택은 서로 독립이므로 어떤 답도
바뀌지 않으며, 재현성 계약의 일부이므로 run identity에 기록한다.

### 6.1 커널 정확도 (실측)

블록 커널은 `||q||² + ||l||² - 2q·l` 전개를 쓴다. 이 식은 두 벡터가 거의 같을 때 상쇄가 커서,
동일 벡터에서 0 대신 약 3e-08이 나온다(테스트로 고정). 그래서 **수락된 50개 이웃마다 거리를
직접 다시 계산**해 아티팩트에 `distance_exact`로 함께 싣고, 둘의 차이를 측정값으로 보고한다.

```text
max |expansion - direct| over 3,315,000 accepted neighbours : 1.28e-13
이웃 목록의 순서가 direct 기준으로 뒤집힌 query                 : 0
```

즉 실제 거리대(0.01~0.89)에서는 전개식의 오차가 1e-13 수준이고, **단 한 건의 이웃 목록도
재정렬되지 않았다**. 게이트 조건에 `no_neighbor_list_reordered`로 포함했다.

---

## 7. 동치성 감사

### 7.1 Full-sort 동치 (요청문 §12)

12개 평가일의 **전 query 3,600건**을 최적화 검색과 `select_full_sort`(전수 안정정렬 + 캡)로
각각 선택해 비교했다.

```text
mismatches: 0   (이웃 identity, 순위, 순서 전부 일치)
```

### 7.2 절단 동치 (요청문 §27)

3개 평가일에서 패널을 그 날짜로 **물리적으로 절단**(이후 봉·이후 분할·이후 스냅샷 제거)하고
좌표·라벨유효성·라이브러리·검색을 처음부터 다시 수행했다.

```text
dates 3 · queries 900 · findings 0
비교 대상: 라이브러리 prefix의 end_idx / ticker / figi / 벡터,
          query 벡터, Top-50 identity, 거리, 순서
```

### 7.3 미래 변조 동치 (요청문 §16, §17)

같은 3개 날짜에서 패널 모양은 유지한 채 **query 날짜 이후를 극단값으로 치환**(종가·시가 x100,
고가 x100, 저가 x0.01, 거래량 x500)하고 같은 항목을 비교했다.

```text
dates 3 · queries 900 · findings 0
```

절단과 변조는 잡아내는 실패가 다르므로 둘 다 돌린다. 합성 테스트에는 **한 라이브러리 창이 자기
미래를 못 본다**는 창 단위 진술도 별도로 고정했다(그 ticker의 `d+10` 이후만 변조 -> 그 창의 벡터와
라벨 유효성 불변).

---

## 8. 입력 불변

```text
PRE  read-set digest  7e790a8dcf1db8cee336aa281f5b9b7b8481402bbc1d828f27d19cccb0dd7d55
POST read-set digest  7e790a8dcf1db8cee336aa281f5b9b7b8481402bbc1d828f27d19cccb0dd7d55
D1 부모 아티팩트 3종 sha256: PRE/POST 동일
```

어느 쪽이든 달라지면 `F1 INPUT_MUTATED_DURING_V2A2`로 정지한다.

---

## 9. 결정성

같은 freeze / rules / code / 부모로 D2를 **두 번 완주**했다.

| 아티팩트 | 동일 |
| --- | --- |
| `identity.json` | 동일 |
| `feature_schema.json` | 동일 |
| `parent_d1.json` | 동일 |
| `library_manifest.json` | 동일 |
| `query_manifest.json` | 동일 |
| `status_counts.json` | 동일 |
| `neighbors.parquet` (107.7MB) | **바이트 동일** |
| `summary.json` | `performance` 블록(소요시간·RSS) 외 동일 |
| `COMPLETE.json` | `completed_at` 외 동일 |

run id도 같다(`dv2a2-bf9ceb93dd23`). identity에서 시각·호스트·경로가 빠져 있다는 확인이다.

`neighbors` column digest `961b79c170f08c23…`는 메모리 축소 작업 **전후의 run에서도 같았다**.
즉 §11의 성능 수정이 어떤 이웃도 바꾸지 않았다.

---

## 10. V1 / 타 전략 격리

| 검사 | 결과 |
| --- | --- |
| V1 패키지 code digest | `a8bb64aa52320773…` = V1 D4 run 기록값과 **동일** |
| V1 rules / 문서 sha256 | 변경 없음 |
| V1 run artifacts | 수정 0건 |
| A/B/C/E 파일 | 수정 0건 |
| V1 결과 하드코딩 | 없음(테스트로 고정) |

V1 import는 **화이트리스트**다. D2에서 `neighbor_search`, `similarity`, `artifacts` 세 개가
추가됐고(전부 Reuse Matrix의 REUSE 행), 금지 목록을 테스트에 명시적으로 두어 추가가 흐려지지
않게 했다.

```text
허용  models source universe identity label_extension features(percentile_rank) pit
      neighbor_search similarity artifacts
금지  encoder labels signal evaluation baselines metrics resample gate library config d1~d4
```

`labels`(미래 수익 값 생성)가 금지 목록에 있다는 점이 이 단계의 구조적 alpha firewall이다.

---

## 11. 성능

| 항목 | 값 |
| --- | --- |
| 총 소요 | **380.6초** (load 44.8 / search 216.9 / 감사·아티팩트 나머지) |
| peak RSS | **1,689 ~ 1,714 MB** (한도 2,048MB) |
| 이웃 행 | 3,315,000 |
| `neighbors.parquet` | 107.7MB (zstd) |
| 기타 아티팩트 | 8종 합계 약 13KB |

**첫 완주는 peak RSS 2,961MB로 이 한도를 넘어 FAIL로 기록됐다.** 게이트에 RSS 조건을 넣어 둔
덕분에 판정으로 드러났고, 다음 세 가지로 줄였다. 규칙·K·좌표는 건드리지 않았다.

1. D1 전용 진단용 rank 프레임(좌표 10개분)을 D2에서는 **아예 만들지 않는다**. 이미 할당된 배열을
   나중에 비우는 것은 프로세스의 peak를 낮추지 못한다.
2. `compute_raw`의 롤링 중간배열을 소비 즉시 해제하고, 쓰이지 않던 `open_price` 계산을 제거했다.
   501 x 6,340 패널에서 중간배열 하나가 25MB다.
3. 검색이 끝나면 참조 feature 객체를 **해제한 뒤** 동치 감사를 시작한다. 감사는 자기 패널에서
   좌표를 다시 만들므로 참조본이 필요 없다.

중간 단계(2,058MB)는 여전히 8MB 초과였고, 3번을 추가해 1,689MB가 됐다.

---

## 12. 신규 코드와 테스트

| 모듈 | 줄수 | 역할 |
| --- | --- | --- |
| `schema.py` | 67 | 좌표 순서·수식·스케일링·메트릭을 묶는 feature schema digest |
| `library.py` | 188 | 구조 라이브러리(밀집 행렬 + 컬럼 메타), FIGI 코더 |
| `d2.py` | 848 | 부모 바인딩, 검색, 3종 동치 감사, 아티팩트 |
| (D1에서 수정) `structure_features.py` | +30 | 진단 프레임/raw 보존 옵션, 중간배열 해제 |
| 테스트 `test_strategy_d_v2_d2.py` | 462 | 24 test |

```text
V2-A 패키지 합계 2,709줄 / 테스트 1,337줄 (89 test)
재사용: neighbor_search.select·select_full_sort·greedy_caps·drop_same_symbol,
        similarity.score_block(Euclidean), artifacts.write_table/write_json/column_digest,
        universe, source, identity, label_extension, pit.assert_library_index
```

---

## 13. Regression

| 대상 | 결과 |
| --- | --- |
| `backend/tests/strategy_d_v2` | **89 passed** (features 26 / PIT 29 / D1 10 / D2 24) |
| `backend/tests/strategy_d` (V1) | **167 passed** |
| `backend/tests` 전체 | **1,898 passed / 1 skipped** (508.96s) |

귀속을 구분해 둔다. 다른 세션이 같은 리포에서 `strategy_e1` 계열을 계속 추가하고 있어 전체
스위트 수가 D1 시점 1,841에서 1,898로 늘었다(그 세션 +33, 이번 단계 +24). 실패 0건이므로 귀속
분쟁은 없고, D/V2-A 경로와 겹치지 않는다.

---

## 14. GATE-D-V2A-2

| # | 조건 | 결과 | 근거 |
| --- | --- | --- | --- |
| 1 | rules checksum MATCH | PASS | §1 |
| 2 | D1 parent MATCH | PASS | §3 (9/9) |
| 3 | freeze/grid/read digest MATCH | PASS | §1 |
| 4 | feature schema MATCH | PASS | §2 |
| 5 | structure-distance tests | PASS | 24 test |
| 6 | PIT violations 0 | PASS | §7 |
| 7 | Top-K=50 규칙 정확 | PASS | §6 (전 query 50) |
| 8 | full-sort equivalence | PASS | §7.1 |
| 9 | truncate equivalence | PASS | §7.2 |
| 10 | two-run deterministic digest | PASS | §9 |
| 11 | insufficient-neighbor 비율 | PASS | 0.0 <= 0.05 |
| 12 | input PRE/POST immutable | PASS | §8 |
| 13 | RSS <= 2GB | PASS | 1,689MB (§11) |
| 14 | Alpha metrics calculated = 0 | PASS | §15 |
| 15 | V1/A/B/C files changed = 0 | PASS | §10 |

```text
GATE-D-V2A-2 = PASS
```

---

## 15. Alpha firewall

```text
future labels read   NO
IC                   NO
Q5/Q1                NO
B0 evaluation        NO   (digest 재현 확인만, 값은 함수 밖으로 나가지 않음)
bootstrap            NO
screening decision   NO
```

`neighbors.parquet`의 컬럼은 identity와 유사도뿐이다.

```text
query_date_idx query_date query_ticker_col query_ticker sample_rank rank
library_row neighbor_end_idx neighbor_date neighbor_ticker_col neighbor_ticker
neighbor_figi_code distance distance_exact rank_score pool_m
```

수익·MFE·MAE·라벨 값 컬럼은 존재하지 않으며, 실행 중 가드(`pit.assert_no_label_values`)와
테스트 양쪽에서 확인한다.

---

## 16. 다음 단계

```text
D-V2A-3  STRUCTURE ANALOG SIGNAL
  Top-50 이웃의 excess_return_5를 join해 A(q) = median 생성
  B0를 parent input으로 연결
  아직 IC / delta / Screening Gate 는 계산하지 않는다 (그건 D-V2A-4)
```

D3가 물려받는 값: run `dv2a2-bf9ceb93dd23`, identity `bf9ceb93dd233d08…c96b`,
neighbors digest `961b79c170f08c23…`, feature schema `0b222cf96cccfed2…`,
library 180,947행(h=5), query 66,300, 이웃 3,315,000행.

### 16.1 정리해 둘 것

`data/runtime/strategy_d_v2/runs/`에 이번 단계의 이전 run 3개
(`dv2a2-1f08b8ea3c1a`, `dv2a2-54434657251c`, `dv2a2-32f5e79b71b6`)와 D1의 이전 run 2개가 남아 있다.
전부 코드 digest가 다른 중간본이고 각 D2 run이 107MB parquet을 갖는다. **authoritative는
`dv2a2-bf9ceb93dd23` 하나**이며, D3는 부모 run id를 명시적으로 받으므로 오선택 위험은 없다.
디스크를 비우려면 중간본 5개를 지워도 무방하다(삭제는 사용자 판단).

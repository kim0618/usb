# C-E0 / C-EM 사전등록 V1

- 선언일: 2026-09-18 (KST 10:3x), 결과 확인 전
- 상태: **FROZEN**
- 이벤트 데이터: **0건** (SEC 호출 0회, 로컬 이벤트 파일 없음)
- 기계 판독 정본(이 문서와 충돌하면 JSON이 우선):

| 파일 | canonical sha256 |
| --- | --- |
| `c_e0_rules_v1.json` | `48fc34cb06dd88b1bf3d6c5083366cf768fd269f355363a6d83789f2d0f62c71` |
| `c_e0_event_taxonomy_v1.json` | `6ee5a16a9828d6b1d04d76f005117629fd84341399eeadb243c6b41567a45c6b` |

checksum은 V1과 같은 방식으로 계산한다: `canonical_checksum` = `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)`의 sha256. rules JSON은 taxonomy checksum을 담고 있으므로, taxonomy를 바꾸면 rules checksum도 바뀐다.

초안 `c_v2c_event_taxonomy_draft_v0.json`(`31ca4a75…`)은 이 V1으로 대체된다. 초안은 어떤 통계에도 쓰이지 않았다.

## 1. 동결된 기준선

C-M V1(`c769aea5…`, run `cmsel1-855b6a0c…`, raw `adc4f191…`)은 그대로 둔다. 규칙, 임계값, universe, 피처, 매칭 셀, 통제군, 라벨, 신호 시각 모두 그대로다. C-E0는 이 후보에 이벤트 flag를 붙여서 둘로 나누기만 한다. C-M을 살리려는 파라미터 조정은 금지한다.

공통 스냅샷에서 실행할 때는 이벤트를 조인하기 전에 V1 C-M0 후보 집합(V1 primary 날짜)을 행 단위로 똑같이 재현해야 한다. 재현되지 않으면 중단한다.

**기준 변형은 C-M0 하나.** V1에서 C-M1의 RS 조건은 정보를 거의 더하지 못했고, C-M2는 후보를 55% 줄이면서 Lift도 낮췄다. 변형을 하나로 두면 primary 검정이 정확히 두 개로 유지된다. M1과 M2는 기술통계로만 보고한다.

## 2. 사전 지식 (숨기지 않는다)

- V1과 V2A에서 C-M0 전체 후보의 매칭 대비 종가 초과는 이미 봤다. 1/3/5/10일 각각 -0.10/-0.13/-0.01/-0.06%p이고, CI는 모두 0을 포함한다.
- 그러니 전체 풀의 초과수익은 약 0이다. EM이 양수라면 M_ONLY는 가중치만큼 음수가 되는 구조다. H1과 H2는 서로 상관되어 있다. 그래서 둘을 **동시에** 요구하고, 하나를 다른 하나와 맞바꾸지 않는다.
- 이벤트 통계, 이벤트 건수, 후보와 이벤트의 겹침은 계산한 적도 본 적도 없다. 5일 horizon은 요청에서 정한 것이고 데이터를 보고 고른 것이 아니다.

## 3. 평가 기간

- Primary: V1 primary 신호일 240일(2025-09-18..2026-09-01). 공통 스냅샷이 더 길어도 바꾸지 않는다.
- Secondary: V1 secondary(인덱스 60..N-10), 기술통계만.
- 스냅샷이 V1 grid 밖으로 길면 그 구간은 `C-E0-EXT`로 따로 보고한다. 게이트에는 넣지 않는다.
- Time block: 240일을 같은 크기의 연속 block 4개로 나눈다. V1과 같은 분할이다.

## 4. PIT 계약

| 항목 | 규칙 |
| --- | --- |
| 시각 원천 | EDGAR `acceptanceDateTime`. `filingDate`는 이벤트 시각으로 쓰지 않는다 |
| 정규장 전 접수 | 그 거래일 시가부터 유효 |
| 정규장 중 접수 | 접수 시각부터 유효 |
| 장 마감 후 또는 휴장일 접수 | 다음 거래일 시가부터 유효 |
| 조기 폐장일 | 그날 예정된 마감 시각(13:00 ET) 적용 |
| 접수시각 없음 | 계열 filing인데 filingDate가 [D-3, D+1]이면 `UNKNOWN_PIT` |
| close(D) 이후 공개 | D의 이벤트가 아니다. `post_signal_event_flag`로 기록만 한다 |

**Timezone 감사(필수):** submissions JSON의 `acceptanceDateTime`은 `Z` 접미사가 붙어 나오지만, 실제로 ET인지 UTC인지는 확인되지 않았다. C-E1은 조인하기 전에 30건 이상에서 이 값을 filing의 `ACCEPTANCE-DATETIME` 헤더와 대조해 해석을 확정한다. 장후 filing과 DST 경계 filing을 반드시 포함한다. 설명할 수 없는 불일치가 나오면 `INCONCLUSIVE(PIT_UNCERTAIN)`로 처리한다.

## 5. Event window

- **Primary `W_PRIMARY`**: effective_time이 [open(D-2), close(D)) 안에 있는 경우.
- Secondary(기술통계만): `W_D5` [open(D-5), close(D)), `W_D1` [open(D-1), close(D)), `W_D0` [open(D), close(D)).
- Negative risk 판정에도 `W_PRIMARY`를 쓴다. [D-20, D] 구간의 E4는 `recent_dilution_20`으로 기록만 한다.
- MA target pin은 [open(D-60), close(D))로 본다.

## 6. Event taxonomy (form 유형과 8-K item 번호로만 분류)

| 클래스 | 원천 | 상태 |
| --- | --- | --- |
| E1 MATERIAL_AGREEMENT | 8-K 1.01 | EVENT_PRESENT, 방향 UNKNOWN |
| E2 ACQUISITION_DISPOSITION | 8-K 2.01 | EVENT_PRESENT, 방향 UNKNOWN |
| E3 RESULTS_OF_OPERATIONS | 8-K 2.02 (E3a), 10-Q/10-K/10-KT (E3b) | EVENT_PRESENT, 방향 UNKNOWN |
| E4 FINANCING_DILUTION | 8-K 3.02 (E4a), 424B1/B4/B5 (E4b), S-1/S-3/S-3ASR/F-1/F-3/F-3ASR (E4c), 8-K 1.03/3.01/4.02 (E4d distress) | NEGATIVE_RISK |
| E5 OTHER_MATERIAL | 8-K 1.02, 2.04, 2.05, 2.06, 5.01, 7.01, 8.01 | EVENT_PRESENT, 방향 UNKNOWN |
| MA_TARGET_PIN | SC 14D9(/A), SC TO-T, PREM14A/DEFM14A/PREM14C/DEFM14C, SC 13E3 | 모든 primary cohort에서 제외 |
| UNCLASSIFIABLE | 6-K, 20-F, 40-F | `UNKNOWN_FORM` |
| ROUTINE_IGNORED | 8-K 2.03 단독, 3.03, 4.01, 5.02~5.08, 9.01, S-8, Form 3/4/5, 13D/G 등 나머지 전부 | 무시, 건수만 기록 |

- `POSITIVE_EVENT` 변수는 만들지 않는다.
- 요청 범위를 넘어 하나를 추가했다. E4d(파산, 상장폐지 통지, 재무제표 신뢰불가)다. 이 셋은 구조적으로 부정적이다. 빼면 E5 material event로 EM에 들어가게 된다.
- 424B2(은행 구조화채권), 424B3(재매도·합병 설명서), S-8, 8-K 2.03 단독은 E4에서 뺀다. 2.03 단독은 텍스트를 읽지 않으면 전환사채인지 일반 신용한도인지 구분할 수 없어서다. 전환사채 조달은 구조적으로 3.02 경로로만 잡는다.
- 8-K 1.01에는 ATM 판매계약도 들어간다. 같은 accession이나 같은 window에 3.02 또는 424B5가 있으면 EM_NEGATIVE_RISK로 간다.

**Dedup:** key = (CIK, event_class, event_session).
- 한 8-K에 item이 여러 개면 클래스당 최대 1건이다.
- 같은 key의 accession 여러 개는 1건으로 치고, 가장 이른 시각을 쓴다.
- `/A`는 이벤트를 만들지 않는다(MA pin만 예외).
- E3a와 E3b가 같은 세션이면 1건이다.
- 날짜가 다르면 별개 이벤트다.

**Form code alias 감사:** EDGAR form 이름이 바뀐 경우에만 같은 form의 새 이름을 추가할 수 있다. 추가분은 조인 전에 checksum과 함께 `c_e0_taxonomy_addendum_v1.json`에 기록한다. 클래스, item, 방향은 바꿀 수 없다.

## 7. CIK 매핑

- CIK = D 이전 as_of 중 가장 최근인 Massive CS reference snapshot의 `cik`. V1 eligibility에 쓴 바로 그 스냅샷이다. ticker 문자열은 조인 키로 쓰지 않는다.
- 참고(구조 확인만): 첫 스냅샷 `CS_2024-10-01`은 5,194행 중 5,185행에 `cik`가 있다. 이벤트나 수익률 정보는 아니다.
- CIK가 없으면 `UNKNOWN_MAPPING`이다. NO_EVENT로 처리하지 않는다.
- 인접 스냅샷 사이에 CIK가 바뀌었으면 그 구간은 `UNKNOWN_MAPPING`이다.
- [D-400일, D] 안에 EDGAR filing이 하나도 없는 CIK도 `UNKNOWN_MAPPING`이다.
- submissions 수집이 [D-60, D+1]을 덮지 못했으면 `UNKNOWN_COVERAGE`다.

## 8. Cohort (후보 1건 = 1행, 위에서 먼저 맞는 규칙 적용)

1. `UNKNOWN_MAPPING`
2. `UNKNOWN_COVERAGE`
3. `UNKNOWN_PIT`
4. `UNKNOWN_FORM`
5. `EXCLUDED_MA_TARGET`
6. `EM_NEGATIVE_RISK`: `W_PRIMARY`에 E4가 있는 경우. material event 동반 여부는 따지지 않는다. E4만 있어도 여기로 간다
7. **`EM`**: E1/E2/E3/E5 이벤트가 1건 이상이고 E4가 없는 경우
8. **`M_ONLY`**: E1~E5 이벤트가 없는 경우(무시 대상 filing은 있어도 된다)

각 행에는 다음을 저장한다: `event_count`, `event_types[]`, `event_accessions[]`, `negative_risk_flag`, `negative_risk_types[]`, `ma_target_pin_flag`, `post_signal_event_flag`, `recent_dilution_20`, `routine_count`, secondary window별 상태.

통제군은 V1 C-M0 매칭 통제군을 그대로 쓰고, 이벤트로 분류하지 않는다. E-only cohort와 이벤트 조건부 통제군은 C-E0 범위 밖이다.

## 9. 가설과 지표

| | 내용 | PASS |
| --- | --- | --- |
| **H1** | EM의 5D 종가 초과(매칭 대비) > 0 | 점추정 > 0 이고 95% CI 하한 > 0 |
| **H2** | EM 5D 초과 - M_ONLY 5D 초과 > 0 | 점추정 > 0 이고 95% CI 하한 > 0 |

- Primary outcome: `close_return_5` = C(D+1..D+5 중 마지막 거래 세션) / open(D+1) - 1. split 정규화하고 [-1, 1]로 clip한다.
- Secondary: `close_return_10`. H1_10과 H2_10은 기술통계다. M_ONLY 되돌림(H_reversion, M_ONLY 초과 < 0)도 기술통계다.
- 보조 지표: 1D/3D 종가, MFE5/10, MAE5/10, time to MFE, giveback, 승률, MFE10 ≥10%/≥15% 적중률. MFE는 종가 게이트를 대신할 수 없다.
- 초과 추정량은 V1 `_diff_ci`와 같다. 매칭된 유효 후보에 대해 (Σ close_k - Σ 셀 통제평균) / N을 날짜 단위로 합산한다. H2는 같은 bootstrap draw 안에서 두 cohort의 초과를 뺀다.
- Bootstrap: 240개 신호일에 대한 moving block bootstrap. block 10세션, 10,000회, seed `20260918`, 양측 95% percentile.
- 다중성: H1 AND H2를 모두 요구하는 intersection-union 구조다. 그래서 각각 5% 수준으로 검정하고 Bonferroni는 적용하지 않는다. 10D와 나머지 숫자는 검정이 아니다.
- 비용 참고로 왕복 0.25%p를 옆에 병기한다. 게이트 입력은 아니다.

## 10. 결과를 보기 전의 검사 (실패하면 INCONCLUSIVE로 멈춤)

이 검사들은 cohort 소속과 라벨 유효성만으로 판정하므로, 수익률을 읽기 전에 끝난다. 표본이 부족하다는 이유로 결과를 본 뒤 INCONCLUSIVE를 고르는 일이 구조적으로 생기지 않는다.

| | 조건 |
| --- | --- |
| P1 | timezone 감사 통과 |
| P2 | PIT 감사 위반 0 (이 단계에서 발견된 위반은 버그다. 수익률을 보기 전이므로 고치고 다시 감사한다) |
| P3 | UNKNOWN_* 합계 ≤ C-M0 primary 후보의 10% |
| P4 | EM 매칭 유효(k=5) ≥ 300 |
| P5 | EM unique ticker ≥ 100 |
| P6 | M_ONLY 매칭 유효(k=5) ≥ 300 |
| P7 | 4개 block 각각에서 EM ≥ 30 |
| P8 | V1 primary 240일이 스냅샷에 모두 있음 |

기준 미달일 때 기준을 낮추지 않는다.

**PIT 감사 항목:**
- W_PRIMARY 이벤트는 모두 effective_time < close(D)여야 한다.
- close(D) 이후 filing을 모두 지우고 cohort를 다시 계산해도 결과가 같아야 한다.
- effective_time을 전부 +1세션 옮기면, window 경계를 넘는 후보만 cohort가 바뀌어야 한다.
- close(D)+1초에 합성 filing을 주입하면 감사가 잡아내야 한다(양성대조).
- `/A`는 이벤트를 만들지 않아야 하고, 접수시각 없이 배치된 filing이 없어야 한다.
- CIK의 스냅샷 as_of가 D 이하여야 한다.

수익률 계산 **후에** PIT 위반이 발견되면 이 선언의 게이트는 FAIL이다. 재시도하려면 새로 선언해야 한다.

## 11. GATE-CE0

**PASS: 아래 9개를 모두 충족.**
1. EM 5D 초과 점추정 > 0
2. H1 95% CI 하한 > 0
3. EM - M_ONLY 5D 점추정 > 0
4. H2 95% CI 하한 > 0
5. (EM MAE5 초과) - (M_ONLY MAE5 초과) ≥ -0.02
6. EM-M_ONLY > 0인 block이 4개 중 3개 이상이고, 따로 EM 초과 > 0인 block도 4개 중 3개 이상
7. 표본 P4~P7 충족
8. 집중도:
   - EM 단일 ticker ≤ 5%, 상위 5개 ≤ 15%
   - 단일 날짜 ≤ 5%, 상위 10일 ≤ 25%
   - EM의 10% 이상을 차지하는 클래스는 하나씩 빼도 조건 1과 3의 점추정이 > 0이어야 한다
   - 상위 5개 ticker를 빼도 조건 1과 3의 점추정이 > 0이어야 한다
9. PIT 위반 = 0

→ `GATE-CE0 = PASS`. 이 경우에만 `C-EM Selection V1`을 개발 후보로 올린다. 그때는 새 선언, out-of-sample 요건, 비용 반영이 필요하다.

한 클래스가 EM의 60%를 넘으면 PASS의 적용 범위를 그 클래스로 한정해서 기록한다. "이벤트 일반"이라고 쓰지 않는다.

**INCONCLUSIVE:** P1~P8 중 하나라도 실패한 경우. 표본, coverage, CIK, PIT 불확실, 기간 부족이 여기에 해당한다. 결과를 보기 전에 판정하며, PASS로 해석하지 않는다.

**FAIL:** P는 통과했지만 조건 1~9 중 하나라도 실패한 경우. EM 종가 우위 없음, MFE만 증가, EM ≈ M_ONLY, EM < M_ONLY, 특정 클래스·ticker·기간 의존, 결과 확인 후 발견된 PIT 문제가 해당한다.

H1(조건 1~2)과 H2(조건 3~4)는 각각 PASS/FAIL로 따로 기록한다.

## 12. 종료 규칙

`GATE-CE0 = FAIL`이면 Strategy C 방향성 가설을 폐기한다. H1과 H2가 모두 FAIL이면 종료 규칙이 명시적으로 적용된 사례로 기록한다.

FAIL 이후에는 다음을 금지한다: 임계값 재조정, window 변경, 새 score, 이벤트 선별, primary horizon이나 기준 변형 교체.

Volatility 연구는 별도 이름의 새 전략으로 새로 선언할 때만 할 수 있고, Strategy C를 이어가는 것으로 취급하지 않는다.

## 13. 필수 보고 표

- 주 표: Matched Control / M_ONLY / EM / EM_NEGATIVE_RISK × N, Unique, 5D Close, 10D Close, MFE5, MAE5, MFE10, MAE10
- 증분 표: EM - M_ONLY의 5D·10D 종가 초과, MFE5/10, MAE5/10 (95% CI 포함)
- Time block 표: block별 N, EM 초과, M_ONLY 초과, 차이
- 집중도 표: 클래스별 N, ticker 상위 10과 비중, 날짜 상위 10과 비중, 클래스 비중
- 상태별 건수: 모든 EVENT_STATUS의 건수와 비중
- Secondary window(W_D5, W_D1, W_D0) 버전의 주 표와 증분 표. DESCRIPTIVE로 표시한다

## 14. 실행 순서 (공통 Historical Snapshot ID 확정 후에만)

1. 사용자가 SEC User-Agent 식별자를 정한다. 임의의 이메일은 쓰지 않는다.
2. C-M0 후보 CIK에 대한 SEC event raw store를 만든다. 이 단계에서는 C-M 결과를 읽지 않는다.
3. Form code alias 감사(필요하면 addendum을 checksum과 함께)와 timezone 감사를 한다.
4. 스냅샷에서 V1 C-M0 후보를 재현하고, CIK로 조인하고, 상태를 부여한다.
5. PIT 감사와 P1~P8을 수행한다. INCONCLUSIVE면 여기서 멈춘다.
6. 수익률과 H1/H2를 계산하고 필수 표를 만든다.
7. 이 파일만 보고 GATE-CE0를 판정한다.

산출물은 `data/runtime/strategy_c/e0/runs/<run_id>/`에 둔다. run_id 입력은 rules checksum, taxonomy checksum, snapshot digest, event store digest다. 기존 raw, V1/V2 runs, 공통 store에는 쓰지 않는다.

# C-M PIT Contract V1 (C-P2)

작성 2026-09-17. 대상: `backend/app/backtest/strategy_c_selection/` 선정 연구 코드.
규칙 정본: `c_m_selection_rules_v1.json` (canonical sha256 `c769aea5f25bcc46cfdc40a7d74fe325b5059f630714c007d1285cb2d9865d54`,
2026-09-17 15:38 KST 선언, C 데이터 수집 시작 전).

## 1. 시간 정의

| 항목 | 정의 |
| --- | --- |
| `signal_date` | D. feature가 읽는 마지막 세션 |
| `as_of` | D 정규장 종료(XNYS, 조기폐장 포함) |
| `available_at` | D+1 04:00 ET. 후보가 존재하는 가장 이른 시각(보수) |
| label 시작 | D+1 정규장 시가 `P0` |
| label 창 | D+1..D+k 세션. D의 고가/저가/종가는 포함하지 않음 |

## 2. 분할 처리의 한 가지 형태

`F(t)` = 해당 티커에서 실행일 `<= t`인 분할들의 가격 계수 곱(`split_from / split_to`).
feature는 `x(t)/F(t)`(가격), `v(t)*F(t)`(주식수)의 **비율로만** 계산한다. `F(t)`는 t보다 늦은
정보를 쓰지 않으므로 D 이전 두 시점의 비율은 D 시점 분할 기준과 같다. 미래 분할은 D 행에 들어갈
경로가 없다.

label은 같은 `F`를 D 이후 행까지 읽는다(경제적 가격 조정). 코드 경로가 다르다: `labels.py`만
D 이후 행을 읽고, `features.py`는 `labels.py`를 import하지 않는다(AST 테스트).

## 3. Feature별 계약

lookback 표기는 세션 인덱스 기준. 모든 값은 fraction.

| Feature | as_of | available_at | lookback | PIT risk | status |
| --- | --- | --- | --- | --- | --- |
| close (raw) | D close | D+1 04:00 ET | D | 없음 | INCLUDE |
| return_1d / 3d / 5d | D close | D+1 04:00 ET | D-k..D | 분할 미기록 시 가짜 수익률 | INCLUDE, CA 제외 규칙 적용 |
| volume (raw) | D close | D+1 04:00 ET | D | Massive 일봉 volume은 완료 세션 기준(Kiwoom 20:19 ET 부분값과 다름) | INCLUDE |
| rvol_20 | D close | D+1 04:00 ET | D-20..D (평균은 D-20..D-1) | 분할 시 주식수 기준 변화 | INCLUDE (`v*F`) |
| volume_z_20 | D close | D+1 04:00 ET | D-20..D | 동일 | INCLUDE |
| dollar_volume | D close | D+1 04:00 ET | D | 없음(분할 불변) | INCLUDE |
| adv20_dollar | D-1 close | D+1 04:00 ET | D-20..D-1 | 없음 | INCLUDE (hard filter, bucket) |
| dollar_volume_change | D close | D+1 04:00 ET | D-20..D | 없음 | INCLUDE |
| atr_pct | D-1 close | D+1 04:00 ET | D-15..D-1 | D 제외(신호일 변동이 매칭 bucket을 오염하지 않게) | INCLUDE |
| rs_spy_1d / 3d / 5d | D close | D+1 04:00 ET | D-k..D, SPY 동일 | SPY 결측 세션 | INCLUDE (결측이면 NaN, 후보 불가) |
| distance_52w_high | D close | D+1 04:00 ET | D-251..D (최소 240봉) | 미래 고가 사용 | INCLUDE (M2) |
| breakout_52w | D close | D+1 04:00 ET | D-251..D-1 | 동일 | 기록만(규칙 입력 아님) |
| split_flag / reverse_split_flag | D | D+1 04:00 ET | (D-20, D] 실행 분할 | 분할 발표일 없음, 실행일 기록만 사용 | INCLUDE (제외 규칙) |
| ca_suspect | D close | D+1 04:00 ET | D-19..D 종가비 | 미기록 분할 | INCLUDE (제외 규칙) |
| price / atr / adv20 bucket | D | D+1 04:00 ET | 위 값 | 없음 | INCLUDE (매칭) |
| CS universe 소속 | 분기 첫 세션 스냅샷 | 스냅샷 as_of | as_of <= D 중 최신 | 스냅샷 사이 신규 상장은 다음 분기까지 누락(보수), `type` 분류는 ASSUMED_STATIC | INCLUDE (PARTIAL PIT) |
| Market cap | - | - | - | ticker details `date` 시총은 그날 종가 x 주식수, 전 종목 일별 조회 불가 | **EXCLUDE** |
| Sector / SIC / Peer | - | - | - | 날짜별 SIC PIT 미검증, related-companies 429 미검증 | **DEFER (C-M3)** |
| Ticker change 연결 | - | - | - | 종목별 events 호출 필요 | **DEFER** (새 티커는 이력 부족으로 자동 비적격) |
| News / Financials / Earnings | - | - | - | C-E 범위 | **EXCLUDE (이번 범위)** |

## 4. Universe 계약

1. D의 universe = D 이전 최신 CS 스냅샷(`active=true` as of 스냅샷 날짜, 이후 상폐 종목 포함) 중
   `primary_exchange ∈ {XNYS, XNAS, XASE, ARCX, BATS}`, D에 봉이 있는 티커.
2. 현재 상장 목록을 쓰지 않는다. 상폐, 거래 중단 티커를 데이터에서 지우지 않는다.
3. OTC는 grouped daily 기본값(`include_otc=false`)과 스냅샷 `market=stocks`로 제외.
4. hard filter(`RESEARCH_BASELINE_NOT_OPTIMIZED`): close(D) >= $3, ADV20(D-20..D-1) >= $5M.
5. 이력: base = D-20..D 전 세션 봉 + 첫 봉이 D-60 이전. M2 = 추가로 첫 봉이 D-251 이전, D-251..D 중 240봉 이상.
6. CA 제외: split_flag 또는 ca_suspect인 ticker-date는 후보와 대조군 **모두**에서 제외.

## 5. Label 계약

- `P0 = open(D+1)/F(D+1)`. D+1 봉이 없으면 `NO_ENTRY_BAR`로 모든 horizon 제외.
- MFE/MAE/종가수익률은 D+1..D+k의 `x/F`로 계산. 창 안 분할은 경제적으로 조정된다.
- D+k 이후 봉이 데이터에 없으면 horizon k는 `DISAPPEARED`로 제외(상폐, 티커 변경 포함). 그룹별 비율 보고.
- D..D+10 사이 분할조정 후 종가비 또는 시가/전일종가비가 3배 이상 또는 1/3 이하이면 `LABEL_CA_SUSPECT`로 제외(양 그룹 대칭), 비율 보고.
- 종가수익률 평균은 [-100%, +100%]로 clip 후 계산.

## 6. Mutation tests

| # | 테스트 | 합성 fixture (`tests/strategy_c/test_strategy_c_selection_pit.py`) | 실데이터 감사 (`pit_audit.py`) |
| --- | --- | --- | --- |
| 1 | 미래 bar 삽입/변조 -> D 신호 불변 | `test_1_future_bar_insertion_does_not_change_signal` | `future_bars` (D 이후 전 행 변조) |
| 2 | 미래 split 적용 -> 과거 feature 불변, label은 경제적 조정 | `test_2_future_split_...` | `future_splits` (200종목 가짜 분할) |
| 3 | 이후 상폐 종목 삭제 -> 다른 종목 결과 불변, 상폐 종목은 D에 후보로 존재 | `test_3_ticker_that_later_disappears_...` | `later_disappeared_removed` + survivorship 카운트 |
| 4 | 미래 52W High 감지 + 과거 고가는 반영(양성 대조) + 252세션 밖은 무시 | `test_4_52w_high_...` | `positive_control_changed_cells` |
| 5 | 후보 생성 시각 이전 데이터만으로 재실행 = 전체 실행 | `test_5_truncated_rerun_equals_full_run` | `truncation` (세션/분할/스냅샷 절단) |
| 6 | 동일 입력 2회 -> digest 동일 | `test_6_same_input_twice_...` | run의 `determinism` (후보표 sha256 2회) |
| + | 역분할 착시 차단(기록 있으면 수익률 정상+제외, 없으면 ca_suspect) | `test_reverse_split_is_not_momentum` | - |
| + | label은 D+1 시가부터, D 고가 무시 | `test_labels_start_after_d_and_use_d_plus_1_open` | - |
| + | 규칙 파일 변경 시 실행 거부 | `test_rules_checksum_is_the_declared_one` | `load_rules()` |

PIT violation = 실데이터 감사 불일치 셀 수 + (결정성 실패 1) + (양성 대조 미감지 1). 게이트 조건 9는 이 값이 0일 때만 통과.

## 7. 알려진 한계 (PIT가 아니라 데이터 한계)

- Massive 분할 기록에는 발표일이 없다. 실행일 기준으로만 쓰며, 실행일 오기(1일 차이)는 CA 제외 규칙으로만 방어한다.
- 스냅샷 `type`, `primary_exchange`는 스냅샷 날짜 기준 값으로 가정한다(ASSUMED_STATIC).
- 티커 재사용(상폐 후 다른 회사가 같은 티커)은 구분하지 않는다. 20세션 연속 봉 요구로 영향은 제한적이다.
- Massive 일봉 volume 소수(fractional share) 포함. 비율 feature에만 쓰므로 영향 없음.

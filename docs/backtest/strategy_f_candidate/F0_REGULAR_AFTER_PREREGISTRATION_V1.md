# F0 REGULAR → AFTER-HOURS PREREGISTRATION V1

- 연구: Strategy F / `REGULAR_TO_AFTER_HOURS_V0` / gate `F0_REGULAR_AFTER_PREVALIDATION`
- 상태: **FROZEN** (2026-09-27 KST). `frozen_before = F0_RESULT_EXECUTION`
- 정본: `f0_regular_after_rules_v1.json`. 이 문서와 JSON이 다르면 JSON이 우선한다. 체크섬은 `f0_regular_after_rules_v1.sha256`에 있다.
- 코드 가드: `backend/app/backtest/strategy_f0_regular_after/config.py::DECLARED_RULES_CHECKSUM`. JSON이 한 글자라도 바뀌면 `load_rules()`가 `RulesChanged`로 멈춘다.
- 선행 감사: F0 PREVALIDATION READINESS AUDIT (2026-09-27) = `READY_WITH_LIMITATIONS`

이 문서를 쓰는 동안 F0의 feature 값, return, MFE/MAE, baseline, bucket, bootstrap, tail 통계는 **하나도 계산하거나 열람하지 않았다.** 결과를 본 뒤 이 규칙을 바꾸면 그것은 다른 연구이고, 새 파일과 새 체크섬이 필요하다.

## 1. 질문

세션 D의 정규장 정보(16:00 ET 이전에 시작한 봉)와 D-1까지의 일봉·reference만으로, 같은 날 16:05→17:00 ET 장후 구간에서 큰 상승 excursion이 나올 확률과 기대 수익을 같은 PIT universe의 체결 가능한 baseline보다 의미 있게 높일 수 있는가.

PASS가 뜻하는 것은 `Strategy F - PROMOTED TO DEVELOPMENT`뿐이다. alpha 승인, 매매 규칙, 체결·비용 주장, Paper 승인 중 어느 것도 아니다.

## 2. 연구 창

- **Primary:** 2026-04-20 ~ 2026-09-16, XNYS 104세션(목록은 JSON에 고정). 이 창에는 조기폐장일도 DST 전환도 없다. 조기폐장일이 있었다면 모든 표본에서 제외하는 것이 규칙이다.
- **2년 장기 표본:** 30종목(V1)과 U1 136종목. `DIAGNOSTIC_ONLY`이며 판정을 바꿀 수 없다. 사후에 고른 대형주 표본이라 PIT universe가 아니다.

## 3. 입력

- **분봉:** Common Raw, `MASSIVE_TICKER_AGGREGATE adjusted=false`. 정본은 Drive다. 로컬 미러는 모든 페이지의 sha256이 Drive ledger와 같을 때만 읽는다. legacy parquet은 쓰지 않는다.
- **일봉·reference·splits:** `data/runtime/strategy_c/raw`(STRATEGY_C_RAW_FREEZE_V1). 로더는 `strategy_c_selection.panel.load_panel`을 수정 없이 쓴다.
- **시간:** 봉의 t는 봉 **시작** 시각(UTC ms)이다. zoneinfo로 ET로 바꾸고, m = 자정 이후 분으로 센다.
- **Provider API 호출은 금지한다.**

## 4. Universe

`STRATEGY_E_TRADING_UNIVERSE_V1_1`, 즉 `app.strategy_e_v1_1.universe.daily_eligibility`를 수정 없이 쓰고 기준값도 바꾸지 않는다. 호출 방식은 `strategy_e_r3.build.daily_sessions`와 같다.

적격 조건:
- CS이고 XNAS/XNYS/XASE 상장
- D-1 이전 최신 분기 CS snapshot에 포함
- close(D-1) ≥ $5
- 20세션 달러거래량 중앙값 ≥ $5M
- 20세션 중 거래가 있는 세션 ≥ 15
- (D-1, D]에 분할 실행이 없음
- D-1까지의 정보만 사용

`TICKER_SUSPECT`(composite_figi가 직전 snapshot 대비 바뀐 경우)는 진단 플래그로만 붙이고 행을 제외하지 않는다.

LIMITATION으로 기록하는 것: list_date 없음, 티커 재사용 미감지, reference 분기 주기, 분할 공시일 없음, CON 분봉 없음.

## 5. 정보 컷오프

Feature의 절대 컷오프는 **15:59:59 ET**다.

Feature에 쓸 수 있는 봉:
- D의 봉 중 `570 ≤ m < 960`(09:30~15:59 시작)인 것
- RVOL 분모에 한해, primary 창 안에서 D보다 앞선 세션의 정규장 봉

Feature에 쓸 수 없는 것:
- **16:00 봉(POSTMARKET, 종가 경매 가격 혼재)**
- 16:01 이후 봉, 장후 가격, 장후 거래량
- D의 공식 종가
- D 이후 일자의 daily/reference

SEC·뉴스는 signal에 쓰지 않는다.

## 6. 정규장 정의와 feature

**정규장 기준**
- 정규장 종가 proxy = **15:59 봉 close**(c1559).
- 15:59 봉이 없으면 `FEATURE_INVALID_NO_1559`로 처리하고, 그 행은 baseline과 모든 H에서 빠진다.
- 16:00 open이나 공식 종가로 대체하지 않는다.

**vw 규칙**
- v>0인 정규장 봉은 모두 유한한 vw가 있어야 한다.
- 하나라도 없으면 `VWAP_UNAVAILABLE`로 처리하고, vw를 쓰는 feature는 전부 NaN이 된다. 대체 가격은 두지 않는다.

**표기**
- reg = D의 정규장 봉
- DV(S) = Σ v·vw
- H_reg / L_reg = 정규장 최고가 / 최저가
- P(t) = 시각 t의 유효가격. `t-10 ≤ m < t`인 마지막 봉의 close이고, 그런 봉이 없으면 NaN
- prev_close = grouped daily close(D-1). raw 값이며, universe가 (D-1, D] 분할을 제외하므로 그대로 쓴다.

| Feature | 정의 |
|---|---|
| day_return | c1559 / prev_close − 1 |
| regular_dollar_volume | DV(reg) |
| regular_RVOL | DV(reg, D) / median(DV(reg, S))<br>S = primary 창 안에서 D 이전, DV>0인 최근 최대 20세션. 5세션 미만이면 NaN<br>2026-04-20 이전 분봉은 읽지 않으므로 창의 처음 5세션에는 RVOL이 없다 |
| close_vs_VWAP | c1559 / VWAP_reg − 1, VWAP_reg = DV(reg) / Σv |
| position_in_day_range | (c1559 − L_reg) / (H_reg − L_reg), H=L이면 NaN |
| distance_to_day_high | c1559 / H_reg − 1 |
| return_1500_1600 | c1559 / P(15:00) − 1 |
| return_1530_1600 | c1559 / P(15:30) − 1 |
| return_1545_1600 | c1559 / P(15:45) − 1 |
| volume_1500_1600 | DV(15:00 ≤ m < 16:00), 달러거래량 |
| last30m_volume_share | DV(15:30~15:59) / regular_dollar_volume |
| last15m_return | **return_1545_1600의 ALIAS**(같은 값). 단일 feature 분석에서는 한 번만 보고한다 |
| last15m_volume_share | DV(15:45~15:59) / regular_dollar_volume |
| relative_strength_vs_SPY | day_return(종목) − day_return(SPY). SPY도 같은 식과 같은 컷오프를 쓴다 |

**결측 처리**
- 15:59 봉이 있는 행은 feature-valid다.
- 개별 feature가 NaN이면 그 feature를 쓰는 H에서만 빠지고(`FEATURE_MISSING_<name>`) baseline에는 남는다.
- 대체값을 채워 넣지 않는다.

## 7. 체결 계약

**진입**
- 결정 시각은 16:05 ET다.
- **16:05 정각 봉(m=965)이 있어야 하고, 진입가는 그 봉의 OPEN**이다.
- 16:05 봉이 없으면 `NO_TRADE`다. 16:06 이후 봉으로 대신 진입하지 않는다.

**청산 (목표 분 T 공통)**
- `[T−10, T−1]` 안에서 마지막 봉의 close를 청산가로 쓴다.
- 그 안에 봉이 없으면 `UNRESOLVED_EXIT`이고, 해당 horizon의 실현 표본에서 빠진다.
- 더 오래된 봉이나 T 이후의 봉은 쓰지 않는다.
- `exit_age_seconds = T·60 − (m_exit+1)·60`을 행마다 기록한다.

**Horizon**
- **Primary:** 17:00(T=1020, 창 16:50~16:59)
- **Secondary(진단 전용):** 16:30, 18:00, 20:00. 같은 청산 원칙을 쓴다.
- **16:15 진입 진단:** 16:15 정각 봉 open으로 진입하고 청산은 17:00 규칙을 쓴다.
- Secondary와 16:15 진단은 판정을 바꿀 수 없다.

**수익과 경로**
- MFE / MAE = 16:05 봉부터 청산 봉까지의 max(high) / min(low) 대비 진입가. 청산 봉 이후는 읽지 않는다.
- gross = 청산가 / 진입가 − 1
- net = gross − cost_bp / 10000(왕복 비용을 한 번 차감)

## 8. 표본

- **Baseline:** primary 세션 × universe 적격 × 15:59 봉 있음 × 16:05 봉 있음 × 17:00 청산 resolved를 모두 만족하는 symbol-session 전체. H 행도 포함한다. 장후 거래가 없는 종목은 baseline에 들어가지 않으므로 H와 체결 가능성 정의가 같다.
- **H 표본:** baseline 중 그 H가 쓰는 feature가 모두 유한하고 모든 조건을 만족하는 행.
- **상태 코드와 coverage 리포트는 필수:** NOT_ELIGIBLE, NO_MINUTE, FEATURE_INVALID_NO_1559, VWAP_UNAVAILABLE, NO_TRADE, UNRESOLVED_EXIT, RESOLVED. exit_age_seconds 분포도 함께 보고한다.

## 9. H1~H5 (고정, 모두 `>=` 포함 비교)

| H | 이름 | 조건 |
|---|---|---|
| H1 | STRONG_REGULAR_MOMENTUM | day_return ≥ 0.03, position_in_day_range ≥ 0.80 |
| H2 | CLOSING_STRENGTH | return_1530_1600 ≥ 0.01, close_vs_VWAP ≥ 0.005 |
| H3 | VOLUME_PLUS_STRENGTH | regular_RVOL ≥ 2.0, position_in_day_range ≥ 0.80 |
| H4 | RELATIVE_STRENGTH | relative_strength_vs_SPY ≥ 0.02, return_1530_1600 ≥ 0.005, regular_dollar_volume ≥ 20,000,000 |
| H5 | AGGRESSIVE_TAIL_SETUP | day_return ≥ 0.05, regular_RVOL ≥ 2.0, position_in_day_range ≥ 0.90, return_1530_1600 ≥ 0.01 |

## 10. 단일 feature 분석 (진단 전용)

- 13개 feature를 분석한다(ALIAS 1개는 제외).
- baseline 행 중 값이 유한한 것을 합쳐서 Q1~Q5로 나눈다.
  - 경계 = `numpy.quantile(x, [.2,.4,.6,.8])`(linear)
  - bucket = `searchsorted(edges, x, 'right') + 1`
- 경계는 feature 값만으로 정하고 outcome은 읽지 않는다.
- bucket마다 보고하는 것: N, mean, median, win rate, P(r≥1%), P(r≥2%), P(MFE≥3%), P(r≤−2%), 평균 MFE/MAE, 단조성, Q5−Q1.
- threshold 탐색이나 변경에 쓰지 않는다.

## 11. Outcome 지표

- **중심:** mean, median, win rate(gross>0)
- **양의 tail:** P(r ≥ +0.5/1/2/3/5%)
- **음의 tail:** P(r ≤ −0.5/1/2/3%)
- **경로:** MFE, MAE 분포와 P(MFE ≥ 2/3/5%)
- **Primary tail endpoint:** `P(MFE ≥ +3%)`. 나머지는 supporting 지표다.
- 비용 시나리오를 명시하지 않은 값은 gross다.

## 12. Gate (H마다)

**Sample**
- trades ≥ 300, 고유 종목 ≥ 100, 고유 세션 ≥ 60.
- 미달이면 성과와 무관하게 `INSUFFICIENT_SAMPLE`이다.

**Statistical**
- session-cluster bootstrap: 104세션을 복원추출하고, 뽑힌 세션의 모든 행을 합친다.
- 5000회, seed **2026092701**. `default_rng(seed).integers(0,104,(5000,104))` 행렬 하나를 모든 H와 지표에 공통으로 쓴다.
- 신뢰구간은 percentile(linear) 방식이다.
- Primary 지표는 tail_lift = P_H(MFE≥3%) − P_base(MFE≥3%)다.
- **99% CI 하한 > 0**이어야 통과한다. mean_lift의 95%·99% CI도 함께 보고한다.

**Tail**
- tail_lift ≥ +0.02(2%p)
- P_H / P_base ≥ 1.50(P_base=0이면 P_H>0일 때만 통과)
- Statistical 통과

**Mean / Median**
- mean_lift(gross) > 0
- H의 median gross ≥ −0.25%

**Downside** (P(r ≤ −2%) 기준)
- H가 baseline보다 +2%p 이상 나쁘거나 baseline의 1.5배를 넘으면 FAIL(P_base=0이면 P_H>0일 때 FAIL).
- MAE 분포도 보고한다.

**Extreme removal**
- gross 수익 상위 top1, top5, top1%(ceil(0.01·n))를 제거한다. 제거 대상은 gross>0인 거래뿐이고, 동률은 symbol 오름차순 다음 session 오름차순으로 정한다. 제거한 행은 baseline에서도 뺀다.
- top1% 제거 후에도 mean_lift > 0 **이고** tail_lift > 0이어야 한다.

**Concentration**
- 기준은 50bp net 기준 티커별 합계이고, 비중은 양수 합계에 대한 비율이다.
- top1 ≤ 15%, top5 ≤ 40%. top10 비중, HHI, 고유 종목 수도 보고한다.
- 양수 합계가 0이면 FAIL.
- leave-top-10-tickers(H와 baseline에서 모두 제거) 후에도 mean_lift > 0이어야 한다.

**Time consistency**
- 104세션을 시간순 `array_split`으로 4블록(26세션씩) 나눈다.
- mean_lift > 0인 블록 ≥ 3 **이고** tail_lift > 0인 블록 ≥ 3이어야 한다.
- H 거래가 없는 블록은 양수로 치지 않는다.

**Cost**
- 이 비용은 **cost stress assumption**이다. 호가 데이터가 없으므로 실제 비용 추정이 아니다.
- 왕복 10/20/30/50/75/100bp 전부 계산한다. primary는 50bp다.
- break_even_cost_bp = 10000 × H의 gross 평균. **50 이상**이어야 통과한다.

**PIT**
- PIT-1~3을 모두 통과해야 한다.

## 13. Matched control (보고 항목, gate 아님)

- 같은 세션 안에서 CEM으로 매칭한다. `strategy_e1_h5_confirm.matching`의 셀 ID·매칭률 규약을 따르고, 셀 변수는 F용으로 정한다.
- 셀 변수(구간은 하한 포함·상한 제외):

| 변수 | 경계 |
|---|---|
| prev_close | 5 / 10 / 20 / 50 / 100 / ∞ |
| 20세션 달러거래량 중앙값 | 5M / 20M / 50M / 200M / ∞ |
| 당일 regular_dollar_volume | 0 / 5M / 20M / 100M / ∞ |

- 대조군은 같은 세션·같은 셀의 non-H baseline 행이다. gross와 1[MFE≥3%]의 차이를 매칭률과 함께 보고한다.
- 16:00 이후 유동성은 매칭 변수로 쓰지 않는다. 16:00~16:04 활동을 넣은 매칭은 진단으로만 보고한다.
- 판정을 바꾸지 않는다.

## 14. 진단 (판정 불변)

- **Event:** C-E0 SEC store에서 [D-1 16:00, D 17:00) ET 사이에 8-K item 2.02가 접수됐으면 EVENT다. NON_EVENT는 store가 그 CIK와 날짜를 커버할 때만 붙인다. 그 외에는 `UNKNOWN_EVENT_STATUS`이고, 이것을 NON_EVENT로 간주하지 않는다.
- **16:00 경매:** 15:55~15:59 움직임, 16:00 봉의 open/close와 c1559 비교, 16:00~16:04 움직임.
- **기타:** TICKER_SUSPECT 분할, 장기 표본.

## 15. PIT 감사 (outcome 계산 전에 실행, 실패하면 판정 없이 중단)

- **PIT-1 Feature cutoff poison:** D의 m≥960 봉과 D 이후 세션의 모든 봉을 양수 잡음으로 바꾼다(seed 2026092711). [960,1200)의 빈 분에는 합성 봉을 추가한다. 그래도 feature(NaN 위치까지 비트 단위로), H1~H5 mask, 유효 상태가 모두 같아야 한다.
- **PIT-2 Daily/reference poison:** primary 12세션(index `round(linspace(0,103,12))`)에서 D 이후 grouped daily, D 이후 snapshot, D 이후에 실행되는 분할을 덮어쓴다(seed 2026092712). 그래도 universe, prev_close, feature, mask가 같아야 한다.
- **PIT-3 Synthetic leak:** 16:05 open을 읽는 테스트용 day_return 변형을 PIT-1에 넣는다. PIT-1이 이것을 반드시 잡아내야 하고, 못 잡으면 감사 자체가 무효이므로 중단한다.

## 16. 무결성

**Critical** (하나라도 있으면 PASS 불가, INCONCLUSIVE(DATA_INTEGRITY))
- 페이지 sha 불일치
- 값이 다른 중복 timestamp
- F0가 쓰는 봉의 invalid OHLC 또는 0 이하 가격
- 분 경계에 맞지 않는 timestamp
- rules 체크섬 불일치

**보고만 하는 것**
- 동일값 중복(제거하고 개수를 보고)
- 20:00 이후 봉
- TICKER_SUSPECT

## 17. 판정 계약

사용하는 판정은 PASS / INCONCLUSIVE / FAIL 세 가지다. **BORDERLINE은 쓰지 않는다.**

**H별 판정** (위에서부터 순서대로 적용)
1. **INSUFFICIENT_SAMPLE:** sample gate 미달
2. **H_PASS:** sample, tail, statistical, mean/median, downside, extreme, concentration, time, cost gate를 모두 통과
3. **UNDERPOWERED:** 99% CI 조건만 빼고 모든 gate를 점추정으로 통과했고, tail_lift의 99% CI 반폭이 0.02 이상
4. **H_FAIL:** 그 외

**F0 판정**
- **PASS:** H_PASS가 하나 이상이고, PIT-1~3을 통과했고, critical 무결성 실패가 없다.
- **INCONCLUSIVE:** H_PASS가 없고, 다음 중 하나에 해당한다.
  - 모든 H가 INSUFFICIENT_SAMPLE
  - UNDERPOWERED인 H가 있음
  - baseline에서 resolved 행이 있는 세션이 60개 미만(EXECUTION_RESOLUTION_INSUFFICIENT)
  - critical 무결성 실패
- **FAIL:** 위 INCONCLUSIVE 조건 중 어느 것도 해당하지 않고 H_PASS도 없다.

Secondary horizon, 16:15 진단, 단일 feature 분석, matched control, event, 16:00 경매 진단, 장기 표본은 FAIL을 구제할 수 없다.

## 18. 승격

- PASS일 때만 `Strategy F - PROMOTED TO DEVELOPMENT`가 된다.
- 이후 순서: F-D0 Trading Preregistration → F-D1 Signal → F-D2 Execution → F-D3 Exit → F-D4 Cost → F-D5 Risk/Sizing → F-D6 Trading Backtest.
- F0 PASS는 Paper 승인이 아니다. Paper는 Trading Backtest PASS 이후에만 검토한다.

## 19. Run identity

- run id = `f0-<digest[:12]>`
- digest 구성요소: rules canonical checksum, `app.backtest.strategy_f0_regular_after` code digest, 분봉 read-set(읽은 페이지 file_sha256), 일봉·reference read-set, primary 세션 목록
- git HEAD는 기록만 하고 digest에는 넣지 않는다.
- 같은 identity로 두 번 실행하면 결과 artifact가 바이트 단위로 같아야 한다.

## 20. 금지

- Provider API 호출
- 결과를 본 뒤 threshold, 창, horizon, 비용, gate를 바꾸는 것
- universe 재선택
- 결과를 근거로 feature를 추가·삭제하는 것
- optimizer 또는 threshold 탐색
- A/E의 코드, signal, runtime, 설정 변경

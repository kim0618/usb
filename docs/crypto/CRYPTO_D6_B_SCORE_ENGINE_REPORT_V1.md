# US-B CRYPTO D6-B Score Engine 구현 보고 V1

작성 2026-09-28. **구현 단계 보고서다.** 백테스트 0, PnL 0, 성과 지표 0, 주문 0, 배포 0, commit/push 0.

동결 계약 `CRYPTO_D6_AUTO_STRATEGY_DESIGN_CONTRACT_V1.md`
(sha256 `1a6bcaa51d9302850d14827618bcc5d32709f768f324f425e401fac8a622cc0d`, 작업 시작·종료 시 재계산해 일치 확인),
기계본 `data/research/crypto/d6/strategy_contract_v1.json`
(sha256 `db2cb57f86ff6eb2c8c5a7c39cdece624a0fab505e11ed9e53a3860334e5cbe4`).

---

## 0. 판정

**D6-B = PASS.**

의미는 하나다: **D6-A 계약을 의미 변경 없이 결정론적으로 구현했고, D6-C를 오염 없이 수행할 준비가 됐다.**
전략이 좋다는 뜻이 아니다. 이 단계는 성과를 보지 않았고, 볼 수 없게 만들어 두었다.

---

## 1. 모듈 구조

`backend/app/crypto/research/d6/` (신규, 1,441줄)

| 파일 | 역할 |
|---|---|
| `contract.py` | 동결 계약 로더. hash 검증, 구조 검증, 문자열 바인딩(4절) |
| `model.py` | 입력·출력 dataclass. 전부 frozen, 전부 주입식 |
| `features.py` | feature 4개 + D5 bucket 규칙 + 변동성 라벨 |
| `filters.py` | 하드필터 13개 |
| `score.py` | category 4개 + LONG_SCORE + 필수조건 + separation |
| `sizing.py` | 손절 거리 + 위험예산 사이징 |
| `exits.py` | 청산 의사 평가기 |
| `decision.py` | 결정 조립 + 출력 스키마 |
| `data.py` | D2 정본에서 PIT-safe 입력 구성 |
| `runner.py` | 오프라인 CLI (샘플 timestamp만) |

**import 방향은 한 쪽이다.** 이 패키지는 `..dataset`(D5 로더)만 읽고, `app.crypto.paper`·
`app.crypto.terminal`에서 아무것도 import하지 않는다. 기존 crypto 엔진·연구 모듈 **수정 0줄**.

---

## 2. 입력 데이터

계약 Z2가 허용한 D2 정본 5종뿐이다. D5·D5.1이 쓴 같은 파일·같은 checksum을 `dataset.load()`로 읽는다.

| 데이터 | PIT 규칙 |
|---|---|
| kline 1m | bar 종가 시각 |
| mark 1m | bar 종가 시각 |
| index 1m | bar 종가 시각 |
| open interest 5m | **기록 시각 + 5분** (D2 규칙, D5 grid가 이미 적용) |
| funding | 정산 시각 |

**쓰지 않은 것**: D5.2 외부 데이터(Binance·OKX·COIN-M), 청산 forward 수집분, AOA 원장.
`test_d6_isolation.py`가 import 그래프와 문자열 리터럴로 검사한다.

`data.py`가 D5 grid에 없는 두 값을 **과거 행만으로** 파생한다.

| 값 | 용도 | 파생 방법 |
|---|---|---|
| `oi_record_ts` | H2 | `dataset._asof`와 같은 규칙, 값 대신 원본 stamp를 남김 |
| `next_funding_ts` | H12 | **관측된 과거 정산 2건의 간격**으로 외삽. funding 표의 미래 행을 읽지 않는다 |

두 번째는 중요하다. funding 표에서 다음 정산을 직접 읽으면 미래 행 참조가 된다. 그래서 마지막 두 **과거**
정산의 간격을 구해 앞으로 밀었다. 미래 행을 지워도 답이 바뀌지 않음을 테스트로 고정했다.

---

## 3. Score category 4개

전부 25점, 순서형 3단계(0 / 12 / 25). 배점·단계는 계약에서 읽고 코드에 두 번 쓰지 않는다.

| category | 입력 | 방향 | 25점 | 12점 | 0점 |
|---|---|---|---|---|---|
| `market_structure` | `f_basis` | 낮을수록 LONG | B1 | B2 | 그 외 |
| `positioning` | `f_oi1h` | 낮을수록 LONG | B1 | 값 < 0 | 그 외 |
| `volatility_exhaustion` | `f_rv24h` | 높을수록 LONG | HIGH | MID | LOW |
| `price_exhaustion` | `f_drop1h` | 낮을수록 LONG | B1 | B2 | 그 외 |

`LONG_SCORE = 네 category 합` (0~100).

`positioning`만 bucket 위에 부호 조건이 하나 더 붙는다. 계약이 그 category에만 `negative` 단계를 준 것을
그대로 구현했고, 0은 음수가 아니라 0점이다.

### 3.0 threshold 50이 격자 간극에 놓인다 (전수 확인)

순서형 3단계 4개라 **총점은 이산적이다.** 도달 가능한 값을 전부 열거했다:

```
0, 12, 24, 25, 36, 37, 48, 49, 50, 61, 62, 74, 75, 87, 100
```

- threshold 미달 중 최대는 **49**, 통과 중 최소는 정확히 **50**이다. 50과 61 사이에는 아무 값도 없다.
- M1·M2를 만족하면서 50 이상인 조합 **24개 전부**가 25점 category를 최소 1개 포함한다.

즉 계약 5.5절이 "구조적 의미가 있다"고 적은 주장이 열거로 확인된다. threshold 50은 촘촘한 분포를 자른
값이 아니라 격자의 경계다. `test_the_threshold_sits_on_a_gap_in_the_reachable_scores`로 고정했고,
나중에 단계 배점이 바뀌면 이 테스트가 실패해 threshold를 다시 보게 만든다.

### 3.1 Feature 공식 (신규 0개)

| id | 공식 | 상속 원본 |
|---|---|---|
| `f_basis` | `ln(C[t] / IDX[t])` | D5 계약 6절 E3 `fund_premium` |
| `f_oi1h` | `ln(OI[t] / OI[t-60])` | D5 계약 6절 D1 `oi_chg_60` |
| `f_drop1h` | `ln(C[t] / C[t-60])` | D5 계약 6절 A1 `trend_ret_60` |
| `f_rv24h` | `std(ln(C[t]/C[t-1]), t-1439..t)`, ddof 0 | D5 계약 10절 변동성 regime |

**"verbatim"을 주석이 아니라 테스트로 만들었다.** `test_d6_features.py`가 네 공식을 **D5 결과를 실제로
만든 구현**(`app.crypto.research.features`)과 배열 전체에서 `atol=0, rtol=0`으로 대조한다.
bucket 배정도 `D5F.bucketize`와 날짜별로 대조한다.

그 과정에서 D5와 어긋날 수 있었던 지점 하나를 잡았다: D5의 유효비율 분모는 **고정 43,200**(30일 폭)이고
넘겨받은 슬라이스 길이가 아니다. 슬라이스 길이로 나누면 데이터 시작부의 짧은 창이 통과해 버린다.
고정 분모로 맞췄고 테스트로 고정했다(`test_sparse_denominator_is_the_nominal_window_not_the_slice`).

### 3.2 경계 규칙

D5의 관례를 재현했다. 추론하지 않고 복제했다.

| 경계 | 규칙 |
|---|---|
| bucket | `searchsorted(cut, v, side="right")` → **B1은 10분위 미만(strict)**. 정확히 cutoff면 위 bucket |
| 변동성 | HIGH는 `rv > hi`, LOW는 `rv < lo`, 경계값은 **MID** |
| 변동성 cutoff | D5.1이 F1 train에서 동결한 상수를 그대로 사용. **재계산 0** |

---

## 4. 계약 공백 하나와 그 처리 (중요)

기계본 계약에서 **일부 고정값이 숫자 필드가 아니라 문자열 안에만 있다.**

- `exit.risk.formula` = `"entry * (1 - clip(2.5 * f_rv24h * sqrt(240), 0.01, 0.10))"` → 손절 승수 2.5, 환산 240, clip 0.01/0.10
- `hard_filters[].threshold` = `"last 1m bar close older than 90s"` 등 → H1·H2·H6·H12의 임계값

영어를 파싱하는 것은 사전등록을 읽는 방법이 아니고, 숫자를 코드에 그냥 적으면 계약이 바뀌어도 조용히
옛 값이 살아남는다. 그래서 **정확한 문자열 일치로 바인딩**했다.

`contract.py`의 `STRING_BINDINGS`에 계약이 담고 있어야 하는 문자열과 그 옆에 코드가 쓸 숫자를 함께 적고,
로더가 문자열이 다르면 `CONTRACT_MISMATCH`로 거부한다. 숫자 필드가 따로 있는 값은 `CROSS_CHECKS`로
양쪽이 같은지 확인한다(cooldown, 위험예산, 일일 가드, max_hold, 유효비율).

테스트로 확인한 것:
- 손절 공식 문자열의 2.5를 3.0으로 바꾸면 로드 실패
- H1 문자열의 90s를 120s로 바꾸면 로드 실패
- `entry.cooldown_min`을 30으로 바꾸면 문자열과 불일치로 실패

**이것은 계약 위반이 아니라 계약의 표현 공백이다.** 동결 계약은 수정하지 않았다(해시가 깨진다).
V2를 만들 때 이 값들을 숫자 필드로 노출할 것을 권고한다(9절).

---

## 5. 하드필터 13개

계약 순서대로 전부 구현하고, **차단 여부와 무관하게 13개 전부의 결과를 기록**한다.
`id / status / reason / observed / threshold`를 반환하고, 계약의 필터 목록과 개수가 어긋나면 `RuntimeError`다.

상태는 3종이다.

| 상태 | 의미 |
|---|---|
| `PASS` | 통과 |
| `FAIL` | **차단**. 점수가 100점이어도 진입 금지 |
| `NOT_EVALUATED` | 계약이 `realtime_only`로 표시한 H6·H7을 historical에서 건너뛴 것. **차단하지 않고, 건너뛴 사실을 기록** |

H6·H7을 historical에서 통과로 처리하면 근거 없는 주장이 되고, 실패로 처리하면 D6-C가 아무것도 못 한다.
D1이 확정한 "과거 호가 없음"을 그대로 기록하는 쪽을 택했고 양방향으로 테스트했다.

**입력이 없어야 할 이유가 없는데 없으면 FAIL이다**(보수적). 예: `H2_OI_RECORD_TS_UNKNOWN`,
`H12_NEXT_FUNDING_UNKNOWN`, `H5_VOL_LABEL_UNKNOWN`, realtime의 `H7_SAFE_MAX_UNAVAILABLE`.
임의 fallback 0.

| id | 이름 | 구현 임계값 | 출처 |
|---|---|---|---|
| H1 | DATA_STALE | bar 종가 후 > 90,000ms | 바인딩 문자열 |
| H2 | OI_STALE | OI 기록 stamp 후 > 900,000ms | 바인딩 문자열 |
| H3 | INSUFFICIENT_HISTORY | bucket 유효비율 < 0.5인 feature 존재 | 문자열 + `bucketing.min_valid_fraction` |
| H4 | FEATURE_NAN | feature 4개 중 NaN 존재 | 문자열 |
| H5 | VOL_LOW | 라벨 = LOW | 문자열 |
| H6 | SPREAD_WIDE | `(ask-bid)/mid > 5bp` (realtime) | 문자열 |
| H7 | DEPTH_SHORT | 의도 수량 > SAFE MAX (realtime) | 문자열 |
| H8 | COOLDOWN | 직전 청산 후 < 3,600,000ms | 문자열 + `entry.cooldown_min` |
| H9 | POSITION_OPEN | AUTO 포지션 보유 | 문자열 |
| H10 | DAILY_LOSS_GUARD | 당일 실현손익 ≤ -2.0% | 문자열 + `account_risk` |
| H11 | CONSECUTIVE_LOSS | 연속 4회 **이고** 마지막 손실 후 24h 내 | 문자열 + `account_risk` |
| H12 | FUNDING_WINDOW | 다음 정산까지 < 300,000ms | 문자열 |
| H13 | LEDGER_DIVERGENCE | `emergency_stop`이 설정됨 | 문자열 |

H11은 계약이 "4회 이상 → 24h 차단"이라고만 적어 차단 종료 시각의 주인이 없다. 외부가 계산한 차단 시각을
받는 대신 **상태의 `consecutive_losses`와 `last_loss_ts_ms`만으로 결정론적으로 판정**했다. 24h가 지나면
스스로 풀린다.

---

## 6. 결정

### LONG / HOLD

```
하드필터 중 FAIL 하나라도  → HOLD
LONG_SCORE < 50            → HOLD
M1(market_structure ≥ 12) 또는 M2(volatility ≥ 12) 미충족 → HOLD
score separation < 10       → HOLD   (V1은 SHORT 비활성이라 항상 성립)
사이징 불가                 → HOLD
그 외                      → LONG / ENTRY_INTENT_LONG
```

`HOLD`가 기본 상태다. 진입 조건을 모두 통과해야 `LONG`이다.

### SHORT 비활성 확인

`short_score`는 **`None`이고 0이 아니다.** 0으로 두면 진짜 0점과 구분이 안 되고, separation 조건을
엉뚱한 이유로 만족시킨다. `short_state`는 모든 출력에 `SHORT_DISABLED_FOR_V1`로 실린다.
`score.short_score()`는 계약에서 SHORT가 켜지면 `NotImplementedError`를 던진다.

### 출력 스키마

`D6B_DECISION_V1`. 계약 J절이 요구한 항목 전부를 담는다: `timestamp_ms`, `strategy_id`,
`strategy_version`, `contract_hash`, `decision`, `entry_intent`, `entry_allowed`, `long_score`,
`short_score`, `short_state`, `category_scores`(내역·레벨·cutoffs 포함), `hard_filters`(13개 전부),
`filter_pass`, `reason_codes`, `features`, `buckets`, `volatility_label`, `market_context`,
`required_inputs_available`, `data_age`, `sizing`.

**주문 객체는 만들지 않는다.** 최대 출력은 `ENTRY_INTENT_LONG` 문자열이다.

---

## 7. 청산 / 손절 / 레버리지 / 사이징

### 청산 평가기

가상 포지션을 받아 `HOLD_POSITION` 또는 `EXIT_INTENT`만 판정한다. PnL 0, 원장 접근 0, close 주문 0.

| 순서 | 규칙 |
|---|---|
| 1 | **X2 손절**: mark(또는 bar의 mark 최저) ≤ 손절가 |
| 2 | **X1 시간 청산**: 240분 경과 |

순서가 계약의 `same_bar_rule`이다. 같은 bar가 손절가를 찍고 시간도 다 되면 **손절이 이긴다**.
1m bar는 자기 극값의 순서를 말해주지 않으므로 불리한 해석을 택한다.

**긴급정지는 청산을 강제하지 않는다.** 계약 R7이 "신규 진입 중단, 보유분은 X1/X2 규칙대로"라고 못박았다.
그래서 `emergency_stop`은 필터(H13) 쪽 관심사이고 청산 평가기에 없다.

mark가 없으면 `HOLD_POSITION / MARK_UNKNOWN`으로 "손절을 못 봤다"를 명시한다(안전하다고 주장하지 않는다).
시간 청산은 mark 없이도 판정한다.

### 변동성 손절

```
sigma_4h  = f_rv24h * sqrt(240)
stop_dist = clip(2.5 * sigma_4h, 0.01, 0.10)
stop_price = entry * (1 - stop_dist)
```

승수·환산·clip 전부 계약 문자열에 바인딩. ATR 대체 0, 고정 % 대체 0. 경계 테스트는
정확히 손절가 / 한 틱 위 / 한 틱 아래를 모두 포함한다.

### 레버리지

**1x 고정.** 계약에서 읽고 파라미터가 아니다. manual 레버리지 설정·SAFE MAX·UI 경고는 건드리지 않았다.
레버리지는 사이징에 들어가지 않는다(격리형에서 청산 거리만 정하고, 1x는 청산 없음).

### 사이징

```
theoretical_notional  = equity * 0.005 / stop_dist
risk_limited_notional = min(theoretical, equity * 1.0)
qty                   = floor_to_step(risk_limited / price, 0.001)
qty                   = min(qty, SAFE_MAX)        # 주어졌을 때만
final_notional        = qty * price
불가 사유: QTY_BELOW_MIN / NOTIONAL_BELOW_MIN(5 USDT) / STOP_DISTANCE_UNKNOWN / EQUITY_UNKNOWN / ENTRY_PRICE_UNKNOWN
```

네 단계를 전부 따로 보고한다. 상한에 걸린 것과 신호가 작은 것을 구분할 수 있어야 하기 때문이다.
**SAFE MAX는 상한 constraint로만 쓴다.** D6-B는 실시간 SAFE MAX를 호출하지 않고, 테스트는 mock 상한만 쓴다.

**구현 중 확인한 사실**: 계약 상수에서 **명목 1.0배 상한은 도달 불가능**하다. 위험예산 0.5%를 손절 하한
1%로 나눈 최대가 자본의 0.5배다. D6-A가 사전 측정한 `capped_share = 0.0`과 일치한다. 결함이 아니라
위험예산이 이미 상한을 지배한다는 뜻이고, 나중에 예산을 올리거나 하한을 내리면 테스트가 깨져서 알려준다.

---

## 8. PIT / 결정론 / 격리 근거

### PIT (10개 테스트)

| 근거 | 내용 |
|---|---|
| 구조 | `BarWindow`의 마지막 행이 bar t다. **t 이후 행은 객체에 존재하지 않는다.** 테스트가 아니라 타입으로 막았다 |
| feature 4개 | 결정 bar 이후 행을 **5배로 조작**해도 t의 값이 비트 단위로 동일 |
| bucket cutoff | `[D-30일, D)`만 사용. 결정 bar가 속한 날은 제외. 그 이후를 999로 덮어도 cutoff 불변 |
| 결정 전체 | grid를 33일로 늘리고 t 이후를 3배로 조작한 뒤 같은 bar에서 재결정 → **출력 JSON 바이트 동일** |
| funding | 미래 정산 행을 지워도 `next_funding_ts` 동일 |
| 사이징 가격 | 아래 별도 항목 |

**사이징 가격에서 PIT 구멍 하나를 발견해 막았다.** 계약 §6.1은 최초 합법 진입을 `open[t+1]`로 두고
§8은 `qty = notional / entry_price`라고 쓴다. `open[t+1]`로 사이징하면 사이징 가능/불가 판정이 결정을
뒤집을 수 있어 **미래 bar가 결정에 들어온다.** 그래서 `entry_reference_price`를 **결정 시점에 알려진
가격**(replay는 `close[t]`, realtime은 호가)으로 정의했다. 실거래 동작과도 맞는다: 화면에 보이는 가격으로
수량을 정하고 체결은 그 뒤에 난다. `open[t+1]`은 D6-C가 **체결가**로 따로 들고 간다.
이것은 해석이므로 9절에 올렸다.

### 결정론 (4개 테스트)

동일 입력 5회 반복 → `to_json()` 바이트 동일. window 객체를 새로 만들어도 동일. 키 순서 불변.
`json.dumps(sort_keys=True, separators=(",",":"))`로 직렬화.

코드 레벨: `time.time()`·`datetime.now()`·`utcnow`·`random`·네트워크 호출이 패키지에 **없음**을
소스 스캔으로 검사한다(`runner.py`는 표시용 포맷만, 시계는 읽지 않음). 결정 시각은 `now_ms` 인자다.

### Manual / AUTO 격리 (6개 테스트)

| 검사 | 방법 |
|---|---|
| AUTO → manual 차단 | AST import 그래프에 `crypto.paper`·`crypto.terminal` 없음 |
| 모듈명 직접 참조 차단 | `paper.account`·`paper.ledger`·`paper.state`·`paper.engine`·`paper.persistence`·`terminal.session`·`terminal.api` 텍스트 없음 |
| manual → AUTO 차단 | `paper/`·`terminal/` 전 파일에 `research.d6` 없음 |
| 상태 주입 | `StrategyState`는 frozen dataclass. 조회하지 않고 받는다 |
| 데이터 범위 | `ROOT_DATASETS`가 D2 5종뿐, 금지 연구선 import 0, 금지 원천 문자열 리터럴 0 |
| 주문 생성 | `place_order`·`submit_order`·`PaperEngine`·`apply_market`·`OrderRequest` 없음 |

### PnL / 백테스트 부재 (3개 테스트)

`profit_factor`·`win_rate`·`drawdown`·`sharpe`·`forward_return`·`future_return`·`backtest`·
`equity_curve`·`trade_pnl`이 패키지에 **한 번도 나오지 않음**을 소스 스캔으로 검사한다.

`pnl`이라는 단어는 **H10 가드의 주입 입력 한 곳에서만** 허용하고, 그 밖의 줄에서 나타나면 테스트가 실패한다.
H10은 PnL에서 온 숫자를 비교해야 하므로 단어를 통째로 금지할 수 없고, **계산은 하지 않는다**.
테스트 fixture도 forward return 기반 기대값을 쓰지 않는다.

---

## 9. 해석·UNKNOWN (사용자 확인이 필요한 항목)

계약이 명확하지 않아 판단한 지점만 적는다. 결과를 좋게 만들기 위한 해석은 없다(성과를 보지 않았다).

| # | 항목 | 계약 상태 | 이 구현의 선택 | 왜 |
|---|---|---|---|---|
| I1 | **사이징 기준가** | §6.1은 진입을 `open[t+1]`, §8은 `entry_price`로만 적음 | **결정 시점 가격**(`close[t]`) | `open[t+1]`은 미래 행이고 사이징 가능 판정이 결정을 뒤집을 수 있어 PIT 조항 위반. 체결가는 D6-C가 별도 보유 |
| I2 | **손절 승수·clip·필터 임계값의 출처** | 문자열 안에만 있음 | 정확한 문자열 일치로 바인딩(4절) | 파싱은 위험, 하드코딩은 조용한 drift |
| I3 | **다음 funding 정산 시각** | 계약에 스케줄 필드 없음 | **과거 정산 2건의 간격으로 외삽** | 표의 미래 행 읽기를 피함. D3 계약 FN1의 8h 상수를 코드에 새로 적지 않음 |
| I4 | **H2 age 기준** | "last OI record older than 15min" | **기록 stamp** 기준(가용 시각이 아니라) | 문장 그대로. 정상 운영에서 5~10분이라 슬랙 1건분 |
| I5 | **H11 차단 종료 시각의 주인** | "→ 24h block"만 | `consecutive_losses` + `last_loss_ts_ms`로 자체 판정 | 외부가 계산한 상태를 더 받지 않기 위해 |
| I6 | **긴급정지 시 보유분** | §R7이 "X1/X2 규칙대로" | 청산 평가기에 긴급 청산 **없음** | 계약 문장 그대로 |

**I1이 가장 중요하다.** D6-C 착수 전에 확인받는 것이 좋다. 다만 어느 쪽을 택해도 D6-C의 체결가는
`open[t+1]`이고, 차이는 수량이 `close[t]` 기준인지 `open[t+1]` 기준인지뿐이다(일반적으로 0.1% 미만).

**UNKNOWN으로 남긴 것 없음.** 위 6건은 전부 계약 조항 사이의 우선순위로 풀렸고, 추측이 필요한 지점은
없었다. PIT 조항이 다른 조항보다 강하다는 판단(I1)이 유일한 우선순위 결정이다.

---

## 10. 골든 스냅샷

`data/research/crypto/d6/d6b_golden_snapshots_v1.json` (8건). 실 D2 데이터, 결과 무관하게 고른
고정 timestamp. 원시 입력 → feature 값 → bucket → category 점수 → 결정을 전부 담는다.
**forward return 0, PnL 0, 거래 시뮬레이션 0.**

| timestamp (UTC) | 결정 | score | vol | f_basis | f_oi1h | f_drop1h |
|---|---|---|---|---|---|---|
| 2021-05-19T14:00 | LONG | 75 | HIGH | B1 | B1 | B5 |
| 2022-06-13T08:00 | LONG | 87 | HIGH | B1 | B2 | B1 |
| 2022-11-09T18:00 | **HOLD** | **50** | HIGH | B3 | B5 | B1 |
| 2023-08-17T22:00 | LONG | 100 | HIGH | B1 | B1 | B1 |
| 2024-03-05T12:00 | HOLD | 49 | HIGH | B5 | B3 | B2 |
| 2024-08-05T02:00 | LONG | 87 | HIGH | B2 | B1 | B1 |
| 2025-02-03T04:00 | HOLD | 37 | HIGH | B2 | B5 | B4 |
| 2026-04-10T10:00 | HOLD | 12 | LOW | B3 | B3 | B4 |

**2022-11-09은 점수가 정확히 threshold 50인데 HOLD다.** basis 할인이 없어(B3 → 0점) M1이 막았다.
필수조건이 총점만으로는 할 수 없는 일을 하고 있다는 실증이라 테스트로 고정했다.
2026-04-10은 저변동성이라 H5가 막은 사례다.

8건 전부 재현이 바이트 단위로 일치함을 `test_d6_golden.py`가 검사한다(D2 grid가 없으면 skip).

---

## 11. 테스트

| 파일 | 개수 | 내용 |
|---|---|---|
| `test_d6_engine_contract.py` | 33 | 로더: hash 불일치, 필수 필드, 점수 합, 중복, 임계값 범위, SHORT, 금지 기능, 문자열 바인딩 |
| `test_d6_features.py` | 31 | **D5 구현과 4개 공식 + bucket 대조**, 경계, 결측, lookback 부족, **PIT 10개** |
| `test_d6_filters.py` | 52 | 13개 각각 pass/fail, NOT_EVALUATED 양방향, 다중 실패, reason code |
| `test_d6_score_decision.py` | 66 | category 0/최대/총 0/총 100, threshold 49·50·75, 필터 우선, 청산 경계, 사이징 |
| `test_d6_isolation.py` | 19 | import 그래프 양방향, **PnL은 AST로 코드/산문 분리 검사**, 시계·네트워크 부재, 결정론, 결정 레벨 PIT |
| `test_d6_golden.py` | 20 | 스냅샷 재현, 로더 PIT |
| `test_d6_contract.py` | 38 | (D6-A에서 작성) 설계 계약 검증 |
| **합계 (신규 221)** | **259** | |

```
.venv/bin/python -m pytest backend/tests/crypto/ -q
→ 680 passed in 40.30s        (D6-B 이전 459 → +221)

.venv/bin/python -m pytest backend/tests -q \
  --ignore=backend/tests/strategy_b/test_strategy_b_scanner.py \
  --ignore=backend/tests/test_strategy_b_historical_scanner.py
→ 4674 passed, 23 skipped in 944s (exit 0)
```

기존 crypto 테스트 **수정 0개, 실패 0개.**

제외한 2개 파일은 **다른 세션의 untracked 파일**이고(`git status`에서 `??`), 이 작업 이전부터 collection
error였다. 내 변경과 무관하다.

---

## 12. 하지 않은 것

| 항목 | 상태 |
|---|---|
| 백테스트 / PnL / 성과 지표 (승률·PF·MDD) | **0** |
| forward return / trade simulation | **0** |
| threshold·weight 튜닝, feature 추가·삭제 | **0** |
| 주문 생성 / Paper Engine 호출 | **0** |
| 기존 crypto 엔진·연구 모듈 수정 | **0줄** |
| 운영 서버 접근 / 배포 / rsync / systemd / frontend build | **0** |
| 운영 Paper 계좌 접근 | **0** |
| liqfwd 수집기 접근 | **0** |
| 동결 계약 파일 수정 | **0** (해시 불변 확인) |
| commit / push | **0** |

---

## 13. D6-B Exit Gate

| # | 조건 | 결과 | 근거 |
|---|---|---|---|
| 1 | contract hash 일치 | **PASS** | 시작·종료 시 재계산, `1a6bcaa5…` 일치 |
| 2 | 4 score category 구현 | **PASS** | 3절, 65개 테스트 |
| 3 | 13 hard filters 구현 | **PASS** | 5절, 52개 테스트 |
| 4 | LONG/HOLD decision 구현 | **PASS** | 6절 |
| 5 | SHORT disabled | **PASS** | `short_score is None`, 켜면 `NotImplementedError` |
| 6 | exit evaluator 구현 | **PASS** | 7절, 경계 테스트 포함 |
| 7 | 1x fixed leverage | **PASS** | 계약에서 읽음, 사이징과 분리 |
| 8 | 0.5% risk sizing | **PASS** | 7절, 손절 시 손실이 예산 이하임을 테스트 |
| 9 | PIT tests PASS | **PASS** | 10개 (요구 최소 5) |
| 10 | determinism PASS | **PASS** | 바이트 동일 4개 |
| 11 | manual/auto isolation PASS | **PASS** | import 그래프 양방향 6개 |
| 12 | no PnL/backtest code | **PASS** | 소스 스캔 3개 |
| 13 | 기존 crypto regression PASS | **PASS** | 679 passed |

---

## 14. 위험 / 남은 것

| # | 항목 |
|---|---|
| R1 | **I1(사이징 기준가)은 해석이다.** D6-C 착수 전 확인 권고. 영향은 수량 소수점 수준이지만 사전등록 문서에 없던 선택이다 |
| R2 | 계약 기계본이 일부 고정값을 문자열로만 담는다(4절). V2에서 숫자 필드로 노출할 것을 권고. 지금은 문자열 바인딩이 drift를 막는다 |
| R3 | D6-C는 **합성 호가**를 쓴다(과거 호가 없음). 체결가는 모델 출력이고 H6·H7은 평가 불가다. 결과 문서에 반드시 기재 |
| R4 | 명목 1.0배 상한은 현 상수에서 도달 불가(7절). 위험 설계상 문제는 아니나 "상한이 작동한다"고 말할 수 없다 |
| R5 | `data.py`가 D5 로더의 비공개 함수 `_read`를 재사용한다. D5 코드 수정 금지 때문에 택한 방법이고, 같은 패키지 안이라 순환은 없다 |
| R6 | 이 단계는 **전략의 가치를 전혀 말하지 않는다.** D6-A의 사전 예상은 D6-C FAIL 쪽이었고, 그 예상은 변하지 않았다 |

---

## 15. 다음 단계

**D6-C: Historical replay / 백테스트 → 계약 13절 게이트 G1~G11 판정.**

선결 조건:
1. **I1 확인** (사이징 기준가: 결정 시점 가격 vs `open[t+1]`)
2. D6-C 사전등록 문서 작성 (arm 10개, 비용 시나리오, bootstrap seed 20260928는 이미 계약에 있음)
3. 사용자 승인

D6-C 착수는 **사용자 승인 뒤에만** 한다. 여기서 멈춘다.

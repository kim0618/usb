# US-B CRYPTO C1 Signal V1

작성 2026-10-02. 대상은 이 터미널을 운영하는 사람과 다음에 이 코드를 고칠 사람이다.

D5.2의 `C1 dislocation_event`를 **운영 신호 레이어**로 구현했다. 차트 마커 + 전용 shadow 원장이며
**주문을 내지 않는다**. 페이퍼도, 바이낸스도, 레버리지도, Auto Exit도 건드리지 않는다.
그 이유는 연구 결과 자체에 있다(2절).

---

## 1. 계약 정본 (코드가 아니라 문서에서 복원)

정본은 `CRYPTO_D5_2_DERIVATIVES_FLOW_CONTRACT_V1.md` 4절과 `derivatives_flow.py`다.
sha256 `c49cd0e5...bc8cf81`은 동결 기록 `data/runtime/crypto/d5_2/contract_freeze_v1.json`과 일치한다.

**C1 = 아래 3개가 동시에 참인 1분봉 (Bybit BTCUSDT 선형 무기한, 결정은 봉 종가)**

| # | 조건 | 정의 |
|---|---|---|
| 1 | 현물 대비 할인 하위 10% | `S1 = ln(Bybit perp 5m 종가 / Binance spot 5m 종가)`가 **직전 30 UTC일**의 같은 1분 계열 10% 분위 **미만** |
| 2 | OI 1시간 감소 | `ln(OI[t] / OI[t-60]) < 0` (Bybit 5m OI, 기록시각+5분에 가용) |
| 3 | 고변동성 | 직전 24h 1분 로그수익률 표준편차(ddof 0) > **0.0010924950359531157** |

- 3번 cutoff은 **F1 train(2021-03-04~2022-01-01)에서 한 번 추정하고 동결**한 값이다. 최근 데이터로 재추정하지 않는다.
  재추정하면 라이브의 HIGH가 연구의 HIGH와 다른 뜻이 된다.
- 1번의 5m 값은 **결정 봉 종가까지 이미 끝난 5m 경계**만 본다(`floor((ts+60s)/5m)*5m`). 결측은 5m 1개까지만 이월.
- 2번은 **엄격한 `< 0`**. 연구 창에 정확히 0인 봉이 5개 있고 연구는 그것들을 제외한다.
- **청산 데이터는 입력이 아니다.** 계약 0절 Z3이 청산을 FORWARD_ONLY로 제외했다. 스냅샷에 자리만 두고 `NOT_A_C1_INPUT_D5_2_Z3`으로 표기한다.

**방향 계약 = LONG-only.** 연구 격자는 양방향을 전부 채점했고 C1의 SHORT는 LONG의 정확한 음수로
모든 horizon에서 REJECT다(`C1-EVENT-240m-SHORT` net -41.3bp). D5.2에 구현할 SHORT 정의가 없으므로 만들지 않았다.

**거래 계약**: 결정 t 종가 → 진입 `open[t+1]` → 청산 `open[t+1+240]`.
**비용(VIP0_BASE)** = `taker x (1 + X/E) + 0.0118bp + 보유 중 정산된 funding`, taker 0.00055.
"11bp"는 상수가 아니라 **왕복 taker**(X≈E일 때 2 x 5.5bp)를 가리킨다.

### 연구 판정 (반드시 같이 읽을 것)
`C1-EVENT-240m-LONG`은 **WEAK(W1)이고 SURVIVE가 아니다.** D5.2 게이트는 **CASE C**(SURVIVE 0).
720셀 중 비용을 넘은 유일한 셀이지만 S7(N_eff 77 < 200, 판정 fold 4 < 6)에서 떨어졌고,
S8 변동성 robustness는 C1이 고변동성을 조건에 포함하므로 **구조적으로 통과 불가**(계약 설계 결함).
그래서 이 레이어의 산출물은 주문이 아니라 **전방 기록**이다.

---

## 2. 왜 주문 경로가 없는가 (구조로 보장)

`backend/tests/crypto/test_c1_isolation.py`가 소스를 읽어 다음을 고정한다.

- `app/crypto/c1/`는 `live`·`orders`·`arm`·`leverage`·`exit_guard`·`sizing`을 import하지 않는다.
- `paper` 패키지도 import하지 않는다(shadow 원장은 페이퍼 계좌가 아니다).
- 패키지 밖에서 이것을 import하는 모듈은 `terminal/c1_routes.py` 하나뿐이다.
- numpy·pandas 등을 쓰지 않는다. `app/crypto`는 stdlib+fastapi/httpx/websockets만으로 도는 **배포 스냅샷**이다.
  그래서 feature 산술을 손으로 옮겨 적었다.
- 라우트는 `/attribute` 하나를 빼고 전부 GET이고, 그 하나도 C1 자기 파일에만 1줄 쓴다.

---

## 3. 운영 동작

**평가 주기는 1분이다(지시받은 5분이 아니다).** 계약의 결정 격자가 1분이고 3개 조건 중 2개(OI 변화,
24h 변동성)가 매 분 갱신된다. 5분 주기면 트리거가 최대 4분 늦고 1주기보다 짧은 이벤트는 조용히 사라진다.
tick은 20초마다 돌지만 그것은 "새로 닫힌 봉이 있는지" 보는 주기이지 결정 격자가 아니다.

**신호 1개 = C1이 연속으로 참인 구간 1개.** 연구는 이벤트 안 1분봉을 전부 표본으로 셌지만
(OOS 18,498봉/179일) 운영자는 봉이 아니라 이벤트에 반응한다. 조건이 한 번 끊겨야 다음 신호가 나온다.
shadow는 겹칠 수 있다(연구 표본도 겹쳤다). `signal_id = C1-LONG-<트리거 ms>`로 결정적이라
같은 봉을 다시 읽어도 중복이 생기지 않는다.

**결측은 FALSE가 아니라 NOT_ELIGIBLE.** 빠진 입력 이름을 `missing`에 담는다. stale 값을 끌어다 쓰지 않는다.

**계약에 없는 운영 가드 1개(명시)**: 연구의 OI as-of 조인은 마지막 값을 무한히 끌고 간다. 완성된 과거
파일에서는 맞지만 라이브 피드가 죽으면 "변화 없음"으로 읽힌다. 그래서 OI 스탬프가 **15분**보다 오래되면
STALE로 처리한다. 연구 표본 창(2021-03-04~)의 5m OI는 584,352개·결손 0이라 한 봉이 읽을 수 있는 가장
오래된 스탬프가 10분이고, 15분 가드는 **과거 판정을 바꿀 수 없다**(전수 재검증으로 확인, 4절).

**재시작 복구**: 원장을 먼저 읽어 커서와 armed를 복원하고(진행 중 이벤트가 두 번째 신호를 내지 않는 이유),
윈도를 다시 받아 밀린 shadow를 **계획 시각의 과거 봉 가격으로** 정산한 뒤 따라잡는다. 현재가로 때우지 않는다.

**브라우저 비의존**: 평가·원장·정산은 전부 백엔드 루프다. 탭이 닫혀 있어도 13:35 신호의 17:35 결과는 생긴다.

---

## 4. HISTORICAL PARITY GATE = PASS

`scripts/c1_parity.py`가 연구 표본 창 **전체**를 운영 엔진으로 재생해 봉 단위로 비교한다.

```
PYTHONPATH=backend .venv/bin/python scripts/c1_parity.py 2021-03-04 2026-09-23 180
```

| 항목 | 결과 |
|---|---|
| 비교 봉 | **2,921,760** (2021-03-04 ~ 2026-09-23 전 구간) |
| 이벤트 마스크 불일치 | **0** |
| S1 bucket 불일치 | **0** |
| 변동성 regime 불일치 | **0** |
| S1 값 차이 | **정확히 0.0** |
| OI 1h 변화 차이 | **정확히 0.0** |
| 진입가 / 청산가 불일치 | **0 / 0** |
| gross 수익률 차이 | **정확히 0.0** |
| 이벤트(연속 구간) 수 | 2,579 |

**잔차 2개와 그 원인**. "거의 비슷함"으로 넘기지 않았다.

1. **24h 변동성 5.11e-14.** 연구의 rolling sum은 격자 시작(2021-02-01)부터의 `cumsum` 차분이라
   2021년 이후 모든 분의 반올림을 들고 다닌다. 유한 버퍼를 쓰는 라이브 엔진은 재현할 수 없다.
   전 구간 실측: 두 방식 차이 최대 **5.11e-14**, 변동성이 cutoff에 가장 가까이 간 거리 **9.14e-10**.
   네 자릿수 차이라 **regime이 뒤집힌 봉 0개**. bucket 쪽도 같은 확인: `|S1 - q10|` 최소 7.2e-9.
2. **net 수익률 2.78e-17** (= 2.8e-13 bp). 비용 항의 덧셈 순서 차이. 어떤 순서도 바꾸지 못한다.

`numpy.quantile`의 두 갈래 lerp(중간점 위에서 위쪽에서 보간)까지 옮겨 적었고, 무작위 12,000건 비교에서
비트 단위 불일치 0이다.

### 추정량 차이 (보고에 반드시 포함할 것)
연구의 `+19.278bp`는 **이벤트 안 1분봉 18,498개의 평균**이다. 운영자가 이벤트당 1회 진입했을 때의
수치가 아니다. 같은 전 구간 재생에서 **이벤트 단위** OOS(2022-01-01~, 1,603건):

| 추정량 | OOS net 평균 | 중앙값 | 승률 |
|---|---|---|---|
| 연구(1분봉 가중, 18,498) | **+19.28bp** | - | - |
| 이벤트 단위(1,603) | **+10.30bp** | **+1.27bp** | 50.4% |

긴 이벤트가 봉 수만큼 가중되기 때문이다. **같은 데이터, 다른 추정량이고 둘 다 맞다.**
전방 기록을 평가할 때 비교 대상은 **+10.30bp 쪽**이다.

---

## 5. SHADOW 원장

전용 디렉터리 `data/runtime/crypto/c1/` (`CRYPTO_C1_ROOT`로 변경). 수동 PAPER·LIVE·AUTO와 파일을 공유하지 않는다.
append-only + id별 마지막 줄 우선, 커서는 원자적 교체.

- **공식 결과 = 4H 하나.** `summary`는 4H만 집계한다.
- 관측 horizon 30m/1h/2h/4h/6h/8h는 보유기간 연구용이며 **공식 성과에 섞지 않는다**.
  그중 **6h(360분)는 D5.2 horizon 집합에 아예 없다**(연구는 15/30/60/120/240 주요 + 480 참고). 코드가 그렇게 표기한다.
- MFE/MAE는 보유한 봉(t+1 ~ t+240) 구간이며 연구 `targets`와 같은 창이다.
- 사용자가 주문했든 안 했든 **모든 신호에 shadow가 생긴다**.

## 6. MANUAL ATTRIBUTION (기반만)

`POST /api/crypto/c1/attribute` 가 `signal_id ↔ (account, trade_ref)` 를 선언적으로 연결한다.
`source`는 항상 `USER_DECLARED`이고 **시간 근접으로 추정하지 않는다**. 근접은 증거가 아니다.
`GET /api/crypto/c1/attribution`이 선언 목록과 "지금 선언 가능한 신호"를 준다.
UI 토글은 이번 범위 밖이며 backend linking foundation까지만 했다.

## 7. 화면

- 마커: LONG은 **캔들 아래 ▲ `C1 LONG`**(보라). SHORT는 계약에 없으므로 그릴 수 없다. 배경 밴드 없음.
- **신호 계열은 하나.** timeframe을 바꾸면 같은 이벤트를 그 timeframe 버킷으로 다시 접을 뿐 재계산하지 않는다.
  같은 캔들에 여러 개면 `C1 x5`. 두 마커가 3캔들 이내로 붙으면 라벨을 `C1`로 줄인다(1d·390px에서 겹침 실측).
- 마커 기준점은 **트리거 시각이 아니라 신호 봉**이다. 1분봉에서 한 칸 밀리지 않게.
- 스트립: 활성 신호가 있으면 `C1 LONG · 활성 / 11:57 · 4H 기준 15:58 (n시간 m분 남음)`,
  없으면 `C1 · 신호 없음` 한 줄. 빈 카드 만들지 않는다. 연구 판정 줄은 활성/선택 시에만 붙는다.
- **4H는 benchmark지 EXIT가 아니다.** 화면 어디에도 "4H 종료"·"청산 예정"을 쓰지 않는다(12절).
- PAPER 화면과 LIVE 화면이 **같은 signal_id**를 본다(엔진 1개, 페이지가 같은 객체를 양쪽에 넘긴다).

## 8. 엔드포인트

전부 `CRYPTO_C1_SIGNAL=on` 일 때만 동작한다. **기본 off**. 이 모듈을 배포 스냅샷에 넣어도
켜기 전에는 거래소를 폴링하지 않고 라우트는 503이다.

| 경로 | 설명 |
|---|---|
| `GET /api/crypto/c1/state` | 계약·판정·활성 신호·마지막 봉 진단 |
| `GET /api/crypto/c1/markers?from_ms&to_ms&limit` | 차트용 신호 계열(구간 조회, lazy history용) |
| `GET /api/crypto/c1/ledger?limit` | shadow 원장 + 4H 요약 |
| `GET /api/crypto/c1/c1x` | C1x 진단 기록 + 전방 집계 (12절) |
| `GET /api/crypto/c1/attribution` | 선언 목록 |
| `POST /api/crypto/c1/attribute` | 선언 1건 기록 |

환경변수: `CRYPTO_C1_SIGNAL`(on/off), `CRYPTO_C1_ROOT`(상태 경로), `C1_FIXTURE`(미리보기 전용).

## 9. 데이터 출처

| 계열 | 출처 | 비고 |
|---|---|---|
| Bybit 1m kline | `/v5/market/kline` | D2 수집과 **같은 client·같은 spec**(`models.SERIES`) |
| Bybit 5m OI | `/v5/market/open-interest` | 동일 |
| Bybit funding | `/v5/market/funding/history` | shadow 비용 전용, feature 아님 |
| Binance spot 1m | `api.binance.com /api/v3/klines` | **신규 리더**(기존 Binance client는 서명된 선물 계좌용). 무서명·읽기 전용 |

기존 collector는 중단하지 않았고 건드리지 않았다.

## 10. 미리보기 FIXTURE

`scripts/c1_fixture.py`가 **실제 과거 이벤트**를 운영 엔진으로 재생해 고정 파일을 만든다(시각만 현재로 평행이동).
`FixtureRuntime`은 거래소를 폴링하지 않고 그 파일을 읽는다. 화면에 `미리보기 고정데이터` 배지가 붙는다.
`C1_FIXTURE`를 사람이 직접 지정해야만 만들어지므로 운영에서 활성화될 수 없다.

주의: 기본 fixture 6건은 **한 번의 괴리 국면에서 연속으로 난 실제 이벤트**라 5건 전부 이익이다.
C1의 평균이 아니다(4절 표가 평균이다).

## 11. 남은 것 / 하지 않은 것

- Production 배포 안 함. nginx·systemd·운영 상태 경로 무수정.
- git commit/push 안 함(사용자 정책).
- 수동 attribution UI 토글 미구현(backend만).
- 실제 LIVE 체결 마커(`▲ LIVE LONG`)는 미구현. 현재 LIVE 진입은 **진입 가격선**으로 표시되고 있어
  C1 마커(캔들 아래 보라 ▲)와 시각적으로 구분된다. 체결 마커까지 넣으려면 `/binance/fills`를 차트에
  붙이는 별도 작업이 필요하다.
- 라이브 전방 기록은 아직 0건이다. 이벤트 빈도가 봉의 0.7%, 연 평균 약 36일꼴이라
  **의미 있는 표본이 쌓이는 데 시간이 걸린다**. 그 전까지 이 레이어의 수치로 전략을 판단하지 말 것.


---

# 12. C1x: Premium Normalization 진단 (E2, FORWARD DIAGNOSTIC ONLY)

## 12.1 계약 정본

정본 `docs/crypto/c1_exit/C1_EXIT_E2_DYNAMIC_CONTRACT_V1.md` 3절 + `research/c1_exit/e2.py`
(`trig_c1x`/`_confirmed`). sha256 `61d194fd…` 동결 기록과 일치 확인. **threshold 신규 생성 0.**

**C1x = C1 LONG 진입 이후, 5분 평가 봉에서만, S1 bucket ≥ B3(그날 PIT 30분위 cutoff 이상)이 2회 연속.**

- 5분 평가 봉 = 종가가 5분 경계인 봉 (`(ts + 60s) % 5m == 0`). E2의 `eval5` 그대로.
- 조건 거짓 **또는 입력 결측**이면 카운트 0으로 리셋(stale 값 사용 안 함).
- trigger = 2번째 확인 봉, 체결 가정가 = **다음 봉 시가** `open[k+1]`.
- 8H 내 미발동이면 censored → 진단은 그냥 발생하지 않음(`EXPIRED_MAX_HOLD`).
- **진입 엔진은 D5.2대로 1분 격자를 유지한다.** 두 cadence를 억지로 통일하지 않았다.

## 12.2 C1x의 의미와 비의미

**의미**: "C1 진입 당시의 극단적 할인이 정상 영역으로 돌아온 시점".

**아님**: EXIT · SELL · CLOSE · STOP LOSS. 실제 청산 여부는 사용자가 직접 판단한다.
코드로 보장: `c1x.py`는 `live/orders/arm/leverage/exit_guard/sizing/paper`를 import하지 않고
(테스트가 소스 파싱으로 고정), 레코드에 `is_exit=False`가 박혀 있으며 **파일이 true라고 주장해도
로드 시 false로 강제**된다. 필드명도 전부 `hypothetical_exit_price`/`*_if_exited`다.

**연구 판정**: E2에서 C1x는 **INCONCLUSIVE**. 게이트 G2(큰 수익 보존) 탈락.
**E0 상위 5% winner의 48.8%만 보존**한다. 평균이 E0보다 높은 것은 손실을 더 많이 잘라서지
타이밍을 맞춰서가 아니다. 그래서 운영 적용이 아니라 **기록**이다.
(E2 결과 문서 자신이 "shadow에 진단 필드로 기록할 가치"를 제안했고, 이것이 그 구현이다.)

## 12.3 E2 PARITY GATE = PASS

`backend/tests/crypto/test_c1x_parity.py`가 **E2의 `trig_c1x`를 직접 호출**해 운영 엔진과 비교한다
(재구현 아님). 운영 쪽은 자기 grid에서 bucket을 유도하므로 premium 경계 계열도 함께 재확인된다.

| 항목 | 결과 (`C1_PARITY_FULL=1`, OOS 2022-01-01~2026-09-23) |
|---|---|
| 비교 event | **1,603** (E1/E2 event 수와 정확히 일치) |
| trigger / censored | **1,328 / 275** (82.8%, E2 보고 83%) |
| trigger 봉 불일치 | **0** |
| 체결 가정 봉·가격 불일치 | **0** |
| holding_minutes 불일치 | **0** |

## 12.4 Lifecycle · 1 event per signal

`NOT_TRIGGERED → CONFIRM_1 → TRIGGERED` (+ `EXPIRED_MAX_HOLD`).
`c1x_event_id = C1X-<signal_id>` 파생값이라 **signal당 최대 1개**가 구조적으로 보장된다.

**hindsight 금지**: 한 번 TRIGGERED로 기록되면 runtime이 **재계산하지 않고 원장에서 읽는다**.
이후 어떤 캔들도, 재시작도 이미 공개된 trigger 시각을 옮길 수 없다. E0 pairing만 나중에 바뀐다
(benchmark가 구조상 나중에 끝나므로).

평가 자체는 (signal, grid)의 **순수 함수**다. 확인 카운터를 tick 간에 들고 다니면 재시작 후
복원해야 하고 계약과 어긋날 수 있지만, 캔들에서 다시 계산하는 쪽은 그럴 수 없다.

## 12.5 E0 Pairing

같은 signal_id에 C1 Entry · C1x 가정 결과 · **E0 고정 4H benchmark**가 전부 연결된다.
4H가 아직 안 끝났으면 `e0_status = PENDING`, `delta_net = null`.
끝나면 `delta_net = C1x net − E0 net`.

**두 쪽 비용 모델이 같다**(VIP0_BASE: `taker×(1+X/E) + 0.0118bp + funding`). 그래야 차이가 의미를 갖는다.
E2는 PaperEngine/SyntheticBook으로 계산했지만 trade당 11.02~11.07bp로 같은 자리수이고,
여기서 재사용한 것은 **규칙(trigger)**이지 비용 엔진이 아니다.

## 12.6 전방 연구 지표

`/c1x`의 `summary`가 C1 신호수·trigger수·censored·pending·paired·trigger rate·보유 중앙값·
C1x 평균 net·E0 평균 net·paired delta를 전부 분모와 함께 낸다.

**`forward_sample_sufficient`**: paired 수가 E2의 1,603건에 못 미치면 **false**이고,
화면은 비교값 대신 `전방 표본 부족, 비교 판단 보류`를 출력한다. E2조차 1,603건으로 CI가 0을
걸쳤다. 몇 건으로 결론 내지 않기 위한 장치다.

winner preservation은 **사후 평가 지표로만** 쓴다. "이 거래가 winner일 것"을 실시간 trigger에
절대 쓰지 않는다(C1x trigger 조건에 수익 관련 항이 없다는 것이 그 보장이다).

## 12.7 화면

- `▲ C1 LONG` (보라, 캔들 **아래**, arrowUp) = 진입 후보
- `◇ C1x` (하늘색, 캔들 **위**, **square**) = 청산 검토용 진단 포인트
- **lightweight-charts에 diamond shape이 없다**(`circle|square|arrowUp|arrowDown`).
  방향으로 오해될 수 없는 가장 가까운 shape으로 **square**를 썼다. 텍스트 라벨은 `C1x`.
- 집계는 **종류별로 분리**: 같은 캔들에 둘 다 있어도 `C1 x5`와 `C1x x5`가 각각 뜨고 절대 합쳐지지 않는다.
- 6개 timeframe 전부 같은 event를 매핑만 한다(재계산 0).
- 클릭 상세: `C1x · Premium Normalization / C1 13:20 / C1x 14:35 / 경과 75분 / C1x 가정 +0.82% /
  4H 기준 +0.47% / 차이 +0.35%`. 4H 미완료면 `4H 기준 집계 중`.
- **EXIT·SELL·CLOSE·청산·종료 단어를 쓰지 않는다**(테스트가 전체 문구 코퍼스를 대문자 변환해 금지어 검사).

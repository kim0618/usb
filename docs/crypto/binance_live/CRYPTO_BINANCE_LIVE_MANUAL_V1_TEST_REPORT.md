# CRYPTO BINANCE LIVE MANUAL V1 · 테스트 보고서

작성: 2026-09-29
판정: **READY_FOR_READ_ONLY**
전제: 실제 API 키 미연결. 아래 결과는 전부 로컬·모킹 기준이며, 실계좌 호출은 **0건**이다.

---

## 1. 실행 결과

| 대상 | 명령 | 결과 |
|---|---|---|
| crypto 백엔드 전체 | `.venv/bin/python -m pytest backend/tests/crypto -q` (저장소 루트) | **1338 passed, 3 skipped** (75s) |
| ├ LIVE 기능 | `test_binance_live.py` | 76 passed |
| ├ LIVE 안전 | `test_binance_live_safety.py` | 11 passed |
| └ LIVE 라우트 | `test_binance_live_api.py` | 16 passed |
| 프론트엔드 전체 | `npx vitest run` | **556 passed / 42 files** |
| └ LIVE UI | `components/crypto-live.test.tsx` | 15 passed |
| 타입 | `npx tsc --noEmit` | 오류 0 |
| 린트 | `npx eslint` (신규 4파일) | 오류 0, 경고 0 |

LIVE 도입 전 crypto 테스트는 모두 통과 상태였고, 도입 후에도 기존 테스트는 한 건도 수정하지 않았다.

## 2. 엔드포인트 실측 (문서 대신 API로 확인)

서명 없이 호출했을 때 `-2014 API-key format invalid` = 존재하는 서명 엔드포인트,
바이낸스 HTML 오류 페이지 = 존재하지 않는 경로.

```
/fapi/v1/time                {"serverTime":1790639783075}            [200]
/fapi/v1/positionRisk        HTML 오류 페이지                         [404]   ← 없음
/fapi/v2/positionRisk        {"code":-2014,...}                      [401]
/fapi/v3/positionRisk        {"code":-2014,...}                      [401]   ← 사용
/fapi/v3/account             {"code":-2014,...}                      [401]
/fapi/v1/account             HTML 오류 페이지                         [404]
/fapi/v1/bookTicker          HTML 오류 페이지                         [404]   ← 없음
/fapi/v1/ticker/bookTicker   {"symbol":"BTCUSDT","bidPrice":...}     [200]   ← 사용
/fapi/v1/symbolConfig        {"code":-2014,...}                      [401]   ← 레버리지·마진모드
/fapi/v1/commissionRate      {"code":-2014,...}                      [401]
/fapi/v1/userTrades          {"code":-2014,...}                      [401]
/fapi/v1/income              {"code":-2014,...}                      [401]
POST /fapi/v1/listenKey      {"code":-2014,...}                      [401]
POST /fapi/v1/leverage       {"code":-2014,...}                      [401]
```

WebSocket 실접속:

```
wss://fstream.binance.com/ws/btcusdt@bookTicker   OK  {"e":"bookTicker",...}
wss://fstream.binance.com/public/ws (SUBSCRIBE)   OK  {"result":null,"id":1}
wss://fstream.binance.com/stream?streams=...      OK
wss://fstream.binance.com/private/ws              핸드셰이크만 되고 프레임 없음 → 미사용
```

**이 확인으로 초안의 경로 버그 2개를 잡았다**(`/fapi/v1/positionRisk`, `/fapi/v1/bookTicker`).
둘 다 실키 첫 호출에서 404가 났을 경로다.

## 3. 항목별 커버리지

### 계좌
- 지갑·가용·마진 잔고, 미실현이 Binance 응답 그대로 표시되는지 (`test_a_full_snapshot_...`)
- 필수 필드 누락 시 0을 쓰지 않고 `LiveFieldMissing` → `RESPONSE_SHAPE_CHANGED` blocker
- 키 없음 → 네트워크 접속 0회, blocker `CREDENTIALS_MISSING`

### 포지션
- LONG/SHORT/flat, 진입가·청산가·미실현
- 청산가 `"0"`은 None으로(0원 청산가를 화면에 쓰지 않음)
- 심볼 행이 아예 없는 계정 = flat (오류 아님)
- 레버리지·마진 모드는 `symbolConfig`에서 (V3 positionRisk에 없음을 테스트로 고정)

### 필터
- stepSize·minQty·maxQty·minNotional·precision 파싱
- 필터 누락 시 기본값 발명 없이 예외
- 내림(floor) 고정, 올림 금지
- 거부 코드 4종이 페이퍼와 같은 어휘로 나오는지

### 주문 (모두 모킹, 실전송 0)
- 명목→수량 변환이 Binance step 기준
- 반대 포지션 보유 시 `REVERSE_NOT_ALLOWED`
- **CLOSE가 스냅샷이 아니라 직전 재조회 수량을 쓰는지** (조회 횟수 +1과 수량 변화로 확인)
- flat에서 CLOSE는 `NO_POSITION_TO_CLOSE`
- 플래그 off → 전송 0건 + 미러에 INTENT/REFUSED 기록, SENT 없음
- 환경 플래그만 켜면 여전히 미무장
- 무장 시 나가는 파라미터가 MARKET/RESULT/reduceOnly/clientOrderId 규격대로인지
- 계좌가 차단(Hedge 등)이면 주문 계획 자체가 거부

### 미리보기
- 호가 걷기 VWAP 정확성
- 보이는 잔량으로 못 채우면 `NO_LIQUIDITY`(마지막 호가로 외삽 금지)
- 수수료는 계정 taker율
- 손익분기 가격으로 실제 계산하면 순손익 0 (역산 검증)

### 스트림
- `ACCOUNT_UPDATE`/`ORDER_TRADE_UPDATE` → dirty만 세우고 잔고를 옮기지 않음(프레임의 숫자가 view에 없음)
- `listenKeyExpired` → 키 폐기
- URL이 `<base>/<listenKey>` 형태

### 복구
- `resync()`가 로컬 상태를 버리고 Binance를 다시 읽는지
- 스트림 이벤트가 전체 재읽기를 강제하는지(호출 수로 확인)
- fast 티어가 계좌 전체를 다시 읽지 않는지(호출 수로 확인)

### UI
- 기본 PAPER, 키 없으면 LIVE 선택 불가
- LIVE 배지에 "실계좌"와 잠금 문구
- Binance 잔고·레버리지·마진 모드·청산가·계정 수수료율 표시
- flat이면 포지션 카드 대신 안내
- 주문은 확인창을 거쳐야 호출, CLOSE는 입력칸 수량을 쓰지 않음
- 백엔드 거부 메시지를 숨기지 않고 표시
- 알 수 없는 blocker 코드는 코드 그대로 노출

### 회귀 (PAPER 무변화)
- 페이퍼 라우트 경로 집합 유지, `/api/crypto/live`의 의미 유지
- LIVE 핸들러가 페이퍼 세션에서 읽는 것은 **환율 하나뿐**임을 감시 스텁으로 고정
- 페이퍼 원장·엔진·사이징·미리보기 코드 **변경 0줄**

## 4. 검증하지 못한 것

| 항목 | 이유 | 해소 방법 |
|---|---|---|
| 서명 엔드포인트의 실제 응답 필드 | 키 없음 | read-only soak 1회면 전부 드러남(누락은 blocker로 표시) |
| 실제 레이트리밋 소모량 | 키 없음 | soak 중 `X-MBX-USED-WEIGHT-1M` 관측(텔레메트리에 이미 수집) |
| 시계 오차 실측 | 키 없음 | soak 시작 시 `sync_clock()` 값 확인 |
| 유저 데이터 스트림 실프레임 | 키 없음 | soak 중 로그(미러 `LIVE_STREAM_EVENT`) |
| 실주문 체결 동작 | 금지됨 | 승인 후 최소 명목 1건 |

## 5. 다음 단계 제안

1. Binance에서 **Read + Futures** 권한 키 발급, 출금 권한 없음, IP 화이트리스트에 서버 IP.
2. 로컬 `.env`에 키를 넣고 `/crypto-paper` 화면에서 BINANCE LIVE로 전환 → 잔고·포지션·레버리지·
   마진 모드·수수료율·Mark가 실제 값으로 뜨는지 확인(주문 0건).
3. 30분 이상 두고 `LIVE_STREAM_EVENT`·`LIVE_RECONCILE` 기록과 weight 소모 확인.
4. 결과 보고 → 사용자 승인 → 그때 비로소 `BINANCE_LIVE_TRADING_ENABLED` 검토.

**이 단계에서는 여기까지다. 실주문·운영 배포·commit 모두 하지 않았다.**

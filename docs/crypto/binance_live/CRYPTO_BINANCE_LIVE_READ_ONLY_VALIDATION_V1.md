# CRYPTO BINANCE LIVE · READ-ONLY 실키 검증 V1

작성: 2026-09-29
키 지문: `658d48f4` (전체 문자열·시크릿·IP 전체값은 기록하지 않는다)
판정: **READ_ONLY_FAIL — 원인은 코드가 아니라 API 키 권한(`enableFutures=false`)**

---

## 1. 결론 한 줄

키는 살아 있고 서명도 IP도 문제가 없다. **선물 권한이 꺼져 있어서** `/fapi/*` 서명 엔드포인트가
전부 `-2015`로 거부된다. Binance API 관리에서 Futures 권한을 켜면 그대로 재검증 가능하다.

## 2. 권한 실측 (§4)

진단용으로 `GET /sapi/v1/account/apiRestrictions`를 1회 호출했다.
(애플리케이션은 SAPI를 호출할 수 없다. 거부목록에 있다. 이 호출은 앱 밖 진단 스크립트다.)

| 권한 | 값 | 목표 | 판정 |
|---|---|---|---|
| enableReading | true | enabled | OK |
| **enableFutures** | **false** | **enabled** | **차단 원인** |
| enableWithdrawals | false | disabled | OK |
| enableSpotAndMarginTrading | false | disabled | OK |
| enableInternalTransfer | false | disabled | OK |
| permitsUniversalTransfer | false | disabled | OK |
| enableMargin / PortfolioMargin / VanillaOptions | false | disabled | OK |
| ipRestrict | **false** | (실주문 전 제한 필요) | 경고 |

출금·이체·현물 권한이 전부 꺼져 있는 것은 **보안상 좋은 키**다. 선물만 켜면 된다.

## 3. 원인 분리 (§5)

| 검사 | 결과 | 의미 |
|---|---|---|
| 공개 엔드포인트(exchangeInfo·premiumIndex·ticker/bookTicker) | 200 | 네트워크·경로·파싱 정상 |
| 서명 엔드포인트 전부 | `-2015` | 키 권한 또는 IP |
| **서명을 일부러 틀려서 호출** | 역시 `-2015` (`-1022` 아님) | Binance가 서명 검사 **전에** 키/권한에서 막는다 |
| 같은 키로 현물 서명 조회 | **200 OK** | 키 자체는 유효, IP도 문제 없음 |
| 테스트넷 호스트 2곳 | `-2015` | 테스트넷 키가 아님 |
| 시계 오차 | **+1.63초** (recvWindow 5초) | `-1021` 아님, 창 안 |

따라서 `-2015`의 원인은 **선물 권한 하나**로 좁혀진다.

## 4. 검증된 항목 (키 권한과 무관하게 확인 가능한 것)

| 항목 | 결과 |
|---|---|
| 주문 network call (§17) | **0건.** `new_order`·`set_leverage` 한 번도 호출 안 됨(`trade_requests: 0`, `endpoints_called`에 부재) |
| 운영자가 누른 주문 시도 | 3회(LONG·CLOSE·레버리지) 전부 409 거부 |
| 거부 코드 | `ACCOUNT_NOT_READY` ×2, `LIVE_TRADING_DISABLED` ×1 |
| 감사 원장 | `LIVE_ORDER_INTENT(stage=PLAN)` → `LIVE_ORDER_REFUSED(stage=PLAN)` 기록됨 |
| Paper 무결성 (§18) | 로컬 페이퍼 런타임 14개 파일 sha256 **전/후 동일**. 검증은 격리된 scratch 런에서 실행 |
| Secret leak (§19) | 파일 8종(로그·미러·응답·문서) 검사, 전체 문자열 0건, 16자 부분 문자열도 0건 |
| LIVE UI (§16) | PAPER 기본값 유지, LIVE 선택 가능, 실계좌 배지 표시, 차단 사유 표시, **주문 UI 미표시**, PAPER 복귀 정상 (헤드리스 크롬 실제 렌더링으로 확인) |
| 레이트리밋 | weight 1분 최대 125, 429/418 0건 |

## 5. 검증 못 한 항목 (권한 해제 후 재시도)

account / position / position mode / margin mode / leverage / commission / fills / funding /
user data stream / 30분 soak. 전부 `-2015`로 막혔다. 실키 없이 이미 모킹으로는 통과한 항목이며,
실제 응답 필드 확인은 권한 해제 후에만 가능하다.

## 6. 이번 검증 중 고친 결함 2개

1. **감사 공백**: 계획 단계에서 거부되면(`ACCOUNT_NOT_READY`, `REVERSE_NOT_ALLOWED`, 수량 거부)
   미러에 아무 기록도 남지 않았다. 버튼을 눌렀다는 사실이 사라진다.
   → `plan()`이 `ORDER_INTENT(stage=PLAN)`를 먼저 쓰고, 거부되면 `ORDER_REFUSED(stage=PLAN)`를 쓴다. 테스트 추가.
2. **오해를 부르는 배지**: 인증이 거부된 상태인데 배지가 초록색 "동기화 0초 전"으로 보였다.
   스냅샷 객체의 나이만 보고 있었기 때문이다. → 준비 상태를 먼저 본다. "연결 안 됨". 테스트 추가.

두 결함 모두 **실키를 꽂고 실제로 렌더링해봤기 때문에** 드러났다. 모킹 테스트로는 통과하던 경로다.

## 7. 사용자 조치 (다음 단계)

1. Binance → API 관리 → 이 키(`658d48f4`) → **"선물 사용(Enable Futures)" 체크**.
   - 체크박스가 비활성이면 USDⓈ-M 선물 계좌가 아직 개설되지 않은 것이다. 선물 계좌 개설 후 다시.
   - 그래도 안 되면 **선물 권한을 포함해 새 키를 발급**하고 `.env`의 두 값만 교체.
2. 교체 후 알려주면 30분 read-only soak(주문 0)까지 이어서 수행한다.
3. **실주문 활성화 전 Trusted IP 제한 필요** (현재 `ipRestrict: false`). 이 머신의 아웃바운드 IP는
   `175.193.*.*` 대역이며 전체 값은 기록하지 않는다. 운영 서버에서 돌릴 경우 서버 IP를 등록한다.
4. Futures 지갑 잔고가 0이면 그것은 정상이다(현물 지갑에만 USDT가 있는 상태).
   자금 이동은 이번 작업 범위가 아니다.

## 8. 운영 변경

rsync 0 · systemd 0 · nginx 0 · 운영 재시작 0 · 운영 접근 0 · commit 0 · push 0 · 실주문 0.
`BINANCE_LIVE_TRADING_ENABLED`는 검증 내내 `false`였다.

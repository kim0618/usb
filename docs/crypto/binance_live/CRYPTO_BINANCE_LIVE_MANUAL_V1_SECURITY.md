# CRYPTO BINANCE LIVE MANUAL V1 · 보안

작성: 2026-09-29
상태: 실주문 0건 · 실키 연결 0건 · 운영 배포 0건

---

## 1. API 키 권한

| 권한 | V1 필요 여부 |
|---|---|
| Read | **필요** |
| Futures Trading | 나중에 필요(지금은 없어도 읽기 동작) |
| Withdrawal (출금) | **절대 금지** |
| Spot Trading | 금지 |
| Margin Trading | 금지 |
| Internal Transfer | 금지 |
| IP 화이트리스트 | **권장**. 운영 서버(trader-j) 한 대만 허용하는 전제로 설계함. 주소 전체값은 문서에 적지 않는다 |

키에 출금 권한이 없어도 코드가 출금 경로를 호출할 수 없게 만들어 뒀다. 권한과 코드가 **둘 다** 막는다.

## 2. 키 보관

- `BINANCE_API_KEY` / `BINANCE_API_SECRET` **환경변수만** 읽는다. 다른 입력 경로가 없다.
- 소스·git·프론트엔드·로그·응답 어디에도 키가 없다.
- `Credentials`는 `repr`/`str`을 덮어써서 `[REDACTED]`만 낸다.
- 밖으로 나가는 모든 예외 문자열에 `redact()`를 적용한다(업스트림이 실수해도 지워진다).
- API가 공개하는 유일한 키 파생값은 **지문**(`sha256(api_key)[:8]`)이다. 키의 앞자리를 보여주지 않는다.
  앞자리도 키의 일부다.
- httpx 전송 예외는 메시지에 URL이 들어갈 수 있어 **예외 클래스 이름만** 남긴다(서명된 URL에는 signature가 있다).

## 3. 호출 가능 범위 (구조적 차단)

```
ALLOWED_PREFIX      /fapi/            ← Spot(/api/v3), Margin·Wallet(/sapi) 구조적으로 불가
DENIED_FRAGMENTS    withdraw, transfer, capital, sub-account
DENIED              POST /fapi/v1/marginType, POST /fapi/v1/positionSide/dual,
                    POST /fapi/v1/multiAssetsMargin, POST /fapi/v1/positionMargin
```

`guard()`는 **모든 요청마다** 메서드·경로 쌍을 다시 확인한다. 화이트리스트에 누가 몰래 행을 추가해도
거부목록이 별도로 막는다(“쓰려던 것”과 “절대 안 되는 것”을 분리한 이유).

## 4. 실주문 이중 게이트

| 게이트 | 값 | 확인 위치 |
|---|---|---|
| 환경변수 `BINANCE_LIVE_TRADING_ENABLED` | **false (기본값)** | `LiveConfig.trading_enabled` |
| 클라이언트 `trading_enabled` | **False (기본값)** | `rest.call()` · 요청을 만들기 전에 거부 |

- 둘 다 true여야 주문이 나간다. 하나만 켜면 안 나간다(테스트로 고정).
- 오타는 false다(`"ture"` → false). 켜기는 어렵게, 꺼지기는 쉽게.
- V1은 어느 쪽도 켜지 않았다.
- 레버리지 변경도 같은 게이트를 통과해야 한다(실계좌 쓰기이므로).

## 5. 감사 기록

주문 버튼은 잠긴 상태에서도 `LIVE_ORDER_INTENT`를 남긴다. 그래서 "눌렀는데 아무 일도 없었다"가
기록으로 남는다. 거부는 `LIVE_ORDER_REFUSED`. 전송은 `LIVE_ORDER_SENT`만 기록하며,
**요청 파라미터에 서명·키가 들어가지 않는다**(파라미터는 plan이 만든 것만 기록).
미러 파일은 페이퍼 런 디렉터리 안에 만들 수 없다(경로에 `paper`가 있으면 생성 거부).

## 6. 테스트로 고정한 항목

`backend/tests/crypto/test_binance_live_safety.py` (11개):

| 검사 | 방법 |
|---|---|
| LIVE 패키지를 import하는 모듈은 `live_routes` 하나뿐 | AST로 `backend/app` 전체 스캔 |
| 전략·연구·백테스트·서비스·dev 트리에 LIVE 참조 0 | 문자열 + AST |
| LIVE 패키지가 페이퍼 엔진을 import 0 | AST |
| 레지스트리에 출금·이체·Spot·Margin 경로 0 | 전 행 검사 |
| 거부 경로는 레지스트리에 추가돼도 막힘 | 런타임에 행을 주입 후 `resolve` |
| 미무장 클라이언트의 TRADE 요청 = 네트워크 0건 | MockTransport 호출 수 0 |
| 읽기 전용 세션이 TRADE 엔드포인트 0회 접촉 | 호출 경로 집합 교집합 |
| 응답·미러 파일에 키/시크릿 0 | 직렬화 문자열 검사 |
| Binance 오류 메시지가 키를 밖으로 못 냄 | 오류에 키를 심어 재현 |
| 프론트엔드에 키 이름 0 | ts/tsx 전수 검사 |
| 미러를 페이퍼 경로에 못 만듦 | 예외 확인 |

## 7. 실주문 활성화 조건 (아직 아님)

전부 PASS 후 **사용자 승인**이 있어야 `BINANCE_LIVE_TRADING_ENABLED=true`를 검토한다.

1. 실제 키로 read-only soak (계좌·포지션·레버리지·마진 모드·체결·펀딩·Mark 조회, 주문 0건)
2. `symbolConfig`·`positionRisk`·`account` 응답 필드가 실제로 존재하는지 확인(필드 누락 시 blocker로 드러남)
3. 시계 오차가 recvWindow 안인지 확인
4. 계정이 One-way Mode인지 확인
5. IP 화이트리스트 적용 확인
6. 페이퍼 회귀 통과 유지
7. 소액(최소 명목) 1건으로 시작

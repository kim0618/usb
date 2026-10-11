# Dynamic HOT V1 - 운영 서버 LIVE 조회 전용 검증 (2026-10-10 13:46~13:48Z)

판정: **B. LIVE_READ_ONLY_PASS** (C 차단 2건은 아래)

- 실행 방식:
  - 포트 없는 1회성 스크립트(`validate.py`)를 `/tmp/hotv1-ro-*`에서 실행했다(브랜치 `feat/dynamic-hot-v1` 코드).
  - `systemd-run --scope MemoryMax=160M`, nice 10. 운영 키는 env로만 읽었고 `BINANCE_LIVE_HOT_ORDERS`는 unset.
  - LiveRuntime는 쓰지 않았다(감사 mirror, user stream, listenKey 없음). 모든 HTTP는 GET 외 차단 가드를 거쳤다(대조군: POST /fapi/v1/order 차단 확인).
- 요청: 91건(서명 66건), 전부 GET, non-GET 0, 차단 0, HTTP 오류 0.
- 후보: HOT#1 CFXUSDT, COMMON BATUSDT, BINANCE_ONLY GTCUSDT(HOT 자격 충족, Bybit 미상장).
- BTC LONG 0.108 @83913.1(updateTime 포함) 전후 동일, BTC 미체결 0→0.
- 운영 변화:
  - 서비스 PID와 NRestarts 불변. 포지션, 무장, exit-guard, AUTO, fills, trade_requests=0 불변.
  - 감사 mirror +3줄 = `LIVE_RECONCILE reason=TTL`. 사후 baseline의 GET 조회가 운영 프로세스에서 유발한 것으로, 화면 폴링과 같은 종류다.
  - REST requests +31 / signed +22 = 같은 baseline GET.
- 정리: 임시 디렉터리 삭제, stray 프로세스 0, transient scope 0, 임시 포트 0(애초에 열지 않음).

## C 차단
1. **Safe MAX가 bracket 최대 레버리지로 제한되지 않는다.**
   - Safe MAX는 `symbolConfig.leverage`만 쓴다. GTC는 설정 20x인데 bracket 최대가 10x다.
   - 가상 100 USDT 기준 Safe MAX 명목은 현재 1,919.9, 올바른 값 995.0 USDT다(1.93배 과대).
   - 기존 일반 경로 결함이다(BTC는 150x라 드러나지 않았다).
2. **availableBalance = 0.** BTC 포지션이 마진을 전부 사용 중이라 HOT LIVE 주문 검증을 실행할 수 없다.

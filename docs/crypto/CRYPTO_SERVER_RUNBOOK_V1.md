# US-B CRYPTO - Paper Terminal 서버 운영 런북 V1

> 운영 worktree에서 보존한 과거 기록입니다(2026-10-04 reconciliation). 아래 실측값과 배포 상태는 당시 기록이며 이번 Stage에서 재검증하지 않았습니다. 현재 release의 동작과 정책은 커밋된 코드 및 최신 기능별 계약을 우선합니다. 이 문서의 명령은 이번 Stage에서 실행하지 않았습니다.
> 특히 §13의 BTC 단일 심볼·레버리지 사다리·0.01 수량 상한·AUTO 미지원 설명은 최신 main의 multi-symbol terminal, paper C1 및 live sizing 정책보다 오래된 기록입니다. 원문은 운영 경로와 사고 이력 보존을 위해 남겼습니다.

D3.5 서버 배포본 운영 문서. 대상은 `trader-j`(Naver Cloud Ubuntu)에 상시 기동된
BTCUSDT Paper Trading Terminal이다.

이 서비스는 **Bybit 공개 데이터만** 읽고 **체결은 전부 시뮬레이션**이다.
거래소 계정, API 키, 프라이빗 엔드포인트, 주문 전송 경로가 코드에 존재하지 않는다.

---

## 1. 주소와 접근

| 항목 | 값 |
|---|---|
| 화면 | `https://usb.jptcalc.kr/crypto-paper` |
| API (브라우저 경유) | `https://usb.jptcalc.kr/crypto-api/...` |
| API (서버 내부) | `http://127.0.0.1:8100` |
| 인증 | nginx HTTP Basic (`/etc/nginx/.htpasswd-usb`), 기존 USB 운영 UI와 동일 계정 |

네비게이션 메뉴에는 항목이 없다. 위 URL로 직접 접근한다. 이유는 8절.

`/crypto-api/` 는 `usb.jptcalc.kr` 443 server 블록 **안**에 있으므로 `auth_basic` 이 그대로 적용된다.
자격증명 없는 LONG/SHORT 요청은 nginx에서 401로 끊기고 백엔드까지 가지 않는다.

---

## 2. 구성 요소

```
브라우저
   │  HTTPS + Basic auth
   ▼
nginx (usb.jptcalc.kr)
   ├── /                → 127.0.0.1:3000   usb-frontend   (Next.js, /crypto-paper 페이지 포함)
   ├── /api/, /health   → 127.0.0.1:8000   usb-backend    (주식 쪽, 크립토와 무관)
   └── /crypto-api/     → 127.0.0.1:8100   usb-crypto-paper
                                              │
                                              └── WebSocket 1개 → wss://stream.bybit.com/v5/public/linear
```

브라우저는 Bybit에 직접 접속하지 않는다. 서버가 WS 연결 하나를 소유하고 화면은 1초 주기 HTTP 폴링만 한다.

| 경로 | 내용 |
|---|---|
| `/root/usb_runtime/crypto_paper/src/backend` | 배포된 크립토 소스 스냅샷 (`app/crypto/` 만) |
| ASGI 진입점 | `app.crypto.terminal.server:app` (`api:app` 아님, 11절 참고) |
| `/root/usb_runtime/crypto_paper/run/` | `CRYPTO_PAPER_ROOT`. 런 설정과 런 디렉터리 |
| `/root/usb_runtime/crypto_paper/run/run_config.json` | 런 설정 (수수료·환율·리스크한도 출처 포함) |
| `/root/usb_runtime/crypto_paper/run/paper-server-001/` | 현재 런의 `input.jsonl`, `ledger.jsonl` |
| `/root/usb_runtime/crypto_paper/reference/` | Bybit risk-limit 응답 캡처본 |
| `/root/usb_runtime/crypto_paper/bin/` | 운영 스크립트 |
| `/root/usb_runtime/crypto_paper/logs/feed.log` | 피드 전환 로그 |
| `/root/usb_runtime/crypto_paper/releases/` | 롤백본과 스모크 기록 |

소스를 `/root/usb` 가 아니라 별도 스냅샷으로 둔 이유: `/root/usb` 는 주식 쪽 배포 대상이고
개발 트리의 크립토 코드는 다른 세션이 계속 수정 중이다. 스냅샷을 분리해야
"지금 서버에서 돌고 있는 코드"가 고정된다. 기존 `usb-e-rvol` 과 같은 방식이다.

---

## 3. 서비스 조작

```bash
# 상태
systemctl status usb-crypto-paper
systemctl is-active usb-crypto-paper

# 시작 / 정지 / 재시작
systemctl start   usb-crypto-paper
systemctl stop    usb-crypto-paper
systemctl restart usb-crypto-paper

# 부팅 자동시작 (이미 enabled)
systemctl is-enabled usb-crypto-paper
```

- `Restart=always`, `RestartSec=5` 이므로 크래시 시 5초 뒤 자동 복구된다.
- `WantedBy=multi-user.target` 이므로 서버 재부팅 후 자동 기동된다.
- 중복 실행 방지는 systemd 단일 유닛 + 8100 포트 단독 바인딩으로 보장된다.
- **`--workers 1` 은 성능 설정이 아니라 정합성 요구사항이다.** 워커가 둘이면 계좌가 둘이 되고
  같은 원장에 서로 섞여 append 되어 다음 재시작 때 복구가 거부된다. 절대 올리지 말 것.

---

## 4. 헬스 체크

```bash
crypto-health
```

종료 코드: `0` LIVE, `1` STALE, `2` DISCONNECTED 또는 정지.

출력에 포함되는 것: 유닛 상태, 기동 시각, 재시작 횟수, 백엔드 생존, 런 설정 적재 여부,
Bybit WS 연결, 북 준비 여부, 마지막 시세 수신 시각과 경과 초, 재연결·북갭·재구독 횟수,
계좌 적재 여부, 현재 포지션, 평가금액, 실현·미실현 손익, 모드, 신규진입 허용 여부,
런 ID, 테이프·원장 건수, 남은 디스크와 예상 잔여일.

화면 우측 상단에도 **LIVE / 시세 지연(STALE)** 배지가 나온다.
시세가 5초 넘게 갱신되지 않거나 끊기면 **LONG/SHORT 버튼이 비활성화**되고 사유가 표시된다.
청산(CLOSE)과 비상 종료는 그 상태에서도 계속 가능하다 - 위험을 줄이는 쪽은 막지 않는다.

---

## 5. 로그

| 용도 | 명령 |
|---|---|
| 애플리케이션 로그 (기동·종료·예외) | `journalctl -u usb-crypto-paper -f` |
| 최근 100줄 | `journalctl -u usb-crypto-paper -n 100 --no-pager` |
| 오늘 것만 | `journalctl -u usb-crypto-paper --since today` |
| WS 재연결·단절 로그 | `tail -f /root/usb_runtime/crypto_paper/logs/feed.log` |
| 주문·체결 원장 | `tail -f /root/usb_runtime/crypto_paper/run/paper-server-001/ledger.jsonl` |
| 원장 보기 좋게 | `tail -5 .../ledger.jsonl \| python3 -m json.tool` |
| 입력 테이프 (시세 원본) | `/root/usb_runtime/crypto_paper/run/paper-server-001/input.jsonl` |
| 에러만 | `journalctl -u usb-crypto-paper -p err --no-pager` |

`feed.log` 는 `usb-crypto-feedlog.timer` 가 1분마다 피드 카운터를 샘플링해
**값이 바뀐 순간에만** 한 줄 남긴다. 무사고일 때는 거의 커지지 않는다.
터미널 코드 자체는 재연결을 로그로 남기지 않고 메모리 카운터로만 갖고 있어서 붙인 장치다.

---

## 6. 배포 방법

주식 쪽과 동일한 파일복사 방식이다.

### 6.1 백엔드(크립토 엔진) 갱신

```bash
# 로컬 WSL에서
cd ~/usb
rsync -a --exclude='__pycache__' backend/app/crypto/ \
  traderj:/root/usb_runtime/crypto_paper/src/backend/app/crypto/
ssh traderj 'systemctl restart usb-crypto-paper && sleep 8 && crypto-health'
```

재시작해도 계좌·포지션·원장은 입력 테이프 재생으로 그대로 복원된다(7절).

### 6.2 프론트엔드 갱신

**RAM 961MB 서버다. 빌드 전 반드시 `usb-frontend` 를 정지한다.**
기동 상태로 `npm run build` 를 돌리면 OOM이 나고 `www.jptcalc.kr` 까지 같이 죽는다(2026-09-08 사고).

```bash
# 로컬에서 파일 전송
cd ~/usb/frontend
scp lib/crypto-paper.ts traderj:/root/usb/frontend/lib/
scp components/crypto-paper-terminal.tsx components/crypto-paper.test.tsx traderj:/root/usb/frontend/components/
scp app/crypto-paper/page.tsx traderj:/root/usb/frontend/app/crypto-paper/page.tsx

# 서버에서
ssh traderj
tar czf /root/usb_runtime/crypto_paper/releases/next-build.$(date +%Y%m%d-%H%M).tgz \
  -C /root/usb/frontend .next-build          # 롤백본 먼저
systemctl stop usb-frontend                   # 필수
cd /root/usb/frontend
setsid nohup bash -c 'npm run build > /tmp/crypto_build.log 2>&1; echo EXIT=$? >> /tmp/crypto_build.log' &
# 완료 대기 후
grep '^EXIT=' /tmp/crypto_build.log
systemctl start usb-frontend
```

빌드는 약 3분, 스왑을 300MB 가까이 쓴다. `free -m` 을 같이 샘플링해 두면 좋다.

**`frontend/.env.production.local` 에 아래 두 줄이 있어야 한다.** 이 파일은 git 무시 대상이고
`.env.local` 은 건드리지 않는다.

```
NEXT_PUBLIC_API_BASE_URL=https://usb.jptcalc.kr
NEXT_PUBLIC_CRYPTO_API_BASE=/crypto-api
```

`NEXT_PUBLIC_CRYPTO_API_BASE` 가 없으면 번들에 `http://127.0.0.1:8100` 이 그대로 박혀서
브라우저가 **보는 사람 본인 PC** 로 요청을 보낸다. 화면은 뜨는데 데이터가 안 나오면 이것부터 의심한다.

```bash
# 확인: 0 이어야 정상
grep -ro "127.0.0.1:8100" /root/usb/frontend/.next-build/static /root/usb/frontend/.next-build/server | wc -l
```

### 6.3 nginx 경로

`/etc/nginx/sites-available/usb.jptcalc.kr` 의 443 블록 안 `location /crypto-api/`.
`proxy_pass` 끝의 슬래시가 `/crypto-api/` 접두어를 떼는 역할을 하므로 지우면 안 된다.

```bash
nginx -t && systemctl reload nginx
```

`sites-available/jptcalc.kr` 은 절대 건드리지 않는다. md5 `ddaef1596920176e6d92e26c7c91935e`.

---

## 7. 상태 영속화

별도 스냅샷 파일이 없다. **입력 테이프가 유일한 정본**이고 원장은 그 테이프의 결정적 산출물이다.
두 번째 상태 사본을 두면 둘이 어긋났을 때 어느 쪽이 거짓말인지 알 방법이 없어진다.

- 모든 기록은 한 줄씩 append 후 fsync 된다. 중간에 죽으면 마지막 한 줄만 잘릴 수 있고, 복구가 그 줄을 잘라낸다.
- 재시작하면 테이프를 재생해 계좌·포지션·레버리지·실현손익·수수료·펀딩·모드를 복원한다.
- 원장이 테이프보다 뒤처져 있으면 모자란 꼬리만 이어붙인다. **이미 있는 체결을 다시 쓰지 않는다.**
- 원장이 테이프가 재구성한 것의 접두사가 아니면 `LEDGER_DIVERGENCE` 로 **거부**한다. 조용히 덮어쓰지 않는다.
- 원장 스키마가 바뀐 빌드로 옛 런을 열면 `LEDGER_SCHEMA_MISMATCH` 로 거부한다.

복구 결과는 `crypto-health` 와 `/api/crypto/state` 의 `recovery` 에 나온다.

> **재시작 직후 주의.** `/health` 는 앱이 뜨자마자 200을 주지만 그 시점엔 Bybit 소켓이
> 아직 첫 호가를 못 받았을 수 있다. 그 창에서 주문하면 `409 NO_QUOTE` 로 거부된다(엔진이 옳게 동작한 것).
> 포지션을 들고 재시작했다면 **`crypto-health` 가 LIVE 가 된 뒤에** 조작한다.

---

## 8. 네비게이션

좌측 메뉴 **선물** → `/crypto-paper`. 상위 메뉴는 6개다.

```
대시보드 · 트레이딩 · 종목 분석 · 전략 · 선물 · 시스템
```

`frontend/components/app-shell.tsx` 의 `navigationItems` 한 줄이 정본이고,
5개로 고정돼 있던 테스트 계약 5곳을 6개로 함께 고쳤다
(`navigation` / `dashboard` / `ux-refinement` / `information-architecture` / `strategy-b` 테스트).
심볼이 늘어나면 `activePaths` 에 경로를 추가한다. 지금은 BTCUSDT 하나뿐이라 빈 서브메뉴를 만들지 않았다.

크립토 화면에서는 헤더의 **주식 전용 정보(현재시각·기준거래일·USD/KRW·미국 장 세션·브로커 모드)를 숨긴다.**
24시간 도는 무기한 선물 옆에서 "시장 UNKNOWN" 은 틀린 정보다.
`isEquityChrome(pathname)` 한 함수가 판단하고, 다른 화면은 그대로 둔다.

---

## 8.1 화면 구성 (UI V2)

모바일 우선. 위에서부터 **가격·계좌 요약 → 차트 → 주문 → 포지션 → 접힌 상세** 순이다.
데스크탑(xl)에서는 차트와 주문 패널이 2열로 붙는다.

측정값 (재설계 전 → 후, 전체 페이지 높이):

| 뷰포트 | 전 | 후 |
|---|---|---|
| 360px | 6827px | 1529px |
| 390px | 6767px | 1513px |
| 430px | 6646px | 1492px |
| 1440px | 4176px | 1113px |

전 뷰포트 가로 overflow 0. 360/390 에서 LONG 버튼은 첫 화면에서 한 번만 짧게 내리면 닿고,
430 이상에서는 스크롤 없이 보인다.

기본 화면에서 뺀 것: CRYPTO eyebrow, 큰 제목, 장문 설명, 복구 세션·원장/입력 건수,
체결 방식 설명, KRW 환율 안내문, 대형 KPI 카드 4개, 성과 패널, 거래기록 표, 원장 표, 큰 AUTO 패널.
**지운 게 아니라 접었다.** 성과·거래기록·원장·런 근거는 화면 하단 접힘 영역에 그대로 있고,
AUTO 는 주문 패널에 한 줄(`AUTO · 준비중`)로 남고 설명은 "런 설정 근거" 안에 있다.

### 차트

`lightweight-charts` 5.2.1 (Apache-2.0). 이 저장소의 첫 차트 의존성이다.
기존 화면은 전부 손으로 그린 inline SVG였는데, 이번에 요구된 crosshair·zoom·pan·거래량·
가격선 오버레이를 직접 구현하면 그 자체가 하나의 프로젝트가 된다.

- `next/dynamic` + `ssr: false` 로 지연 로드. 캔버스 라이브러리라 서버 렌더에서 `window` 를 만지고,
  초기 번들에 넣으면 다른 화면까지 값을 치른다. `/crypto-paper` first load 는 117 kB 를 유지한다.
- **전이 의존성 `fancy-canvas` 가 같이 있어야 한다.** 이걸 빼먹으면 서버 빌드가
  `Module not found: Can't resolve 'fancy-canvas'` 로 실패한다(실제로 1차 빌드가 이걸로 깨졌다).
- 타임프레임 1m / 3m / 5m / 15m. **1분봉이 유일한 원천**이고 나머지는 프론트에서 접는다
  (`aggregateCandles`). 버킷 경계는 `floor(start / span)` 이라 거래소 봉 경계와 같다.
  한 버킷 안에 미확정 1분봉이 하나라도 있으면 그 캔들은 미확정으로 둔다.
- 오버레이: Mark(항상), 진입·청산(포지션 보유 시). **Target/Stop 선은 그리지 않는다.**
  전략이 없으므로 그 값은 지어낸 숫자가 되고, 차트 위의 지어낸 선은 진짜 선과 똑같이 보인다.
- 데이터는 `setData`/`update` 로 넣는다. 1초마다 차트를 다시 만들면 보던 확대·이동이 초기화돼
  쓸 수가 없다.

### 배포 시 주의

프론트 빌드에 `node_modules/lightweight-charts` 와 `node_modules/fancy-canvas` 가 서버에 있어야 한다.

```bash
cd ~/usb/frontend
rsync -a --delete node_modules/lightweight-charts/ traderj:/root/usb/frontend/node_modules/lightweight-charts/
rsync -a --delete node_modules/fancy-canvas/       traderj:/root/usb/frontend/node_modules/fancy-canvas/
scp package.json package-lock.json traderj:/root/usb/frontend/
```

---

## 8.2 예전 네비게이션 메모 (해소됨)

사이드바 메뉴에 "Crypto Paper" 를 넣지 않았다.
`frontend/components/navigation.test.tsx` 가 상위 메뉴를 정확히 5개(`대시보드·트레이딩·종목 분석·전략·시스템`)로
고정하고 있고, 그 5메뉴 IA는 다른 세션이 의도적으로 동결한 계약이다.
6번째 항목을 넣으면 그 테스트가 깨지고 남의 결정을 뒤집게 된다.

메뉴가 필요하면 그 세션과 합의한 뒤 `navigationItems` 와 `navigation.test.tsx` 를 함께 고쳐야 한다.
그 전까지는 `https://usb.jptcalc.kr/crypto-paper` 를 북마크한다.

---

## 9. 롤백

```bash
# 프론트엔드 빌드 되돌리기
systemctl stop usb-frontend
cd /root/usb/frontend && rm -rf .next-build
tar xzf /root/usb_runtime/crypto_paper/releases/next-build.before-crypto-api.tgz -C /root/usb/frontend
systemctl start usb-frontend

# 크립토 프론트 소스 되돌리기
tar xzf /root/usb_runtime/crypto_paper/releases/frontend-src.before-crypto-api.tgz -C /root/usb/frontend

# nginx 되돌리기 (= /crypto-api 경로 제거)
cp /root/usb_runtime/crypto_paper/releases/usb.jptcalc.kr.before-crypto-api \
   /etc/nginx/sites-available/usb.jptcalc.kr
nginx -t && systemctl reload nginx

# 크립토 서비스 완전 제거 (데이터는 남는다)
systemctl disable --now usb-crypto-paper usb-crypto-feedlog.timer
rm /etc/systemd/system/usb-crypto-paper.service \
   /etc/systemd/system/usb-crypto-feedlog.{service,timer}
systemctl daemon-reload
```

크립토 서비스를 내려도 주식 쪽 `usb-backend` / `usb-frontend` 는 영향받지 않는다.
반대로 주식 백엔드가 죽어도 크립토 터미널은 계속 돈다. 서로 import 관계가 없다.

---

## 10. 장애 시 조치

### 화면은 뜨는데 숫자가 안 나온다
1. `crypto-health` - DISCONNECTED 면 서비스부터.
2. `grep -ro "127.0.0.1:8100" /root/usb/frontend/.next-build/... | wc -l` 이 0이 아니면 6.2의 env 누락. 재빌드.
3. 브라우저 개발자도구 Network 에서 `/crypto-api/api/crypto/state` 가 401이면 로그인 안 된 것, 502면 백엔드가 죽은 것.

### 502 Bad Gateway
`/crypto-paper` 만 502면 `systemctl start usb-crypto-paper`.
사이트 전체가 502면 `usb-frontend` 가 죽은 것이다 - 프론트 빌드 중이면 정상이고, 아니면
`systemctl status usb-frontend` 와 `journalctl -u usb-frontend -n 50`.

### 시세가 계속 STALE
`tail /root/usb_runtime/crypto_paper/logs/feed.log` 로 단절 시각 확인.
`journalctl -u usb-crypto-paper -n 50`. 재연결은 최대 30초 백오프로 자동 재시도한다.
그래도 안 붙으면 `systemctl restart usb-crypto-paper` (포지션은 복원된다).

### 서비스가 재시작을 반복한다
`journalctl -u usb-crypto-paper -n 100` 에서 복구 거부 코드를 본다.
`LEDGER_DIVERGENCE` / `LEDGER_SCHEMA_MISMATCH` 면 **파일을 지우지 말고** 런 디렉터리를 통째로
`releases/` 에 보존한 뒤 새 `run_id` 로 새 런을 시작한다. 원인 분석 재료가 그 파일들이다.

`MemoryMax=200M` 초과로 죽는 경우 `systemctl show usb-crypto-paper -p MemoryPeak` 로 확인.
정상 사용량은 20~45MB다.

### 디스크
입력 테이프는 1Hz 로 계속 쌓이고 **로테이션할 수 없다**(복구가 전체 재생이라 자르면 런이 깨진다).

```
실측 약 388 B/s = 하루 약 34MB = 한 달 약 1GB
```

```bash
df -h /                                   # 남은 용량
du -sh /root/usb_runtime/crypto_paper/run/*
journalctl --disk-usage                   # journald가 이미 약 400MB 쓰고 있다
journalctl --vacuum-size=200M             # 급하면 여기부터
```

여유가 200MB 밑으로 가면 런을 교체한다. 테이프를 자르는 게 아니라 런을 새로 시작한다.

```bash
systemctl stop usb-crypto-paper
mv /root/usb_runtime/crypto_paper/run/paper-server-001 \
   /root/usb_runtime/crypto_paper/releases/paper-server-001.$(date +%Y%m%d)
# run_config.json 의 run_id 를 paper-server-002 로 수정
systemctl start usb-crypto-paper
```

**주의: 런을 교체하면 계좌가 초기 자본으로 리셋된다.** 누적 성과는 보존한 원장에만 남는다.

---

## 11. 빠른 수량 (Quick Position Size)

주문 패널의 `[25%] [HALF] [75%] [MAX]` 는 프론트가 계산한 값이 아니다.
`GET /api/crypto/sizing` 이 돌려준 값을 그대로 쓴다.

```bash
curl -s http://127.0.0.1:8100/api/crypto/sizing | python3 -m json.tool | head -40
curl -s "http://127.0.0.1:8100/api/crypto/sizing?side=LONG"
```

**MAX 는 공식으로 역산하지 않는다.** 시장가는 호가를 걸어 올라가므로 체결가가 수량에 따라 달라지고,
따라서 마진과 수수료도 수량에 따라 달라진다. 닫힌 형태로 풀 수 있는 값이 아니다.
대신 엔진 자신에게 물어본다. 계좌를 복제한 일회용 엔진(파일 없는 `Ledger()`)에 주문을 넣어 보고
받아들여지는 가장 큰 격자 수량을 이분탐색으로 찾는다. 화면에 뜨는 수량·명목·필요마진·예상청산가는
전부 그 체결된 복제본에서 읽은 값이다.

- 프론트에는 마진/레버리지 공식이 없고 있어서도 안 된다.
- 25/50/75 는 MAX 를 비율로 줄인 뒤 **각각 다시 probe** 한다. 호가를 걸어 올라가면 절반 수량의
  체결가는 MAX 체결가의 절반이 아니기 때문이다.
- LONG 과 SHORT 는 각각 계산된다. LONG 은 매도호가를, SHORT 는 매수호가를 걸어 올라가므로
  호가가 한쪽으로 쏠리면 같은 프리셋도 수량이 다르다. 버튼에 각 방향의 수량이 적혀 있다.
- 불가능한 프리셋은 비활성되고 엔진이 준 거절 코드를 그대로 보여준다
  (`INSUFFICIENT_MARGIN`, `REVERSE_NOT_ALLOWED`, `NO_LIQUIDITY` 등).
- `MAX` + 레버리지 20배 이상이면 **HIGH RISK** 배지가 붙는다. 표시용 경고일 뿐,
  실제 거절은 엔진 한도가 한다.
- LONG/SHORT 클릭은 추가 확인 없이 즉시 시장가 주문이다. 수량·마진·청산가가 이미 위에 떠 있기 때문이다.
  EMERGENCY CLOSE 만 체크박스를 유지한다.

probe 는 **실계좌를 건드리지 않는다.** 원장에 이벤트를 쓰지 않고, 포지션을 옮기지 않고,
수수료를 물리지 않는다. 조회만으로 매매가 일어나면 안 되기 때문이고, 테스트로 고정돼 있다.

검증 명령 (MAX 가 진짜 상한인지):

```bash
# MAX 는 체결되고, 한 스텝 위는 INSUFFICIENT_MARGIN 이어야 한다
curl -s http://127.0.0.1:8100/api/crypto/sizing | python3 -c "
import json,sys; d=json.load(sys.stdin)
print([p for p in d['sides']['LONG']['presets'] if p['label']=='MAX'])"
```

### 진입점이 `server.py` 인 이유

`terminal/api.py` 는 연구 세션이 계속 확장 중인 파일이다. 한 워킹트리에서 두 세션이 같은
`create_app()` 을 편집하면 병합되지 않고 **나중에 저장한 쪽이 조용히 이긴다.**
그래서 sizing 라우트는 `terminal/server.py` 에서 `api.py` 가 만든 *같은* app 객체에 얹는다.
`api.py` 는 한 줄도 바뀌지 않았다.

systemd 는 반드시 `app.crypto.terminal.server:app` 을 가리켜야 한다.
`api:app` 으로 되돌리면 `/api/crypto/sizing` 이 사라지고 빠른 수량 버튼이 전부 죽는다.

---

## 11.5 가상계좌 초기화 (RESET)

주문 패널 위 `[가상계좌 초기화]` 버튼, 또는

```bash
curl -s -X POST http://127.0.0.1:8100/api/crypto/reset \
  -H "Content-Type: application/json" -d '{}'
# 금액을 직접 지정하려면 -d '{"target_krw":"5000000"}'
```

기본 금액은 **10,000,000원**이고 정본은 상수 하나다
(`backend/app/crypto/paper/config.py` 의 `DEFAULT_STARTING_CAPITAL_KRW`).
새 런의 시작자본 기본값도 같은 상수에서 온다(`crypto_paper_run.py --starting-capital-krw`).

**RESET 은 삭제가 아니다.** 주문·체결·수수료·펀딩·실현손익 누계·원장·성과기록은 전부 그대로 남고,
원장에 `ACCOUNT_RESET` 한 줄이 추가될 뿐이다. 그 줄에 before/after 잔고, 고정환율,
그리고 보존된 실현손익·수수료·펀딩이 같이 적힌다.

동작 원리는 **자본 앵커**다. 계좌는 `realized_pnl` 등 누계를 되감지 않고,
"여기서부터 쓸 수 있는 돈" 기준점만 옮긴다.

```
wallet = capital_base + (realized_pnl - realized_at_anchor) - (charges - charges_at_anchor)
```

리셋이 없으면 앵커는 `(시작자본, 0, 0)` 이라 예전 식과 완전히 같다.
그래서 **기존 런의 테이프가 그대로 바이트 일치로 재생된다**(실서버 72,987 레코드로 실측 확인).

**포지션 보유 중에는 거부된다**(`RESET_BLOCKED_OPEN_POSITION`, HTTP 409).
자동청산하지 않는다. 잔고 정리를 위해 아무도 고르지 않은 가격에 체결을 내는 셈이기 때문이다.
사용자가 `CLOSE` → `FLAT` → `RESET` 순서로 직접 한다. 거부된 리셋은 테이프에도 원장에도 남지 않는다.

분석은 리셋으로 끊기지 않는다. 거래 통계(총거래·승률·PF·수수료·방향별·레버리지별)는
리셋 전후 전체를 대상으로 계속 계산되고, 자본곡선이 필요한 지표만 `ACCOUNT_RESET` 을
경계로 구간을 나눈다(`performance` 응답의 `capital_resets`, `segments`).
충전액이 수익으로 잡히거나 이전 낙폭이 회복된 것처럼 보이지 않게 하기 위한 것이다.

> **기존 런의 시작자본은 바꿀 수 없다.** `run_config.json` 의 `starting_capital_krw` 는
> `RUN_START` 원장 이벤트 안에 스냅샷으로 들어 있어, 고치면 재생 결과가 달라져
> `LEDGER_DIVERGENCE` 로 기동이 거부된다. 운영 중인 런의 잔고를 바꾸는 방법은 RESET 뿐이고,
> 10,000,000원 기본값은 **새 런**에 적용된다.

---

## 11.6 테이프 메모리 (2026-09-24 사고)

입력 테이프를 메모리에도 들고 있어서 프로세스가 무한정 커졌다.
1Hz 로 하루 쌓이면 레코드 7만 건이 되고, 그 리스트만으로 `MemoryMax=200M` 을 넘겨
**재기동이 "Waiting for application startup" 에서 멈췄다.**

고친 내용:
- 라이브 세션은 `InputTape(retain=False)` - 파일에만 쓰고 메모리에는 개수만 센다.
- 복구는 `InputTape.stream()` 으로 한 줄씩 재생한다(통째로 읽지 않는다).
- 복구 세션은 `InputTape.scan()` 한 번으로 개수·START 시각·마지막 시세 시각만 얻는다.

실측: 실서버 테이프 72,987 레코드(29.9MB) 재생에 **peak RSS 19MB, 0.9초**, 바이트 일치.
운영 사용량은 200M 한도 대비 **10M** 으로 내려왔다.

진단 명령:

```bash
systemctl show usb-crypto-paper -p MemoryCurrent -p MemoryPeak -p MemoryMax --value
```

`MemoryCurrent` 가 `MemoryMax` 에 붙어 있고 `/health` 가 안 뜨면 이 증상이다.

---

## 11.6.1 현재 손익 vs 전체 누적 손익

메인 화면 계좌 요약의 **현재 손익**은 **마지막 ACCOUNT_RESET 이후**의 손익이다.
초기화 직후에는 정확히 0이다. 리셋은 기록을 지우지 않으므로, 리셋 전 손익까지 더한
**전체 누적 손익**은 "이 세션의 성과" 접힘 안에 그대로 있다.

두 값은 섞지 않는다.

```
현재 손익        = (누적 realized - 리셋앵커 realized)
                 - (누적 fees     - 리셋앵커 fees)
                 - (누적 funding  - 리셋앵커 funding)
전체 누적 손익   = RUN_START 이후 전 거래
```

앵커는 `ACCOUNT_RESET` 이벤트가 이미 들고 있는 `preserved_realized_pnl` / `preserved_fees` /
`preserved_funding` 이다. 빼기만 하므로 프론트에도 백엔드에도 새 금융 계산식이 없다.

백엔드 `/api/crypto/performance` 의 `current_segment` 가 정본이다.

```bash
curl -s http://127.0.0.1:8100/api/crypto/performance | python3 -c "
import json,sys
p=json.load(sys.stdin); c=p['current_segment']
print('LIFETIME', p['trades'], p['net_pnl'])
print('CURRENT ', c['trades'], c['current_segment_net_pnl'], 'reset#', c['reset_count'])"
```

**검산**: `current_segment_net_pnl` 은 항상 `wallet_balance - capital_base_usdt` 와 같아야 한다.
어긋나면 화면이 거짓말을 하고 있는 것이다. 테스트로 고정돼 있다.

리셋이 한 번도 없으면 두 값은 같고, 성과 상세의 "현재 구간" 블록은 표시되지 않는다.

---

## 11.6.2 SAFE MAX (D4.1)

`MAX` 는 **"이 스냅샷에서 진입과 즉시 전량 청산이 모두 되는 최대 수량"** 이다.
진입만 보던 예전 정의는 청산이 거부되는 포지션을 만들 수 있었다(실제 발생).

```bash
curl -s http://127.0.0.1:8100/api/crypto/sizing | python3 -c "
import json,sys
d=json.load(sys.stdin)
for s,v in d['sides'].items():
    print(s, 'MAX', v['max_qty'], '진입깊이', v['entry_depth'], '청산깊이', v['exit_depth'])
print(d['sides']['LONG']['max_definition'])"
```

왕복은 호가 양면을 모두 지나므로 **얇은 쪽이 양방향을 똑같이 구속**한다.
그래서 LONG MAX와 SHORT MAX는 깊이가 제약일 때 같은 값으로 수렴한다. 정상이다.

주문 시점에 **양면 probe를 다시** 돌린다. 몇 초 전 preview는 보장이 아니다.
안전하지 않으면 409로 거부하고 현재 안전 최대를 메시지에 담는다.
`intent=CLOSE` 는 이 게이트를 통과하지 않는다.

**미래 깊이 버퍼는 넣지 않았다.** 운영 테이프 78,537건 측정 결과 1초 뒤 깊이 비율의
중앙값은 1.00인데 5퍼센타일이 0.11이라, 어떤 고정 비율도 방어할 수 없다.
근거와 분포는 `docs/crypto/CRYPTO_D4_1_SAFE_SIZING_V1.md` 5절.

부분 청산은 엔진이 지원하지 않는다. 깊이보다 큰 포지션은 **깊이가 회복된 뒤 재시도**하면
청산된다(실제로 그렇게 해소했다).

---

## 11.6.3 운영 Paper 계좌 취급 규칙

`paper-server-001` 은 **사용자 실사용 계좌**다. 개발 테스트로 오염시키지 않는다.

1. smoke는 **격리 인스턴스**에서 한다.

```bash
mkdir -p /tmp/smoke/run
cp /root/usb_runtime/crypto_paper/run/run_config.json /tmp/smoke/run/
# run_id 를 바꾼 뒤
CRYPTO_PAPER_RUN_CONFIG=/tmp/smoke/run/run_config.json CRYPTO_PAPER_ROOT=/tmp/smoke/run \
PYTHONPATH=/root/usb_runtime/crypto_paper/src/backend \
setsid nohup /root/usb/.venv/bin/uvicorn app.crypto.terminal.server:app \
  --host 127.0.0.1 --port 8101 --workers 1 --no-access-log &
```

2. **사용자가 포지션을 들고 있으면 운영 주문·RESET 금지.**
3. 기존 거래를 개발 테스트라고 가정하지 않는다.
4. 운영에서 테스트 거래를 만들었다면, 아래가 **전부** 맞을 때만 cleanup(RESET)한다.
   - 테스트 전 사용자 상태가 FLAT이었다
   - 그 거래가 이번 세션 것임이 타임스탬프로 명확하다
   - 사용자 실거래와 혼동되지 않는다

하나라도 불명확하면 **운영 상태를 그대로 둔다.**

---

## 11.7 테이프 세그먼트와 체크포인트 (D4)

활성 테이프가 2만 레코드(약 6시간)를 넘으면 자동으로 닫히고 체크포인트가 남는다.
재기동은 최신 체크포인트에서 시작해 그 뒤 꼬리만 재생한다.

```
run/<run_id>/
  input.jsonl                 활성 (append only)
  ledger.jsonl                전체 원장 (분할 안 함)
  segments/000001.input.jsonl[.gz] + 000001.manifest.json
  checkpoints/000001.checkpoint.json
```

**원장이 여전히 authority다.** 체크포인트는 원장 prefix를 sha256으로 증언할 뿐이고,
디스크의 원장이 그 바이트로 시작하지 않으면 체크포인트를 거부하고 전체 재생으로 내려간다.
체크포인트는 재기동을 빠르게 할 수는 있어도 틀린 상태를 맞은 것처럼 만들 수는 없다.

점검:

```bash
# 세그먼트 무결성 (전건 재해시)
PYTHONPATH=/root/usb_runtime/crypto_paper/src/backend /root/usb/.venv/bin/python -c "
from pathlib import Path
from app.crypto.paper import segments as s
run = Path('/root/usb_runtime/crypto_paper/run/paper-server-001')
m = s.load_manifests(run)
print('segments', len(m), 'broken', s.verify_segments(run, m))
print(s.SegmentedTape(run_dir=run).storage())
"

# 마지막 복구가 체크포인트였는지
curl -s http://127.0.0.1:8100/api/crypto/state | python3 -c "import json,sys; print(json.load(sys.stdin)['recovery'])"
```

화면에서는 "런 설정 근거" 접힘 안에 **테이프 보관**과 **마지막 복구**로 보인다.

### [사고 2026-09-24] 회전 후 재기동이 START를 다시 발행

회전하면 `START` 커맨드가 닫힌 세그먼트로 넘어간다. 세션이 시작시각을 **활성 테이프만 스캔해서**
찾던 시절에는 그게 None이 되어 "아직 시작 안 됨"으로 판단하고 `START` 를 다시 기록했다.
`start()` 는 엔진에서 `RuntimeError: engine already started` 로 실패하는데 그 전에 테이프에는
이미 기록돼서, 관측 루프가 매 틱 재시도하며 **중복 START 5,577건**이 쌓였다.
그 다음 재기동에서 그 START를 재생하다 죽어 **서비스가 기동하지 못했다**.

고친 내용:
- 세션의 시작시각은 **원장의 `RUN_START` 이벤트**에서 읽는다(원장은 항상 통째로 메모리에 있다).
- 마지막 시세 시각은 엔진이 재생 중 추적한 값을 쓴다.
- 재생 중 만나는 **두 번째 START는 무시**한다. 첫 START가 실제로 일어난 것이고,
  로드 자체를 거부하면 운영자에게 남는 게 없다.
- 활성 테이프 레코드 수에 세그먼트 레코드 수를 더해 보고한다.

기존에 쌓인 중복 START 레코드는 그대로 두었다(재생 시 무시되고, 테이프는 append-only라 지우지 않는다).

증상 확인:

```bash
curl -s http://127.0.0.1:8100/health          # configured:false + "engine already started"
grep -c '"command":"START"' /root/usb_runtime/crypto_paper/run/paper-server-001/input.jsonl
```

### 강제 전체 재생 (체크포인트 의심 시)

```python
recover(run_dir, config, tiers, use_checkpoint=False)
```

체크포인트 복구와 전체 재생이 같은 원장을 만드는지가 테스트로 고정돼 있다.

### 압축 켜기

**현재 배포본은 압축 OFF다.** 켜려면 `PaperSession(compress_segments=True)`.
압축본을 다시 풀어 원본 해시와 일치할 때만 평문을 지우고, manifest가 두 해시를 모두 보관한다.

실측 압축비 **9.8배**. 디스크 여유 1.3GB 기준 운영 horizon이 **37일 -> 361일**로 늘어난다.
그 이상은 세그먼트를 서버 밖으로 옮긴다(immutable + checksum이라 안전하다).

---

## 11.8 수수료 시나리오 (D4)

요율은 공식이고 **티어가 가정**이다.

```bash
python backend/app/dev/crypto_paper_replay.py --list-fee-scenarios
python backend/app/dev/crypto_paper_replay.py ... --fee-scenario ZERO
```

`VIP_0`(현재 운영값) / `VIP_1~SUPREME_VIP`(공표 요율이나 도달하지 않은 티어) /
`ZERO`(수수료 추정이 아니라 결과가 비용을 이겨내야 하는 상한).
동일 테이프에서 gross는 같고 비용만 달라지므로 결론을 단일 수수료 가정에 묶지 않을 수 있다.

---

## 12. 하지 않는 것

배포 범위에 없고 코드에도 없다. 넣으려면 별도 승인이 필요하다.

- Edge / Score / Probability 계산
- 자동매매(AUTO) 판단 로직 - 상태는 정의돼 있으나 `AUTO_ON` 은 `AUTO_NOT_READY` 로 거부된다
- Bybit 계정, API 키, 프라이빗 엔드포인트, 실주문
- 실거래 전환

사용하는 외부 엔드포인트는 이 둘뿐이다.

```
wss://stream.bybit.com/v5/public/linear
https://api.bybit.com        (/v5/market/kline 차트 시드용)
```

---

## 13. Binance USDⓈ-M LIVE 수동매매 (2026-09-30 배포)

12절은 이 절이 생기기 전의 범위다. **Bybit 공개 데이터로 도는 PAPER는 그대로이고**, 그 옆에
Binance 실계좌 수동매매가 추가됐다. AUTO는 여전히 실계좌 주문을 낼 수 없다.

화면은 같은 `/crypto-paper`이고 상단 스위치로 **PAPER ↔ BINANCE LIVE**를 고른다.
두 계좌는 섞이지 않는다. LIVE 패널의 모든 수치는 Binance 응답이 정본이고, PAPER는
기존 입력 테이프/원장이 정본이다.

### 13.1 시크릿 경계

```
/root/usb_runtime/crypto_paper/secrets/binance_live.env   600 root:root
/root/usb_runtime/crypto_paper/secrets/                   700 root:root
```

유닛에서 `EnvironmentFile=` 한 줄로만 연결한다. **`/root/usb/.env`(주식 쪽)는 여전히 읽지
않는다.** 이 서비스가 자격증명을 하나 갖게 됐지만, 지켜야 했던 경계 - 크립토가 키움 브로커를
못 보고, 주식 서비스가 Binance 키를 못 보는 것 - 는 "아무것도 없음"이 아니라 "분리"로
유지된다.

파일에 들어가는 것:

| 변수 | 값 | 이유 |
|---|---|---|
| `BINANCE_API_KEY` / `BINANCE_API_SECRET` | (비공개) | 지문 `487ea08e` / `b1254d54` |
| `BINANCE_LIVE_TRADING_ENABLED` | `true` | **capability**. 이 호스트가 주문을 낼 수 *있다*는 뜻이지 내고 있다는 뜻이 아니다 |
| `BINANCE_LIVE_MAX_QTY` | `0.01` | 신규 OPEN 상한. 13.4 참고 |

**`BINANCE_LIVE_CLIENT_ARMED`는 넣지 않는다.** 넣으면 부팅부터 영원히 무장돼서 아래 장치가
통째로 무의미해진다. 이 변수는 로컬 검증 전용으로 의미가 남아 있다.

### 13.2 두 게이트: capability와 arm

```
BINANCE_LIVE_TRADING_ENABLED (env)   이 배포가 주문을 낼 수 있는가   ← 파일을 고쳐야 바뀜
ArmSession (프로세스 메모리)          지금 내고 있는가                ← 화면에서 클릭, 시간 지나면 자동 해제
```

- **부팅은 항상 해제.** 세션은 어디에도 저장되지 않으므로 재시작·크래시·배포 후 무장 상태로
  돌아올 수 없다
- 무장하려면 화면에서 확인 문구 `ARM LIVE TRADING`을 **정확히** 입력해야 한다. 페이지 로드,
  prefetch, 재시도 GET 어느 것도 계정을 무장시키지 못한다
- 기본 TTL 900초. 만료되면 스스로 닫힌다
- **만료는 게이트를 읽는 순간 적용된다.** 타이머가 아니고 화면 폴링에도 의존하지 않는다.
  탭을 닫아도 창은 닫힌다
- 세션은 세 번째 게이트가 아니라 **두 번째 게이트 그 자체**다. 무장이
  `BinanceFuturesClient.trading_enabled`에 직접 쓰고, 그 플래그가 `rest.call`이 TRADE 요청을
  만들기 전에 검사하는 값이다. 판단하는 곳은 하나뿐이다

상태 확인:

```bash
curl -s http://127.0.0.1:8100/api/crypto/binance/arm | python3 -m json.tool
# armed=false, capability=true 가 기동 직후의 정상 상태다
```

### 13.3 레버리지와 마진 모드

- 선택 가능한 값은 **계정 자신의 `GET /fapi/v1/leverageBracket`**에서 나온다. 코드에 상한을
  적어두지 않았다. BTCUSDT 실측 상한은 150x(bracket 1, 명목 ≤ 300,000 USDT)
- 화면 사다리는 `1·2·3·5·10·20·50`이고 구간표 상한으로 걸러진다. 계정 리스크 티어가
  내려가면 선택지도 같이 줄어든다
- **optimistic update 없음.** 변경 → Binance 응답 → `symbolConfig` 재조회 → 재조회값만 표시
- **포지션 보유 중에는 변경 차단**
- **마진 모드는 표시 전용.** `POST /fapi/v1/marginType`은 엔드포인트 deny list에 남아 있다.
  Cross/Isolated 변경은 Binance 앱/웹에서 한다
- 명목·개시증거금·청산가는 전부 Binance `positionRisk` 값이다. 프론트에서 계산하지 않는다.
  포지션이 없으면 청산가는 `-`이고 "포지션 생성 후 Binance가 산출"이라고 적힌다
- 화면 문구: **레버리지는 노출 배수가 아니라 증거금 설정이다.** 0.001 BTC는 1x에서도
  50x에서도 0.001 BTC이고, 달라지는 것은 묶이는 증거금과 청산가다

### 13.4 신규 OPEN 수량 상한 정책

`BINANCE_LIVE_MAX_QTY=0.01`.

검증 때 쓰던 0.001은 실사용을 막으므로 운영 기본값으로 두지 않았다. 반대로 미설정도 택하지
않았다. 0.01 BTC는 현재 시세로 약 830 USDT 명목이고, 지갑 374 USDT·20x 기준으로 실제 수동
운용에 걸리지 않는 크기다. 동시에 가장 흔한 오타 - 자릿수 하나 밀리는 것 - 를 **우리 코드가
이유를 붙여 거부**하게 만든다. 상한이 없으면 같은 오타가 Binance의 증거금 부족 오류로만
막히고, 그건 메시지가 불친절할 뿐 아니라 계좌가 커지면 더 이상 막아주지도 않는다.

**CLOSE는 이 상한의 적용을 받지 않는다.** 어떤 상한 정책에서도 포지션은 항상 전량 종료할 수
있어야 한다. 상한보다 큰 포지션(Binance 앱에서 직접 연 것 등)도 화면에서 청산된다.

계좌가 커지면 이 한 줄만 고치고 재시작한다.

### 13.5 User Data Stream

```
wss://fstream.binance.com/private/ws?listenKey=<key>
```

경로가 아니라 **쿼리스트링**이고 `events=` 필터는 보내지 않는다. legacy `/ws/<listenKey>`는
2026-04-23에 폐기됐고, 폐기된 URL은 **조용히** 실패한다 - 핸드셰이크도 ping/pong도 정상인데
프레임만 0건이다. 자세한 규명은 `binance_live/CRYPTO_BINANCE_LIVE_FINALIZATION_V1.md` §1.

스트림의 역할은 `CHANGE_SIGNAL_ONLY`다. **잔고·포지션의 정본은 언제나 REST**이고, 스트림은
"지금 다시 읽어라"는 신호만 준다. 스트림이 끊겨도 화면은 폴링으로 계속 맞고, 재시작하면
로컬이 아니라 Binance에서 상태를 복원한다.

```bash
curl -s http://127.0.0.1:8100/api/crypto/binance/status \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["stream"])'
```

### 13.6 배포

백엔드는 기존 6.1과 같은 스냅샷 rsync다. 단 **커밋된 내용만** 보내는 편이 안전하다 -
개발 트리의 `app/crypto/` 밑에는 다른 세션의 미커밋 연구 파일이 섞여 있다.

```bash
cd ~/usb
git archive HEAD backend/app/crypto | tar -x -C /tmp/deploy_head
rsync -a --exclude='__pycache__' /tmp/deploy_head/backend/app/crypto/ \
  traderj:/root/usb_runtime/crypto_paper/src/backend/app/crypto/
ssh traderj 'systemctl restart usb-crypto-paper && sleep 10 && crypto-health'
```

`--delete`는 쓰지 않는다. 프론트는 6.2 그대로(빌드 전 `usb-frontend` 정지 필수).

### 13.7 사용자 사용 절차

1. `https://usb.jptcalc.kr/crypto-paper` 접속 (기존 Basic auth)
2. 상단 **BINANCE LIVE** 선택 → 실제 잔고·포지션 확인
3. **무장** 버튼 → 확인 문구 `ARM LIVE TRADING` 입력
4. 레버리지 선택 (원하면)
5. 수량 입력 후 **LONG** / **SHORT** → 확인 대화상자에서 **실주문**
6. **CLOSE**로 전량 청산
7. 끝나면 **해제**. 누르지 않아도 TTL 만료나 서버 재시작 시 자동 잠금

PAPER로 되돌리면 기존 가상계좌가 그대로 있다. AUTO는 계속 비활성이다.

### 13.8 이 절에서도 하지 않는 것

- AUTO의 실계좌 주문. `LiveOrderRouter.submit`의 호출자는 수동 주문 라우트 하나뿐이고
  `research/**`·`paper/**` 중 live 패키지를 import하는 모듈은 0개다
- 출금·이체. API 키 권한이 false이고 `/sapi/`·`withdraw`·`transfer` 경로는 코드에서 차단된다
- Spot / Margin
- 마진 모드 변경
- BTCUSDT 이외의 심볼

# US-B CRYPTO D5.3 Liquidation Collector Report V1

작성 2026-09-27. 계약 `CRYPTO_D5_3_LIQUIDATION_FORWARD_CONTRACT_V1.md`, 사전 `CRYPTO_LIQUIDATION_DATA_DICTIONARY_V1.md`, 계획 `CRYPTO_FORWARD_DATA_PLAN_V2.md`.
**판정: PASS** (31절). 전략·Score·AUTO·주문·Paper 변경 0.

---

## 1. Git start / end

| 항목 | 값 |
|---|---|
| branch | `main` |
| start HEAD | `8a6ec2d` |
| end HEAD | `96c034a` (다른 세션 커밋 4개: `a5c683c`, `f3ac1fe`, `20328a6`, `96c034a`. Strategy H 연구 등, 이번 작업 파일 미포함) |
| commit / push | 0 |
| tracked 수정 | 23개, 전부 다른 세션 작업. 건드리지 않음 |

## 2. Changed files (전부 신규, untracked)

| 경로 | 내용 |
|---|---|
| `backend/app/crypto/liquidation_forward/__init__.py` | 버전 상수 (`liqfwd.1.1`, `liq.v1`) |
| `.../parsers.py` | 3사 메시지 → `liq.v1` 행, side 규칙, 중복 제거 |
| `.../store.py` | 시간 세그먼트(.partial → gzip → 원자적 rename → sha256), 복구, 디스크 여유 |
| `.../collector.py` | 수집 프로세스 (WS 3개 + OKX REST 폴러 + heartbeat + 단일 인스턴스 락) |
| `.../daily.py` | 일일 parquet·manifest, UP/DOWN 구간, 품질 보고, heartbeat 압축 |
| `backend/tests/crypto/test_liquidation_forward.py` | 16개 테스트 |
| `docs/crypto/` | 계약, 보고서(이 문서), 데이터 사전, Forward Data Plan V2 |

서버에 새로 만든 것: `/root/usb_runtime/crypto_liqfwd/` (src 스냅샷·data·logs·releases), `/etc/systemd/system/usb-crypto-liqfwd.service`, `usb-crypto-liqfwd-daily.service`, `usb-crypto-liqfwd-daily.timer`.
**기존 서버 파일·유닛 수정 0.**

## 3. Service architecture

```
usb-crypto-liqfwd.service  (Restart=always, enabled, MemoryMax=100M, CPUQuota=25%, Nice=10, .env 미로드)
  python -m app.crypto.liquidation_forward.collector      PYTHONPATH=/root/usb_runtime/crypto_liqfwd/src/backend
   ├─ Bybit WS    allLiquidation.BTCUSDT  (+ op ping 20s)
   ├─ Binance WS  /market/stream btcusdt@forceOrder + btcusdt@markPrice@1s (건전성)
   ├─ OKX WS      liquidation-orders SWAP (감사용, + "ping")
   ├─ OKX REST    liquidation-orders uly=BTC-USDT 60초 폴링, 10분 겹침, 최대 30쪽 (정본)
   └─ heartbeat   60초, 세그먼트 회전·fsync, 디스크 가드(여유 < 300MB면 쓰기 중단)
usb-crypto-liqfwd-daily.timer  매일 01:15 UTC → daily finalize (parquet + manifest + heartbeat gzip), MemoryMax=250M
data: /root/usb_runtime/crypto_liqfwd/data   lock: data/runtime/collector.lock (flock, 두 번째 실행 거부)
```

`usb-crypto-paper`와 프로세스·소스 스냅샷·데이터 디렉터리·포트를 공유하지 않는다(이 수집기는 포트를 열지 않는다).

## 4. Collector start timestamp

**LIQUIDATION_FORWARD_V1_START = 2026-09-27T10:35:04.118Z = 2026-09-27 19:35:04 KST**
(collector `liqfwd.1.0`, schema `liq.v1`, 거래소 bybit·binance·okx, 소스 sha256 `c0616aea…2fe4`). 서버 `data/START_MARKER.json`.
버전 이력: 10:34:02Z `liqfwd.1.0` 기동 → 11:17:59Z `liqfwd.1.1` (OKX REST 원문 보존 규칙만 변경, 7절).

## 5. Source semantics

공식 문서와 실측으로 확인한 것(데이터 사전 1절):
- **Bybit**: 문서 원문 "When you receive a Buy update, this means that a long position has been liquidated", `p` = "Bankruptcy price", push 500ms snapshot.
- **Binance**: 청산 주문 스트림. `S`는 주문 방향(SELL = 롱 청산). 문서 페이지가 JS 렌더라 이번에 원문 인용 실패. 집계(초당 1건)는 D5.2 45분 실측으로 확인.
- **OKX**: WS 문서 원문 "**This data doesn't represent the total number of liquidations on OKX.**" `posSide` long/short/net. **REST `/api/v5/public/liquidation-orders`는 현재 공식 문서에 없다**(동작 중). 롤링 약 21시간.

## 6. Bybit result

관찰 약 64분(10:35 ~ 11:38Z): 1건 SHORT, 6,214 USD. 재연결 0, WS 오류 0. 수신 지연 167ms(1건).
전량 여부는 여전히 미검증(REST 없음).

## 7. Binance result

9건: v1.0 창 LONG 6건 11,874 USD, v1.1 창 SHORT 3건 44,804 USD. `is_aggregated = true`. 수신 지연 p50 1,022ms / p95 1,030ms
(1초 집계 창 끝에 push되는 것과 맞다). 재연결 0.

## 8. OKX REST result

- 기동 첫 폴링에서 롤링 창 469건(약 21시간) 백필. **전부 시작점 이전(`pre_session_rows`)이라 정본 아님.**
- 시작점 뒤 실시간 3건(v1.1 창, LONG 2 / SHORT 1, 4,539 USD). v1.0 창(45분)은 OKX BTC 청산 0건: 같은 시각 REST 최신 BTC 이벤트가 61분 전이었고 ETH·SOL은 5 ~ 13분 전이라 REST 장애가 아니라 **BTC가 조용했던 것**으로 확인.
- 발견 지연(수신 - 이벤트) p50 38.1초 / p95 56.8초 / 최대 58.9초 = 60초 폴링 주기.
- HTTP 오류 0, rate limit 0. 폴링 66회, 겹침 중복 약 6,000건은 전부 제거.

## 9. OKX WS comparison

v1.1 창에서 WS 3건 = REST 3건 **전부 일치**(WS 지연 p50 830ms). D5.2에서는 같은 4분 창 REST 46건 중 WS 15건(33%). 소량 구간에서는 WS가 다 잡고,
빈번한 구간에서 누락되는 것으로 보인다. WS는 구독 직후 **과거 청산 스냅샷**(43분 전 이벤트 포함)을 보내므로 지연 관측에도 그대로 쓰면 안 된다(세션 전 이벤트로 분리함).

## 10. Normalized schema

`liq.v1` 23필드(계약 3절, 사전 1절). 값이 없는 필드는 null: 3사 모두 `source_event_id` null, `mark_price`·`index_price` null, Bybit·OKX `is_partial` null, OKX ctVal 조회 실패 시 `qty`·`notional_usd` null.

## 11. Side normalization

| 거래소 | 규칙 | 이번 관찰 |
|---|---|---|
| Bybit | `S` Buy → LONG | SHORT 1 |
| Binance | 주문 SELL → LONG | LONG 6, SHORT 3 |
| OKX | `posSide`, net이면 주문 방향 | LONG 2, SHORT 1. 백필 469건 중 net 0 |
null_side 0. **LONG 의미의 가격 방향 검증은 아직 표본 부족**(계약 4절, 첫 하락 구간에서 재검증).

## 12. Dedupe

거래소별 복합키(계약 5절). 이전 메시지의 키만 버리고 한 메시지 안의 같은 키는 보존(`intra_same_key`, 이번 0). 키 48시간 보관.
정상 재시작·강제 종료 뒤 **키 469 / 483개 재적재**, 재시작 뒤 OKX 재폴링분이 새 행으로 늘지 않음(중복 폭증 0). 창 안 중복 키 0.

## 13. Raw storage

시간 세그먼트 gzip. WS 청산 메시지는 전부, OKX REST는 **새 행이 있는 페이지만**(v1.1). v1.0에서 매 폴링 페이지를 다 저장하면 하루 약 2MB 중복 원문이 쌓인다는 것을 45분 실측으로 발견하고 고쳤다.
REST 페이지 원문은 압축률이 높다(17쪽 268KB → gzip 3.5KB).

## 14. Parquet storage

일일 정리가 이벤트 UTC 날짜 기준 `normalized/exchange=<x>/date=<d>/part.parquet`(zstd)를 만든다. 서버 수동 실행 검증(2026-09-27 부분일): 3파일 6.7 / 6.9 / 17.1KB, 8.9초, 최대 RSS 116MB.
**오늘치 parquet은 내일 01:15 UTC 타이머가 하루 전체로 다시 만든다.** 수집기 상주 프로세스는 pyarrow를 올리지 않는다.

## 15. Manifest

- `manifest/segments.jsonl`: 세그먼트마다 kind, exchange, file, rows, bytes, sha256, 첫/끝 수신, recovered, torn_bytes_cut
- `manifest/daily/date=<d>.json`: 계약 7절 전 항목 + parquet sha256 + 그날 세션 시작·종료 + heartbeat 압축 정보

## 16. Restart recovery (서버 실측)

| 시험 | 결과 |
|---|---|
| 정상 재시작 (`systemctl restart`) | 종료 신호에 열린 세그먼트 봉인, 재기동 뒤 키 469 재적재, 새 행 0 |
| 강제 종료 (`kill -9`) | systemd 10초 뒤 재기동(NRestarts 1). 남은 `.partial`을 **기존 `.gz`를 덮어쓰지 않고 `-r1`로 봉인**, 잘린 바이트 0 |
| 잘린 마지막 줄 | 테스트로 검증(잘라낸 바이트 수 기록) |
| 반쯤 쓴 gzip | 기동 시 삭제(원본 .partial은 남아 있음) |
| 서버 재부팅 | 실제 재부팅은 하지 않음. 유닛이 `enabled` + `WantedBy=multi-user.target`이라 부팅 시 자동 기동 |
| 이벤트 순서 | 세그먼트 안은 수신 순, parquet은 (event_ts, receive_ts) 정렬 |

## 17. Gap detection

heartbeat 60초 + 세션 기록. 연속한 heartbeat가 **같은 세션이고 150초 이내이며 양끝 모두 UP**일 때만 UP.
재시작은 보수적으로 DOWN(오늘 manifest: 재시작 구간 124초 DOWN). 거래소별 UP은 연결 + 90초 안의 건전성 신호.
DOWN 구간은 "청산 없음"이 아니라 "관측 안 됨"으로 manifest에 남는다. v1.0 초판에는 재시작이 150초 안에 끝나면 UP으로 잡히는 결함이 있어 고쳤다(테스트 추가).

## 18. 1h observed events (10:35 ~ 11:38Z, 약 64분)

| 거래소 | 건수 | LONG | SHORT | 명목 (USD) |
|---|---|---|---|---|
| Bybit | 1 | 0 | 1 | 6,214 |
| Binance | 9 | 6 | 3 | 56,678 |
| OKX REST | 3 | 2 | 1 | 4,539 |
| OKX WS | 3 | 2 | 1 | 4,539 (REST와 동일 이벤트) |

D5.2 캡처(시간당 20 ~ 28건/거래소)보다 적다. 조용한 시간대였다.

## 19. Event / notional distribution

표본이 13건이라 분포를 말할 수 없다. 첫 일일 manifest(2026-09-28 01:15 UTC 생성, 2026-09-27 부분일)부터 거래소별 LONG/SHORT 건수·명목이 날마다 쌓인다.

## 20. CPU

수집기 누적 CPU 2.8초 / 2,447초(v1.0), 1.6초 / 1,221초(v1.1) = **약 0.1 ~ 0.13%**. 서버 load 0.07. 일일 정리 8.9초/일.

## 21. Memory

수집기 RSS 39.5MB(ps), cgroup MemoryCurrent 28 ~ 29MB, MemoryPeak 28.6MB (한도 100M). 일일 정리 최대 116MB(한도 250M, 하루 1회 수 초).
서버 가용 메모리 362 ~ 410MB 범위에서 변화 없음.

## 22. Disk / day

| 구성 | 하루 (추정) | 근거 |
|---|---|---|
| raw + norm (gzip) | 0.3 ~ 0.6MB | 거래소당 시간 20 ~ 30건 가정(D5.2 캡처), 행당 수백 바이트 |
| parquet | 0.1MB 미만 | 373행 17KB |
| heartbeat | **0.12MB** 보관(gzip, 17배) / 당일 평문 최대 2.0MB | 1,389바이트 x 1,440줄 |
| segments manifest | 0.03MB | 세그먼트당 약 300바이트 |
| **합계** | **약 0.5 ~ 1MB/일** | |

네트워크 수신(추정, 직접 측정 안 함): Binance markPrice 1초 스트림 약 20MB/일 + OKX REST 60초 폴링 최대 약 23MB/일 + 기타 수 MB = **약 45MB/일 이하**.

## 23. Projected disk / year

**약 0.2 ~ 0.4GB/년.** 서버 루트는 91% 사용, 여유 약 930MB. 1년치는 들어가지만 다른 서비스 증가분(Paper 테이프 등)과 합치면 여유가 빠듯하다.
디스크 가드(300MB)로 이 수집기가 Paper를 멈추게 하지는 않지만, **수집기가 스스로 멈출 수 있다.** 저널 474MB 정리나 디스크 증설은 사용자 결정 사항이다.

## 24. Paper service impact

| 항목 | 배포 전 (10:33Z) | 관찰 끝 (11:38Z) |
|---|---|---|
| usb-crypto-paper | active, PID 3194236, NRestarts 0 | active, **PID 3194236**, NRestarts 0 |
| paper ledger | 182줄 | 182줄 (사용자 거래 없음, 수집기는 접근 안 함) |
| crypto-api `/api/crypto/state` | - | 200 |
| usb-frontend / usb-backend / usb-e-rvol / nginx | PID 3196749 / 3187521 / 2515148 / 707 | 동일, NRestarts 0 |
| jptcalc (Host 헤더 로컬 요청) | 301 | 301 (동일) |

Paper 재시작 0, frontend 재빌드 0.

## 25. Private / API key usage

**0.** 코드에 키·서명·private·주문 엔드포인트 문자열이 없음을 테스트로 고정(`test_no_private_endpoints_or_keys`). 유닛은 `/root/usb/.env`를 읽지 않는다. 요청은 공개 WS 3개와 공개 REST 2개(liquidation-orders, instruments)뿐.

## 26. Forward observation gate

**하락·LONG 청산 이벤트 30회**(2026-10-02 개정으로 6개월 조건 삭제, 계약 9절). 이벤트 정의는 데이터를 보기 전에 새 계약에서 동결. DOWN 구간과 겹친 이벤트는 카운트 제외(Plan V2 3절).

## 27. C1 hypothesis registration

등록만 함(계약 10절, Plan V2 4절): "C1 순간에 3사 LONG 청산이 실제로 늘어나는가." 판정·거래 금지.

## 28. D6 status

**D6 = NOT AUTHORIZED.** AUTO 코드·UI 0. 향후 AUTO는 MANUAL과 계좌·원장·포지션·리스크·분석 분리(계약 11절).

## 29. Tests

`backend/tests/crypto/test_liquidation_forward.py` **16 passed**: Bybit(parse·side·duplicate·reconnect 재적재), Binance(parse·side·aggregated·partial),
OKX(REST·WS parse, net side, 모르는 side null, ctVal 없으면 qty null, REST 페이징·429 재시도·겹침 중복, REST 원문은 새 행 있는 페이지만), 공통(schema·timestamp·시간 파티션·
manifest·sha256·partial 봉인·잘린 줄·덮어쓰기 금지·gap 판정·재시작 DOWN·세션별 카운터·heartbeat 압축·단일 인스턴스 락·private 엔드포인트 없음·paper/terminal 비의존).
crypto 전체 **421 passed**.

## 30. Risks / UNKNOWN

| 항목 | 내용 |
|---|---|
| OKX REST 비공식화 | 공식 문서에 없는 엔드포인트. 사라지면 OKX 정본이 끊긴다(HTTP 오류·DOWN으로 기록됨) |
| Bybit 완전성 | REST가 없어 전량 대조 불가 |
| Binance 집계 | 초당 1건 상한. 건수로 강도를 재면 안 됨 |
| LONG side 검증 | 하락 구간 실측 부족 |
| 디스크 | 루트 91%. 수집기가 디스크 가드로 멈출 수 있음 |
| 지연 해석 | 서버·거래소 시계 차이가 섞임. 음수 지연 0건이었지만 시계 동기 상태를 따로 확인하지 않음 |
| 재부팅 | 실제 재부팅 시험 안 함 |
| 네트워크량 | 추정치 |
| 관찰 시간 | 64분, 조용한 시장. 하루 이상 운용 뒤 첫 일일 manifest로 다시 확인 필요 |

## 31. Verdict

**PASS.**
- 3사 수집 정상(Bybit·Binance WS, OKX REST 정본 + WS 감사), 64분 동안 재연결·HTTP 오류·rate limit 0
- 품질·공백 추적 정상(heartbeat, 세션, 거래소별 UP/DOWN, 재시작 DOWN, 백필 분리)
- Paper와 격리(프로세스·소스·데이터 분리, Paper PID·원장 불변)
- forward 시작점 동결: 2026-09-27T10:35:04.118Z

조건: 첫 일일 manifest(2026-09-28 01:15 UTC)와 첫 하락 구간의 side 재검증을 다음 점검에서 확인한다.

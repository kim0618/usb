# C-M Data Feasibility V1 (C-P1)

작성 2026-09-17. 코드 변경 전 읽기 전용 확인과, C 수집 캐시(`data/runtime/strategy_c/raw`, git-ignored)의 실측.
공급자: Massive Stocks Basic(무료 키, 분당 5콜, 이력 롤링 2년, 당일 T 미제공).

## 1. 요약 표

| Data | Required | Available | PIT | Historical Depth | Cost/API | Include V1 |
| --- | --- | --- | --- | --- | --- | --- |
| Daily OHLCV 전 종목 (grouped daily, `adjusted=false`) | 필수 | PASS: 세션당 1콜, 약 10,600행(ETF, 워런트 포함), OHLC 불일치 0 (§2) | PASS: raw 가격, 상폐 종목 포함(과거 조회에 7/7 포함 실측 이력), 날짜 재현 가능 | 2024-09-17..2026-09-16, 501세션. 2024-09-16은 403 | 501콜, 13초 간격 약 110분 | INCLUDE |
| CS reference (tickers `type=CS`, `date=`) | 필수(ETF/OTC 제외) | PASS: 스냅샷당 6페이지, 5,133~5,305종목 | PARTIAL: `active` as-of 동작, `type`/거래소는 ASSUMED_STATIC, `delisted_utc`는 as-of 결과에 비어 있음 | 분기 첫 세션 8개(2024-10-01..2026-07-01) | 약 55콜 | INCLUDE (분기 스냅샷) |
| Splits 전 시장 (`/v3/reference/splits`, 티커 없이) | 필수 | PASS: 4페이지 3,328건(역분할 2,381), 필드 `execution_date, split_from, split_to, ticker, id` | PARTIAL: 발표일 없음, 실행일만. 실행일 `<= D`만 feature에 사용 | 2024-09-16..2026-09-16 | 4콜 | INCLUDE |
| Reverse split | 필수 | PASS (위 레코드의 `split_to < split_from`) | 위와 같음 | 위와 같음 | 0 | INCLUDE (CA 제외 규칙) |
| Ticker change (`/vX/reference/tickers/{t}/events`) | 권장 | PARTIAL: 200 확인(2026-09-17 감사), 종목별 호출 | PASS 가능 | - | 종목당 1콜, 5,000종목 = 약 17시간 | DEFER (새 티커는 이력 부족으로 자동 비적격, 옛 티커 label은 DISAPPEARED 처리) |
| SPY 일봉 | 필수 | PASS: grouped daily에 포함, 추가 콜 0 | PASS | grouped와 동일 | 0 | INCLUDE |
| Derived features (return, RVOL, z, $vol, ATR%, RS, 52W) | 필수 | PASS: 일봉만으로 계산 | PASS (`C_PIT_CONTRACT_V1.md`) | 52W는 252세션 warmup | 0 | INCLUDE |
| Market cap (ticker details `date=`) | 선택 | PARTIAL: 200, 그날 종가 x 주식수 | 불확실(주식수 갱신 시점) | - | 종목x일 호출, 비현실 | EXCLUDE |
| Sector / SIC (ticker details) | M3 | PARTIAL: SIC 있음 | 날짜별 변경 PIT 미검증 | - | 종목당 1콜 이상 | DEFER |
| Peer (`/v1/related-companies`) | M3 | NOT VERIFIED (429) | 알 수 없음 | - | 종목당 1콜 | DEFER |
| News / Financials | C-E | 200 (2026-09-17 감사) | news published_utc 과거 조회 가능, financials acceptance_datetime 있음 | Basic 2년 | 종목당 다콜 | EXCLUDE (C-E 범위) |
| Earnings/consensus (Benzinga) | C-E | FAIL 403 | - | - | 추가 공급자 필요 | EXCLUDE |

### V1 INCLUDE

grouped daily OHLCV(`adjusted=false`), 분기 CS reference 스냅샷, 전 시장 split 기록(역분할 포함), SPY(grouped 내),
일봉 파생 feature 전부(return 1/3/5, RVOL20, volume z20, dollar volume, ADV20, dollar volume change, 신호 전 ATR%,
RS vs SPY 1/3/5, 52W high distance).

### V1 DEFER

ticker change 연결(events), Sector/SIC, Peer(related-companies), C-M3/C-M4.

### V1 EXCLUDE

Market cap, News, Financials, Earnings/consensus, Benzinga, AI insights(news `insights`는 AI 파생이라 금지).

## 2. Daily OHLCV 실측

- `adjusted=false`를 요청하고 응답 `adjusted: false` echo를 확인, 아니면 수집 중단(`raw_fetch.fetch_grouped`).
- OTC: grouped daily 기본 `include_otc=false`. CS 스냅샷은 `market=stocks`, 거래소 분포 XNAS 3,317 / XNYS 1,754 / XASE 233 / BATS 1 (2026-07-01).
- 2024-10-02 세션: grouped 10,588행, 그중 2024-10-01 CS 스냅샷 5,194종목 중 5,067개 존재(127개는 그날 무거래 또는 티커 불일치).
- 2024-09-16: HTTP 403, 코드 `NOT_AUTHORIZED`(메시지가 plan timeframe 패턴과 다름). 롤링 2년 창 밖으로 판단. 창 시작 = 2024-09-17.
- 롤링 창이라 **캐시가 곧 정본**이다. 내일이면 2024-09-17도 API로 다시 받을 수 없다. raw 파일 sha256 digest가 run identity에 들어간다.
- 25세션(2025-08-08..2025-09-12)은 universe V2 선정 때 Drive에 저장된 같은 endpoint 원본(`adjusted=false` 확인)을 복사해 콜을 아꼈다(`seeded_from` 필드).
- 전 구간 실측(run `cmsel1-855b6a0c…`, 2026-09-18): 501세션, 패널 티커 6,341개(스냅샷 합집합 + SPY), 세션당 봉 5,053~5,242(중앙값 5,144),
  **OHLC 불일치 0행**, 0 이하 거래량 2행, SPY 501/501 세션 존재, 내부 결측 세션 0.

## 3. Corporate actions

- split 레코드 중 panel(CS 스냅샷) 티커에 걸리는 것만 사용. 같은 티커 같은 실행일 중복 12건은 첫 레코드만 사용.
- 역분할 착시 방어는 세 겹이다. (1) `F(t)` PIT 조정, (2) (D-20, D] 분할 ticker-date를 후보와 대조군 모두에서 제외, (3) 분할조정 후에도 종가비 3배 이상/1/3 이하가 남으면 `ca_suspect`로 양쪽 제외.
- 미래 split을 과거 feature에 쓰지 않는 것은 mutation test 2와 실데이터 `future_splits` 감사로 확인.

## 4. Universe 규모

hard filter는 `RESEARCH_BASELINE_NOT_OPTIMIZED`: close >= $3, ADV20 >= $5M.
실측(primary 240일): base 적격 ticker-date 635,890행(하루 평균 2,650개), M2 적격 610,237행. CS 스냅샷은 5,133~5,305종목.
split 기록 3,316건 중 패널 티커에 걸리는 것 1,214건, 역분할 2,380건(전체의 72%).

## 5. Historical depth

| 항목 | 값 |
| --- | --- |
| 세션 grid | 2024-09-17..2026-09-16, **501세션** (idx 0..500, 실측 확정) |
| forward label 제외 | 마지막 10세션 (idx 491..500) |
| M0/M1 warmup | 60세션 seasoning + 20세션 연속 봉 -> 첫 신호 idx 60 = 2024-12-11 |
| M0/M1 secondary 신호일 | 2024-12-11..2026-09-01, **431일** |
| M2 warmup | 252세션(첫 봉 idx 0 이하, 240봉 이상) -> 첫 신호 idx 251 = 2025-09-18 |
| Primary 공통 신호일 (M0/M1/M2) | 2025-09-18..2026-09-01, **240일** |
| Time block (primary) | 2025-09-18..12-11 / 12-12..2026-03-11 / 03-12..06-05 / 06-08..09-01, 각 60일 |

제약: primary 창은 약 1년 단일 regime이다. Basic 롤링 2년에서는 52W feature를 쓰는 한 이 이상 늘릴 수 없다
(GATE-D 데이터 깊이 결정 사항).

## 6. Rate limit과 충돌 관리

- 같은 키를 쓰는 다른 로컬 프로세스와 한도를 공유한다. 이번 세션 실측:
  - 15:11~15:41 KST 다른 세션 `cs_universe_fetch.py`(grouped 100+콜) 실행 중 -> C 수집은 종료를 기다린 뒤 시작.
  - 15:41 KST부터 다른 세션 `build_research_universe_v2 --fetch --collect`(분봉 수집)가 겹쳐 C 수집 62콜 중 429 13회(60초 backoff).
    -> C 수집을 중단(캐시 38세션 보존)하고 그 프로세스 종료 후 13초 간격으로 재개.
  - 17:59:30 KST 사용자 퇴근에 맞춰 중단(346/502), 2026-09-18 09:00 재개해 10:00 이전 완료. 마지막 구간 162콜 중 429 6회(전부 재시도 성공, 데이터 손실 0).
  - 총 API 사용: grouped 501콜(그중 25세션은 Drive 원본 재사용), CS 스냅샷 8개 약 55콜, splits 4콜.
- 계획: (1) 호출 전 `ps`로 Massive 수집 프로세스 확인, (2) 요청 직렬화(단일 프로세스, 스레드 없음), (3) 13초 간격 limiter(12초는 5/분 경계라 429 발생),
  (4) 429는 클라이언트가 retry-after 또는 60초 대기 후 최대 2회 재시도, 그래도 실패하면 120초 쿨다운 후 최대 5회,
  (5) 요청 단위 파일 캐시라 중단해도 재개 비용 0, (6) 403 timeframe/권한은 에러 파일로 기록하고 grid 앞에서만 허용(중간 결측은 실행 거부).

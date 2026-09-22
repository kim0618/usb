# Historical PIT Universe U1 - Build V1 (2026-09-21)

판정: **BLOCKED**. 규칙 `ru2-pit-adv-sector-cap-dataeligible-v2`를 `USB-HIST-V1` 스냅샷에 그대로
적용하면 U1 구간에서 데이터가 완전한 후보가 **21종목**뿐이다. 규칙의 `MIN_SIZE`는 25이므로 U1은
동결하지 않았다. 규칙은 하나도 바꾸지 않았고, 30을 채우려고 완화한 것도 없다.

새 백테스트 실행 0건. 성능 아티팩트(summary, trades, ledger, report)는 열지 않았다.
이번 단계의 Massive 호출 0건(대량 수집은 자동 실행 금지, 섹션 N 참조).

## 1. 날짜 (달력에서 유도, 손으로 고르지 않음)

| 항목 | 값 | 유도 |
| --- | --- | --- |
| snapshot start | 2024-09-17 | `USB-HIST-V1` grouped 첫 세션 (2024-09-16은 403 stub) |
| reference_session | 2024-10-21 | 2024-09-17부터 `HISTORY_SESSIONS=25`번째 세션 |
| selection_as_of | 2024-10-22 | reference_session의 다음 세션 (개장 전 선언) |
| **U1 first entry** | **2024-10-23** | 스캐너 warmup 25세션이 2024-10-22에 끝나므로 첫 진입은 그 다음 세션 |
| U1 last entry | 2025-09-12 | U2 첫 진입 2025-09-15의 직전 세션 |
| U1 entry sessions | 222 | |

지시서 B의 U1 진입 시작 `2024-10-22`는 달력 검증으로 한 세션 뒤인 **2024-10-23**이 된다.
`plan_baseline_range`가 같은 이유로 같은 날짜를 내놓는다(`daily scan coverage starts 2024-10-22`).
selection_as_of는 2024-10-22 그대로다. 유니버스 선언일과 첫 진입일은 같을 필요가 없고, V2도
선언 2025-09-15에 첫 진입은 2025-10-13이었다.

U1이 각 후보에게 요구하는 창(`u1_ranges`, 달력만으로 계산):

| 창 | 범위 | 세션 |
| --- | --- | --- |
| daily scan (warmup 포함) | 2024-09-17..2025-09-11 | 247 |
| minute (premarket 20 + settlement 1) | 2024-09-25..2025-09-15 | 243 |

## 2. 왜 BLOCKED인가 (실측)

`USB-HIST-V1`의 분봉은 30종목뿐이고, 그중 U1 창 전체가 A STRICT로 깨끗한 종목은 21개다.

**적격 21종목** (유동성 순위 순): AAPL, AMD, AMZN, AVGO, BAC, CMCSA, GOOGL, INTC, MSFT, MSTR,
MU, NKE, NVDA, PFE, RIVN, RKLB, SOFI, T, TSLA, UBER, WMT

**탈락 9종목** (strict 계약 그대로, 채우지 않음):

| 종목 | 실패 단계 | 사유 |
| --- | --- | --- |
| UNH | minute | 63/243 세션 REGULAR_INCOMPLETE |
| SPY | minute | 8세션 OUTSIDE_EXTENDED_HOURS_ROWS (벤치마크라 무관, 4절) |
| ORCL | minute | 6세션 REGULAR_INCOMPLETE |
| JPM | minute | 2세션 REGULAR_INCOMPLETE |
| META, PLTR, HIMS, HOOD | minute | 각 1세션 REGULAR_INCOMPLETE |
| CRWV | daily | 132/247 세션 없음 (2025-03 상장) |

이 9종목의 결손은 제공자 쪽 사실이고 재수집으로 메워지지 않는다(감사가 이미 그렇게 기록한다).
CRWV는 U1 구간에 상장 전이라 PIT상 후보가 될 수 없다.

21 < 25이므로 details walk(최대 160콜)는 실행하지 않았다. 시총·SIC를 다 받아와도 분봉이
없는 종목은 minute 단계에서 전부 탈락하므로, 그 호출은 판정을 바꾸지 못한다(지시서 D의
"details는 필요할 때만").

## 3. 해제 조건: 분봉 수집 (시한부)

상위 160위 후보 중 스냅샷에 분봉이 있는 종목은 24개, 없는 종목이 **136개**다.

| 항목 | 수량 | 시간(13초 간격) |
| --- | --- | --- |
| 분봉 요청 (136종목 x 243세션 / chunk 50) | 680 | 2.46 h |
| 종목별 일봉 요청 | 136 | 0.49 h |
| **합계** | **816** | **약 2.95 h** |
| details walk (해제 후) | ≤160 | 약 0.58 h |

**시한**: Massive Basic의 롤링 창 하한이 2026-09-21 기준 `2024-09-20`이고, 매 미국 영업일마다
한 세션씩 올라온다. U1이 요구하는 가장 이른 세션은 **2024-09-25**이므로 남은 여유는 **3세션**이다.
`raw_fetch.ordered()`가 오래된 요청부터 보내므로, **첫 청크 136건(약 30분)**만 먼저 돌리면
만료 임박 구간은 전부 확보된다. 나머지 청크는 수개월간 만료되지 않는다.

참조 엔드포인트(reference tickers, ticker details)는 롤링 창의 제약을 받지 않으므로 급하지 않다.

**충돌**: 지금 B 분봉 수집기(pid 8236, `--b-minute-from 2026-05-18`)가 `collector.lock`을 잡고
돌고 있다(1,307건 남음). B가 받는 구간은 2026-04-20 이후라 만료되지 않는다. 수집기는 한 번에
하나만 돌 수 있으므로, U1 분봉을 받으려면 B를 SIGINT로 멈춰야 한다(같은 명령 재실행으로 이어받음).
어느 쪽을 먼저 돌릴지는 사용자 결정이다.

**저장 위치**: 새 분봉은 Common Historical Store 계약대로 Common Raw
(`market_data/raw/massive/minute/<SYM>/`)에 들어간다. 전략 전용 저장소는 만들지 않는다.
Drive 여유는 6.9 GB, 추정 소요는 약 0.5 GB다.

**그 다음**: `USB-HIST-V1`은 FROZEN이고 멤버 목록이 고정이므로, 새로 받은 분봉은 V1 스냅샷이
보지 못한다. U1 선택에 쓰려면 새 스냅샷 id(세션 감사 재계산 포함)를 동결해야 한다. V1은 건드리지 않는다.

## 4. 벤치마크 (SPY)

A는 SPY를 **일봉으로만** 읽는다. 근거: `baseline/runner.py`의 `required`가 `universe.symbols`에서만
나오고 벤치마크는 거기 없으며, `coverage_preflight`는 `required`만 받는다. 스캐너의 상대강도도
`scanner/metrics.py`에서 `benchmark_bars: Sequence[DailyBar]`를 받는다. 따라서 SPY의 분봉
장외 행 8세션은 U1 후보 적격성과 무관하고, 후보용 strict 분봉 규칙을 벤치마크에 적용하지 않는다.
SPY 일봉은 스냅샷에 U1 구간 전체가 있다.

## 5. PIT 시장 선택 vs 오프라인 데이터 필터 (분리)

| 층 | 내용 | 정보 시점 |
| --- | --- | --- |
| PIT 시장 선택 | reference(CS/거래소/active), 25세션 history, 종가 $10, ADV20 순위, 시총, CIK 중복, sector cap 5 | 전부 2024-10-21 이전 정보 |
| 오프라인 데이터 필터 | metadata → daily → minute 완전성 | **U1 구간 전체**를 본다 |

두 번째 층은 시장 정보가 아니라 연구 데이터 가용성 필터다. 이 필터는 2024-10-22 시점에 알 수
없는 사실(그 뒤 1년간 제공자가 모든 분봉을 남겼는가)을 쓰므로 순수 PIT 종목 선택이 아니다.
V2가 같은 편향을 갖고 그 한계를 명시한 것과 같은 성격이며, U1도 동일하게 기재한다.
이것을 근거로 계약을 완화하지는 않는다: 불완전한 가격 경로를 재생하면 체결과 청산이 달라지고,
그 차이를 나중에 전략 효과와 분리할 수 없다.

## 6. 두 기간 authority 모델 (설계, 구현 없음)

| | U1 | U2 (= research-universe-v2, 불변) |
| --- | --- | --- |
| rule_version | `ru2-pit-adv-sector-cap-dataeligible-v2` | 동일 |
| reference_session | 2024-10-21 | 2025-09-12 |
| selection_as_of | 2024-10-22 | 2025-09-15 |
| 시총 authority | DATED_REFERENCE 2024-10-21, ASSUMED_STATIC | DATED_REFERENCE 2025-09-12, ASSUMED_STATIC |
| 진입 세션 | 2024-10-23 .. 2025-09-12 | 2025-09-15 .. |
| 분봉 tape | 새 스냅샷 (U1 구간) | `USB-HIST-V1` A STRICT |

- **metadata switch**: 2025-09-12 종가 이후. 스캔은 전 세션 종가 뒤에 돌므로 경계 스캔부터 U2 메타데이터를 쓴다.
- **universe switch**: 진입 세션 2025-09-15 개장 전.
- **warmup**: U2 종목은 `USB-HIST-V1`에 2025-09-15 이전 분봉이 있으므로 경계에서 바로 진입 가능하다.
  단 게이트 이력 20세션 안에 strict 제외 세션이 있는 종목(예: META 2025-09-12)은 그 세션이 창에서
  빠질 때까지 DATA_GAP으로 거절한다. 없는 봉을 만들지 않는다(섹션 L).
- **경계 통과 포지션**: U1에서 연 포지션은 기존 포지션 계약 그대로 계속 관리한다. 유니버스
  스냅샷이 바뀌었다는 이유만으로 강제 청산하지 않는다. 신규 진입만 그날의 유니버스를 따른다.
  경계일 스캔 대상은 U2이며, U1에만 있는 종목은 신규 스캔에서 빠지되 보유 포지션은 남는다.
- 자본과 원장은 한 계정으로 연속이다.

## 7. 2년 run identity 요구사항 (설계)

기존 단일 유니버스 run의 run_id는 바뀌지 않아야 한다(ManifestBinding에서 이미 검증한 원칙).
분절 run은 identity 줄에 다음을 추가한다:

```
historical_snapshot / historical_snapshot_sha256 / historical_view
u1_universe_checksum / u1_metadata_checksum
u2_universe_checksum / u2_metadata_checksum
segment_boundary_scan=2025-09-12 / segment_boundary_entry=2025-09-15
selection_method_version / loader_version
```

## 8. 코드 (미커밋)

| 파일 | 내용 |
| --- | --- |
| `backend/app/backtest/research/universe_selection.py` | `rank_by_liquidity`, `rule_block`, `rule_block_v2`에 `reference_session` / `selection_as_of` 인자 추가. 기본값은 V2 날짜라 V2 출력 바이트 불변(`rule_block_v2()` sha256 `b0c0a40f…` 유지, 79 tests green) |
| `backend/app/backtest/research/u1_eligibility.py` | `u1_ranges`, `SnapshotEligibilityChecker` (스냅샷 감사 기반 metadata→daily→minute), 수집 없음, 저장 결함은 `DataEligibilityUnresolved` |
| `backend/tests/test_u1_eligibility.py` | 11 tests |

## 9. 한계

- U1 선택은 아직 없다. 위 21종목은 "스냅샷이 현재 담고 있는 것"이지 규칙이 고른 유니버스가 아니다.
  시총·SIC를 읽지 않았으므로 pool 순위, sector cap, issuer dedupe는 적용 전이다.
- 시총 STATIC 가정은 U1 구간 약 10.5개월에 적용된다(V2와 같은 성격, 더 긴 구간).
- 발행주식수 공시 지연은 확인 불가(UNVERIFIABLE).
- `active`는 Production 상수이며 dated 상장 상태가 아니다.
- 2024-09-17 이전 데이터는 Basic 플랜에 없으므로 U1을 더 앞으로 옮길 수 없다.

## 10. 추가 기록: 긴급 첫 청크 수집 (2026-09-21 10:36~11:07 KST)

롤링 창 소멸을 막기 위해 후보 136종목의 **가장 오래된 청크 1개씩만** 받았다. U1 전체 수집이 아니다.

- B 분봉 수집기(pid 8236)를 SIGINT로 중단 -> 프로세스 종료, 자식 없음, Drive writer lock 해제,
  `collector.lock` 해제, `.partial` 0, 중단 지점은 rate limiter sleep이라 진행 중 요청 없음.
- `app.dev.collect_u1_minute --fetch --max-requests 136`: 136/136 성공, 실패 0, 재시도 0,
  rows 2,896,629, 58.4 MB. 모든 요청이 `2024-09-25..2024-12-04` 50/50 세션.
- 검증: 136개 전부 COMPLETE 원장, `coverage_status=COMPLETE`(클리핑 0), 페이지 sha256 일치,
  `adjusted=false` 확인, `.partial` 0.
- B 재개(pid 61339): planned가 1,334에서 **1,073**으로 줄어 이어받기 확인, 기존 COMPLETE 원장
  2,682건 전부 보존.
- `USB-HIST-V1` snapshot.json sha256 `e2a8e5d6…` 불변, 포인터 FROZEN 그대로.

남은 U1 분봉은 544요청(청크 2~5)이고, 다음 만료 대상은 청크 2의 시작 **2024-12-05**이라
**53세션의 여유**가 있다. 새 분봉은 Common Raw에 있지만 V1 스냅샷 멤버 목록에는 없으므로,
U1 선택에 쓰려면 별도 freeze stage에서 새 스냅샷을 만들어야 한다.

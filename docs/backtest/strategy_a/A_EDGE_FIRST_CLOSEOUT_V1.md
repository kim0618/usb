# Strategy A Edge-First Census Closeout (F1 / F2 / F3)

작성 2026-10-19. 이 문서는 새 판정을 내리지 않는다. 이미 사전등록 규칙으로 나온 판정을 한곳에 모으고, 종결 범위와 재개 조건을 고정한다.
사전등록 문서, 러너, 결과 artifact는 수정하지 않는다.

```text
STRATEGY A EDGE-FIRST CENSUS FINAL STATUS

EDGE-FIRST MENU (F1/F2/F3)   CLOSED
F1  거래량 동반 급등          INSUFFICIENT      (참고: gross부터 음수)
F2  장후 실적 8-K 2.02        DATA_LIMITED / PIT_UNSAFE
F3  52주 신고가 돌파          INSUFFICIENT      (참고: gross 약양수, fee-only 약 0, net 음수)
F4+ / 새 family              NOT AUTHORIZED

STRATEGY A                   CLOSED / ARCHIVED (2026-10-19 사용자 결정)
A 운영                       DISABLED (2026-10-19 14:04 KST)
A Paper                      STOPPED (신규 진입·평가 없음, 과거 기록 보존)
```

---

## A. 종결 범위

- 닫는 것: 이번 Edge-First 후보 family 탐색(F1/F2/F3)과, 그 결과를 근거로 한 threshold 변경·새 family 추가.
- 2026-10-19 사용자 최종 결정으로 Strategy A 전체를 CLOSED / ARCHIVED로 두고, 운영에서도 내린다(§K). A 신규 연구는 STOP이다.
- 코드와 연구 결과는 Git, 이 문서, 로컬 아카이브에 보존한다. 재개는 §H 조건과 사용자 명시 승인이 모두 있어야 한다.

### A 연구 전체의 최종 상태

| 연구선 | 상태 |
| --- | --- |
| Legacy A (quant_v0 / TOP8) | edge 없음 |
| Mover same-day | tradeability는 복구됐지만 net 음수 |
| Mover D+1 | 구조적으로 부적합(gap이 D에 소진) |
| Carry / Swing | FAIL, 기대값 음수 |
| Edge-First F1 / F2 / F3 | INSUFFICIENT / DATA_LIMITED·PIT_UNSAFE / INSUFFICIENT, 메뉴 CLOSED |

## B. 연구 질문

기존 Strategy A의 premarket / OR15 / entry / risk / execution / exit 규칙을 전혀 바꾸지 않고 candidate universe만 F1 또는 F3로 바꾸면, gross edge가 있고 그 edge가 execution cost(왕복 50bp)를 이기는가. F2는 같은 질문을 장후 실적 공시 후보로 묻는다.

## C. 동결 계약

| 항목 | 값 |
| --- | --- |
| C0/C1 사전등록 | `research_reports/a_edge_first_census_v1/PREREGISTRATION_C0_C1.md`, sha256 `3baea825fffcecbb3bdfe6fd59102d9cda680dd2d02791cbed9bc7c918063162` |
| C1 V2 사전등록 | `PREREGISTRATION_C1_V2.md`, sha256 `4b88cd17c1b30f61b83756068923ce02a061fe16f2dc3d716ee2a54e500c4c42` |
| family 메뉴 | F1/F2/F3에서 CLOSED (결과 열람 전 동결) |
| COMMON | close ≥ $5, 시총 ≥ $300M (PIT 발행주식수 × D 종가) |
| F1 | D 종가 수익률 ≥ +5%, D 거래량 ≥ 2.0 × 직전 20세션 평균 |
| F2 | 8-K Item 2.02, 접수 D 16:00 ET 이후 ~ D+1 09:15 ET 이전 |
| F3 | D 종가 ≥ 직전 252세션 최고가 |
| 날짜 계약 | 후보 D, 진입 E = 다음 XNYS 세션 (Original-A형 D→D+1, 현재 same-day Mover 계약과 별개) |
| C1 실행 | 운영 A 코드 그대로(운영 서버 `8f19e63`과 strategy/risk/execution 파일 바이트 동일), 후보별 독립 계좌, ranking·capacity 제한 없음 |
| C1 데이터 창 | Drive market-wide 분봉 실측 104세션, 2026-04-20 ~ 2026-09-16 |
| 비용 층 | GROSS / FEE_ONLY 10bp/leg / EXECUTION_V0_NET 25bp/leg |
| 표본 | 50건 미만 INSUFFICIENT(FAIL 아님), 50 EVALUABLE, 100 STRONG |

## D. 결과

### C0 (구조 census, 2024-09-16 ~ 2026-10-09)

| family | C0 판정 | 후보 | 고유 심볼 | top5 점유 |
| --- | --- | --- | --- | --- |
| F1 | C0_PASS | 11,596 | 2,654 | 1.1% |
| F2 | C0_DATA_LIMITED (SEC coverage 36.3% < 50%) | 6,181 | 1,020 | 1.5% |
| F3 | C0_PASS (YEAR_B만, 52주 룩백) | 15,869 | 1,909 | 1.2% |

### C1 V2 (Exact-A)

| | F1 | F3 |
| --- | --- | --- |
| eligible 후보 | 2,258 | 6,131 |
| 장전 게이트 통과 | 111 | 82 |
| 진입 신호 | 33 | 23 |
| PRICE_ABOVE_LIMIT 거부 | 17 | 12 |
| 거래 | **16** | **11** |
| 세션당 거래 | 0.16 | 0.11 |
| Gross Total R (참고) | −1.70 | +0.49 |
| Fee-only Total R (참고) | −2.33 | −0.02 |
| Net Total R / PF (참고) | −3.28 / 0.46 | −0.80 / 0.77 |
| 판정 | INSUFFICIENT | INSUFFICIENT |

유효성: PIT 위반 0, invariant 0, 재생 오류 0, 엔진 net과 회계 일치, 회전액 대비 비용 25.0bp. 분봉 누락으로 빠진 후보는 2~3%뿐이다.

### F2 기존 자산 복구 감사

- 네트워크 0으로 기존 SEC 자산(H d1_1, Strategy C-E0, H auto 로컬, H d7 운영)을 합치면 coverage는 98.15%까지 오른다(엄격 규칙 240,617 / 245,149). coverage 문제는 해소된다.
- 그러나 SEC submissions JSON의 `acceptanceDateTime`이 수집본마다 같은 공시에 대해 정확히 ±4h/±5h(ET 오프셋) 다르다. 8-K 2.02 31,532건 중 8.99%가 원천끼리 충돌하고, 3.68%는 어느 값을 믿느냐에 따라 "장중이라 제외"와 "창 안이라 포함"이 뒤바뀐다. 충돌 없는 건도 약 3.9%가 의심 시간대에 있다.
- 어느 원천도 PIT-clean이 아니다(C0 BASE 포함). 이 값으로 backtest하면 look-ahead가 들어갈 수 있다. 연구용 authority로는 coverage 0%와 같다.
- Item 2.02 파서는 PASS(창 안 items 100% 정형, EDGAR 헤더 40/40 일치).

## E. 종결 이유

1. 사전등록 메뉴(F1/F2/F3) 안에서 판정 가능한 family가 하나도 없다. F1·F3는 표본 미달이고, F2는 PIT가 안전하지 않다.
2. 참고 성과도 방향이 좋지 않다. F1은 비용 전 gross부터 음수다. F3는 gross가 약간 양수지만 수수료만 빼도 0이고, 실행 비용을 넣으면 음수다.
3. 더 근본적인 원인은 Exact-A 퍼널이다. 수천 개 후보가 들어와도 E gap 게이트(F1 71%, F3 65% 탈락)와 장전 게이트를 거치면 세션당 약 1건만 남는다. 신호가 나와도 절반가량이 PRICE_ABOVE_LIMIT에 걸린다. 표본 부족은 데이터 누락 탓이 아니라 실행 퍼널이 만든 결과다. 그래서 "좋은 후보 family 하나만 찾으면 된다"는 가설 자체가 약하다.
4. 이번에 처음으로 메뉴를 닫고 사전등록해서 연구했다. 여기서 F4/F5를 추가하면 이 설계의 의미가 사라진다.

## F. 금지 (재개 조건 충족 전)

- 새 family(F4 이상) 추가, 조합 추가
- F1/F3 threshold 변경: +5% → +4%, 2x → 1.5x, 52주 → 26주, $5 → $3, $300M → $100M 등
- gap / volume / OR / PRICE_ABOVE_LIMIT / execution 튜닝을 이 연구선의 연장으로 하는 것
- 운영 A Paper 결과로 이 연구의 판정을 바꾸거나 A를 다시 여는 것
- A Paper 재시작, `A_PAPER_RUNTIME_ENABLED` / A 타이머 재활성화
- SEC acceptanceDateTime을 PIT authority로 쓰는 새 연구

## G. 운영 상태

- A 운영은 DISABLED, A Paper는 STOPPED다(§K). A Paper를 다시 켜는 것도 재개 조건(§H)에 해당한다.
- 과거 A Paper 기록은 연구 증거로 보존하며, 이 연구의 판정을 바꾸는 데 쓰지 않는다.

## H. 재개 조건 (사용자가 명시적으로 승인할 때만)

- 외부 데이터 authority: 유료 장기 market-wide 분봉(현재 비율이면 2년에 F1 약 78건으로 평가 가능, F3는 52주 룩백 때문에 약 28건으로 여전히 부족), 또는 PIT-clean한 SEC 접수시각(EDGAR 헤더 등 신규 수집).
- 또는 후보 family가 아니라 A 실행 퍼널 자체(gap 게이트, 체결 상한)를 다루는 별도 execution 연구. 이건 새 사전등록이 필요하고, 이번 연구선의 연장이 아니다.

## I. 별도 위험 (이 closeout 범위 밖)

Strategy H도 SEC `acceptanceDateTime`을 UTC로 읽는다. 같은 ±4/5시간 문제가 H의 PIT 판정에 영향을 줄 수 있고, H는 forward/paper가 돌고 있어 운영 영향이 A보다 클 수 있다. A 연구에 섞지 않고 별도 "H SEC acceptance-time PIT audit"로 분리한다.

## J. Artifact

전부 gitignored 경로 `data/runtime/research_reports/a_edge_first_census_v1/` 아래에 있다.

| 단계 | 파일 |
| --- | --- |
| C0 | `PREREGISTRATION_C0_C1.md`, `C0_REPORT.md`, `c0_results.json`, `c0_candidates.csv`, `c0_census.py`, `SHA256SUMS` |
| C1 V2 | `PREREGISTRATION_C1_V2.md`, `c1v2/C1_V2_REPORT.md`, `c1v2/c1_results.json`, `c1v2/c1_trades.csv`, `c1v2/c1_raw.jsonl`, `c1v2/c0_c1_consistency.json`, `c1v2/SCRATCH_TREE_SHA256.txt`, `c1v2/scratch_tree_shims/`, `c1v2/SHA256SUMS` |
| F2 복구 감사 | `f2_recovery/F2_RECOVERY_REPORT.md`, `f2_recovery/f2_recovery_results.json`, `f2_recovery/pit_tz_impact.json`, `f2_recovery/SHA256SUMS` |
| 보존 위치 (repo, 바이트 동일 사본) | `docs/backtest/strategy_a/edge_first_census_v1/` (사전등록 2개와 sha 파일, C0 / C1 V2 / F2 보고서, `code/` 연구 스크립트 8개) |
| 로컬 아카이브 | `~/usb_archive/2026-10/a/a_edge_first_census_v1_artifacts_20261019.tgz` sha256 `d623ca5f91565cba245f7efce13b6b4b328895a0059740708c0e5b5a58c05edc` (gitignored 산출물 전체 5.3MB) |
| 미커밋 A 연구 인프라 스냅샷 | `~/usb_archive/2026-10/a/strategy_a_uncommitted_research_snapshot_20261019/` (작업트리 미추적 193파일 tgz `f20e0856…`, dirty 추적 파일 diff `883addcc…`, base HEAD, SHA256SUMS). 작업트리 원본은 지우지 않았다. |

부수 발견: TRUE PARITY 재생 인프라(`backtest/replay/session_replay.py`)는 커밋된 적 없는 `SettlementAction / entry_settlement_verdict / entry_fill_risk`에 의존해 현재 트리에서는 import되지 않는다. C1은 스크래치 트리에서만 shim으로 맞췄고(`c1v2/scratch_tree_shims/`), repo 코드는 고치지 않았다.


## K. 운영 해제 기록 (2026-10-19 KST, traderj)

| 항목 | 조치 | 근거 |
| --- | --- | --- |
| backend 코드 | `A_PAPER_RUNTIME_ENABLED`가 false면 entry / position / closing-review owner를 시작하지 않음. SIM 계좌는 과거 A 장부 조회용으로만 로드. research import / activate / decisions는 409 | `main.py`, `api/router.py`, `strategy_a_mover_live/config.py`, 테스트 `test_strategy_a_archive_switch.py`. 운영 dirty 트리에 서버 파일 기준 `git apply`(패치 sha `6f2aec6f…`), 원본 `*.bak-20261019-a-decommission` |
| `.env` | `A_MOVER_LIVE_ENABLED=false`, `A_MOVER_LIVE_ENTRY_AUTHORITY=false`, `A_PAPER_RUNTIME_ENABLED=false` | 백업 `.env.bak-20261019-a-decommission`. 플래그 두 개만 끄면 레거시 PRE_LIVE 진입 경로로 되돌아가므로 런타임 스위치를 함께 둠 |
| `usb-a-snapshot-consumer.timer` | disable --now (A_ONLY) | 09:16 ET A consumer |
| `usb-morning-scan.timer` | disabled (13:53, 다른 세션이 `HOLD_A`와 함께 처리) | 레거시 A 스캐너, A_ONLY |
| `usb-ae-producer` | KEEP (SHARED) | E 공식 paper의 snapshot_0924를 같은 프로세스가 만듦. `HOLD_A`, `ACTIVE_FROM`은 그대로 |
| `usb-grouped-daily`, `usb-splits-refresh` | KEEP (SHARED) | producer가 A universe를 이 파일로 만들고 실패는 예외 처리하지만, 끄면 공용 E 프로세스의 실행 경로(A sweep 생략)가 바뀜. E 자체는 자기 workspace의 forward 저장소를 읽음 |
| `usb-backend` | 재시작만, PID 2248392 → 2606983, NRestarts 0 | 로그 "A: paper runtime DISABLED", SIM 계좌 positions 0 |
| 다른 서비스 | 재시작 0 (frontend, btc-context-ui, crypto-context, crypto-paper, q123, e-rvol-once PID 불변) | |
| DB | 삭제·마이그레이션 0. 본파일 mtime·크기 불변, 기록 행 수 불변. 재시작 후 신규 heartbeat 없음 | `runtime_state.updated_at` 마지막 14:03:00 (구 프로세스) |
| UI | frontend 변경 없음. A 화면은 과거 기록 조회용으로 남고, 승인 등 쓰기는 API에서 409로 거부 | |
| 실주문 | 0 | |

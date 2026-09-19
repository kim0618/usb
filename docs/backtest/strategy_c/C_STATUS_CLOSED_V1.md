# Strategy C 최종 상태: CLOSED

작성 2026-09-19. 이 문서는 Strategy C 연구의 종결 기록이다. 판정을 바꾸거나 되살리기 위한 문서가 아니다.

```text
STRATEGY C
STATUS = CLOSED

Original thesis:
Early Momentum Discovery
(급등 + 거래량 급증 후보의 전방 방향성 알파)

Directional thesis:
REJECTED
```

정확한 의미는 "Strategy C가 실패작이었다"가 아니다. **사전등록된 절차로 충분히 검증했고, 결과가 실패였으므로 연구 프로토콜에 따라 정상 종료했다**는 뜻이다. 실패 결과와 그 재현 경로는 삭제하지 않고 보존한다.

## 1. 단계별 최종 판정

| 단계 | 판정 | 사유 |
| --- | --- | --- |
| **C-M V1** | `GATE-C2 = FAIL` | MFE lift는 존재하나(10D +15% Lift 1.358, CI 하한 1.232) 전방 종가 알파가 없다. 10D 종가 초과 -0.06%p, CI가 0을 포함. 9개 조건 중 조건 3만 실패 |
| **C-V2A** | `SHORT HORIZON = REJECTED` | 보유기간을 줄여도 방향성 우위가 회복되지 않는다. D+1~D+5 × 3변형 15칸 중 통과 0칸, D+1~D+3은 전 변형 음수 |
| **C-V2B** | `VOLATILITY = CONFIRMED` / **DEFERRED, SEPARATE-THESIS** | C-M은 방향이 아니라 변동성 확대를 탐지한다(3/3 변형, 4/4 구간). 방향 중립으로 변동성을 수익화할 수단이 US-B에 없다 |
| **C-E0** | `GATE-CE0 = FAIL`, H1 FAIL, H2 FAIL | 기업 이벤트의 존재가 방향성을 개선하지 않는다. EM이 M_ONLY보다 **못하다** |

### C-E0 핵심 수치 (240 신호일, 사전검사 P1~P8 전부 PASS)

| 코호트 | N | 5D 종가 초과 | 10D 종가 초과 |
| --- | --: | --: | --- |
| M_ONLY (SEC 공시 없는 급등) | 3,355 | **+0.319%p** | +0.445%p |
| EM (공시 있는 급등) | 2,465 | **-0.256%p** | -0.560%p, 95% CI [-1.193, **-0.037**] |
| EM - M_ONLY | | **-0.575%p** | **-1.005%p** |

10일 +15% MFE 적중률: M_ONLY 30.28% vs 통제군 20.07%(초과 **+10.21%p**), EM 15.79% vs 14.23%(초과 +1.56%p).
C-M V1의 헤드라인 Lift는 거의 전부 **뉴스 없는** 부분집합에서 나온다. 이것이 C-V2B의 변동성 해석을 뒷받침하는 독립 증거다.

표본 부족이 아니다: P3 UNKNOWN 4.46%(기준 10%), P4 EM 2,465(기준 300), P5 unique ticker 1,498(기준 100), P6 M_ONLY 3,355, P7 block별 578~659, P8 240/240.

## 2. 종료 규칙

```text
Strategy C directional research is closed.

Reopening Strategy C by:
- threshold tuning
- event-window tuning
- horizon tuning
- event-class cherry-picking
- new score construction
- post-hoc feature selection

is prohibited under the frozen research protocol.
```

한국어로 같은 의미를 다시 적는다. **Strategy C 방향성 연구는 종료되었다.** 임계값 재조정, event window 변경, horizon 변경, 이벤트 클래스 사후 선별(예: E5 제외), 새 composite score 구성, 사후 피처 선택을 통한 재개는 동결된 연구 프로토콜상 금지된다. C-M 단독 트레이딩 백테스터와 C-EM 트레이딩 백테스터도 만들지 않는다.

**Volatility 연구는 예외가 아니라 별건이다.** 재연구하려면 새 전략/연구 ID와 새 사전등록이 필요하고, Strategy C 방향성 연구의 연장으로 취급하지 않는다. 최소 요건은 "D일 True Range를 매칭에 넣어도 변동성 초과가 남는다"를 먼저 사전등록해 검증하는 것이다.

## 3. 보존된 산출물

| 대상 | 위치 |
| --- | --- |
| C-M 사전등록 규칙 | `docs/backtest/strategy_c/c_m_selection_rules_v1.json` (`c769aea5…`) |
| C-M 결과 | `docs/backtest/strategy_c/C_SELECTION_RESULTS_V1.md`, run `cmsel1-855b6a0ce64e3698fc74` |
| C-V2 결과 | `docs/backtest/strategy_c/v2/C_V2_RESULTS_V1.md`, 규칙 `c_v2ab_rules_v1.json` (`2226600f…`) |
| C-E0 선언 | `docs/backtest/strategy_c/v2/C_E0_PREREGISTRATION_V1.md`, `c_e0_rules_v1.json` (`48fc34cb…`), `c_e0_event_taxonomy_v1.json` (`6ee5a16a…`) |
| C-E0 결과 | `docs/backtest/strategy_c/v2/C_E0_RESULTS_V1.md`, run `ce01-234478e8f7ff27570e13` |
| 코드 | `backend/app/backtest/strategy_c_selection/`, `strategy_c_v2/`, `strategy_c_e0/` |
| 테스트 | `backend/tests/strategy_c/` |

재현 경로: C 원시 데이터는 Drive 공통 저장소(`USB-HIST-V1`)에서 `c_raw_freeze.json` 기준으로 511/511 sha256 일치 복원이 가능하다. 복원 후 `app.dev.run_strategy_c_selection`이 run_id `cmsel1-855b6a0ce64e3698fc74`와 후보표 digest `64e57203…`를 그대로 재현했고, `app.dev.run_strategy_c_e0`가 그 후보집합을 행 단위로 재현한 뒤에만 이벤트를 조인한다.

## 4. 재사용되는 계약: SEC acceptanceDateTime = UTC

C-E0 타임존 감사에서 확정했다. submissions JSON의 `acceptanceDateTime`은 `Z` 접미사가 붙어 나오고, **실제로 UTC다**(연구 구간 40건 대조: UTC 40/40 일치, ET 0/40). 예 `2022-11-08T21:10:31Z` ↔ filing 헤더 `20221108161031`(16:10:31 ET).

ET로 읽으면 16:00 이후 접수분이 장중으로 잘못 배치되어 PIT 이벤트 창 소속이 틀어진다. EDGAR를 쓰는 어떤 전략도 조인 전에 이 대조를 수행한다. 대조 원천은 `www.sec.gov/Archives/edgar/data/<cik>/<accno-nodash>/<accno-dashes>-index-headers.html`의 `ACCEPTANCE-DATETIME` 헤더다. SEC는 User-Agent에 실제 연락처를 요구하며(없으면 403), 상한은 10 req/s, US-B는 5 req/s로 운용한다.

## 5. 알려진 비차단 이슈 (재실행하지 않는다)

### 5-1. taxonomy의 10-QT 누락

```text
taxonomy: 10-K / 10-Q / 10-KT 포함, 10-QT 누락
연구기간 내 해당 건수 0 (저장소 전체 9건)
NO IMPACT TO C-E0 V1
```

10-QT는 10-Q의 개명이 아니라 전환기 분기보고서라는 별개 양식이므로 alias addendum 대상이 아니다. 수정은 새 선언에서만 가능하다. C-E0 V1 결과를 다시 실행해 맞추지 않는다.

### 5-2. Event store digest 두 값

수집 CLI가 출력한 `71620fb9…`는 manifest와 타임존 샘플 파일을 **쓰기 전**에 계산한 값이고, run이 기록한 `bf4d744c73cf…`는 그 파일들을 포함한 저장소 전체의 digest다. 동일 저장소이며 **run-record digest가 정본**이다. 이 차이를 맞추기 위해 C-E0 결과를 재실행하지 않는다. 다음 선언에서 수집 CLI가 manifest 기록 후 digest를 계산하도록 순서를 바꾸면 된다.

### 5-3. C-E0 실행 중 수정한 PIT/저장소 정합성 버그 2건

둘 다 C-E0 범위 안이고 Strategy B/D에 영향이 없으며 테스트를 동반한다.

| 버그 | 수정 | 테스트 |
| --- | --- | --- |
| gzip 본문을 `.json` 확장자로 저장해 reader의 `.json.gz` glob과 불일치 | `sec_store.submissions_path`가 `.json.gz`를 생성 | 저장소 왕복이 `read_cik_rows`로 검증됨 |
| 그리드 시작 이전 SEC filing이 session 0으로 잘못 매핑 | `pit.place`가 `moment < open(session 0)`을 배치하지 않음 | `test_placement_rules_including_early_close_and_weekend` |

## 6. 다음

Strategy C는 닫혔다. 활성 연구 라인은 Strategy D(`HISTORICAL_ANALOG_V1`)이며, Strategy B는 별도 세션에서 진행 중이다. 이 문서는 C를 다시 열려는 시도가 있을 때 먼저 읽는 기준 문서다.

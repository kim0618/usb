# Strategy B E0 결과와 실행 원장

계약: `B_E0_BACKTEST_CONTRACT_V1.md` / `b_e0_contract_v1.json`
계약 canonical (FROZEN): `6e3d69af924b89a20ab5c7ab52f26043d37bc6330db58c5b1a41fc796f461f04`
(동결 직전 draft `fc3db6cb...`, 그 전 draft `a1f2605b...`. 이력은 `b_e0_contract_v1.sha256`)
규칙 canonical: `a082b998303d6a95ff2aa2ca3715700f1fe2ae8e10be1a3c192ec7c66660a451`

## 현재 상태 (2026-09-22)

**authoritative 실행 0건.** 계약은 2026-09-22 10:42 KST에 `FROZEN`이 됐다(15/15 전제조건,
A/B 체결 차분 PASS). 실데이터 smoke 1회를 시도했으나 OOM으로 종료돼 산출물이 없다(원장 #1).
판정에 쓰인 결과는 없다.

## 1. 실행 원장

계약 `run_policy.ledger`가 요구하는 원장이다. **폐기한 실행을 포함해 모든 실행**을 여기에
적는다. 진단 목적 실행은 `DIAGNOSTIC`으로 표시하고 판정 근거로 인용하지 않는다.

원장이 없으면 골라 쓴 결과와 유일한 결과를 구분할 방법이 없다. 그래서 실행보다 원장이 먼저다.

| # | run_id | 라벨 | 실행 시각(KST) | 모드 | 사유 | 판정 사용 | 결과 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | (산출물 없음) | BASE | 2026-09-22 11:04 | SMOKE (1세션) | 배선 점검. 데이터 로드 중 커널 OOM kill(anon-rss 7.36GB, 가용 RAM 약 6GB). staging 전 단계라 디렉터리 미생성 | 아니오 | 없음 |

## 2. 판정

| 항목 | 값 |
| --- | --- |
| 판정 | (미실행) |
| 종료 거래 수 | - |
| 거래 발생 세션 수 | - |
| 평균 net R | - |
| 95% CI | - |
| lift 게이트 | - |

## 3. 민감도

| 라벨 | 체결 | 편도 all-in | 평균 net R | 비고 |
| --- | --- | --- | --- | --- |
| `BASE` | `NEXT_BAR_OPEN` | 25bps | - | 판정 입력 |
| `CF_SIGNAL_BAR` | `SIGNAL_BAR` | 25bps | - | stop 주문 기능의 가치 |
| `STRESS_30` | `NEXT_BAR_OPEN` | 30bps | - | - |
| `STRESS_50` | `NEXT_BAR_OPEN` | 50bps | - | - |
| `DIAG_ZERO_COST` | `NEXT_BAR_OPEN` | 0 | - | `DIAGNOSTIC` |

## 4. 반드시 함께 싣는 수치

결과가 나오면 아래를 빠짐없이 기록한다. 계약 8절·11절이 요구하는 것들이다.

- 유니버스 아티팩트 경로·sha256·심볼 수, 제외 심볼(`CON` 포함)과 사유
- 데이터 준비 게이트 보고서와 수집 완결률, 미수집 심볼 목록
- **`SIGNAL_TTL`로 잃은 신호 수.** `NEXT_BAR_OPEN`에서는 신호 봉 다음 1분에 봉이 없으면 체결이
  사라진다(계약 4.2). 표본이 모자랄 경우 첫 번째로 봐야 할 숫자다.
- `candidates.jsonl`의 탈락 사유 분포. 규칙을 고치자는 말을 꺼내기 전에 읽어야 한다.
- 계좌 한도로 막힌 신호 수(`MAX_POSITIONS`, `DAILY_LOSS_LIMIT`, `SIZE_ZERO`)
- lift 두 군의 앵커 수, `DROPPED_NO_REFERENCE_BAR`, `ZERO_BY_NO_FORWARD_BAR` 각각의 개수
- 두 군의 score 분포와 score 매칭 비교 결과(게이트 아님, 한계로 기록)
- 계약 11절의 한계 9개 전부

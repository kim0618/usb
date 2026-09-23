# Strategy B E0 결과와 실행 원장

계약: `B_E0_BACKTEST_CONTRACT_V1.md` / `b_e0_contract_v1.json`
계약 canonical (FROZEN): `6e3d69af924b89a20ab5c7ab52f26043d37bc6330db58c5b1a41fc796f461f04`
(동결 직전 draft `fc3db6cb...`, 그 전 draft `a1f2605b...`. 이력은 `b_e0_contract_v1.sha256`)
규칙 canonical: `a082b998303d6a95ff2aa2ca3715700f1fe2ae8e10be1a3c192ec7c66660a451`

## 현재 상태 (2026-09-22)

**authoritative 실행 0건.** 계약은 2026-09-22 10:42 KST에 `FROZEN`이 됐다(15/15 전제조건,
A/B 체결 차분 PASS). 실데이터 smoke 1회는 OOM으로 종료됐고(원장 #1), 리플레이 메모리 최적화와 code rebind 후
smoke 3회(#2~#4)가 통과했다. smoke는 배선 점검이며 판정에 쓰인 결과는 없다.

## 1. 실행 원장

계약 `run_policy.ledger`가 요구하는 원장이다. **폐기한 실행을 포함해 모든 실행**을 여기에
적는다. 진단 목적 실행은 `DIAGNOSTIC`으로 표시하고 판정 근거로 인용하지 않는다.

원장이 없으면 골라 쓴 결과와 유일한 결과를 구분할 방법이 없다. 그래서 실행보다 원장이 먼저다.

| # | run_id | 라벨 | 실행 시각(KST) | 모드 | 사유 | 판정 사용 | 결과 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | (산출물 없음) | BASE | 2026-09-22 11:04 | SMOKE (1세션) | 배선 점검. 데이터 로드 중 커널 OOM kill(anon-rss 7.36GB, 가용 RAM 약 6GB). staging 전 단계라 디렉터리 미생성 | 아니오 | 없음 |
| 2 | `be0-79abf65f2c3d344dccae` | BASE | 2026-09-22 13:40 | SMOKE (1세션 07-20) | 메모리 최적화(code rebind `e5e60a45`) 후 배선 점검. peak RSS 3.54GB, 176초 | 아니오 | 거래 2, INSUFFICIENT_SAMPLE(정상) |
| 3 | `be0-7f50cfbe35352c11aee1` | BASE | 2026-09-22 13:50 | SMOKE (3세션 07-20~22, run1) | 세션 전환·메모리 누수·결정성 점검 | 아니오 | 거래 8, INSUFFICIENT_SAMPLE(정상) |
| 4 | `be0-7f50cfbe35352c11aee1` | BASE | 2026-09-22 13:54 | SMOKE (3세션, run2) | #3과 바이트 비교(12개 파일 전부 동일) | 아니오 | #3과 동일 |
| 5 | `be0-995dec075c1f4aea610a` | BASE | 2026-09-22 13:52~14:27 | STRICT (authoritative, 84세션) | 동결 B-E0 V1 판정 run. code `e5e60a45`, dataset `ca1ce9d0`, universe `513a540d`, 35분, peak RSS 3.39GB | **예** | **FAIL** |
| 6 | (산출물 없음) | STRESS_30 | 2026-09-22 14:28~14:52 | STRICT (민감도) | 사용자 결정으로 STRESS_30/50 생략, SIGTERM 중단(ABORTED_DIAGNOSTIC) | 아니오 | 없음 |
| 7 | `be0-766076888a43fc695ee1` | DIAG_ZERO_COST | 2026-09-22 14:53~15:24 | STRICT (DIAGNOSTIC) | 비용 제거 진단 | 아니오 | 평균 R -0.064, CI [-0.203, +0.079] |
| 8 | `be0-aa1fc6f27988b957ec62` | CF_SIGNAL_BAR | 2026-09-22 15:24~15:55 | STRICT (counterfactual) | 신호 봉 체결 진단 | 아니오 | 평균 R -0.332, CI [-0.464, -0.198] |

## 2. 판정

| 항목 | 값 |
| --- | --- |
| 판정 | **FAIL** (계약 evaluator 출력: "the 95% CI upper bound -0.2202 <= 0") |
| run_id | `be0-995dec075c1f4aea610a` |
| 종료 거래 수 | 219 (표본 게이트 30 통과) |
| 거래 발생 세션 수 | 77 / 84 (표본 게이트 20 통과) |
| 평균 net R | -0.3545 |
| 95% CI (세션 부트스트랩, seed 20260921, 10,000회) | [-0.4842, -0.2202] |
| lift 게이트 | LIFT_FAIL (신호 382 vs 무셋업 만료 2,166, 30분 전방수익률 차 -0.141, CI [-0.568, +0.267]) |
| 자본 | 7,428.92 → 5,517.90 USD (-25.72%), PF 0.446, 승률 31.5%, MDD 25.72% (세션 종가 기준) |

## 3. 민감도

| 라벨 | 체결 | 편도 all-in | 평균 net R | 비고 |
| --- | --- | --- | --- | --- |
| `BASE` | `NEXT_BAR_OPEN` | 25bps | -0.3545 | 판정 입력, FAIL |
| `CF_SIGNAL_BAR` | `SIGNAL_BAR` | 25bps | -0.3317 | stop 주문 기능의 가치: +0.02R로 미미 |
| `STRESS_30` | `NEXT_BAR_OPEN` | 30bps | - | 생략(사용자 결정, 원장 #6) |
| `STRESS_50` | `NEXT_BAR_OPEN` | 50bps | - | 생략(사용자 결정) |
| `DIAG_ZERO_COST` | `NEXT_BAR_OPEN` | 0 | -0.0642 | `DIAGNOSTIC`: 비용 0에서도 edge 없음 |

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

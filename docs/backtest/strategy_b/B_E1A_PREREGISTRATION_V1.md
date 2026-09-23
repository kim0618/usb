# Strategy B - B-E1-A 사전등록 (FROZEN)

계약: `b_e1a_contract_v1.json` / `.sha256`, FROZEN canonical `b919fc54a65e61d50adf0a56d1c7c7430209339ed5f3ec7c5b0761a7e3505418`
상태: **FROZEN** (2026-09-22). pre-freeze `de1d5c2f...`, 폐기 초안 `31d4e434...`. 생성기: `data/runtime/strategy_b_e1a/freeze/make_b_e1a_contract.py`
부모: B-E0 V1 FROZEN `6e3d69af...`, authoritative 결과 FAIL (`be0-995dec075c1f4aea610a`)

## 1. 한 줄 요약

B-E0 V1에 게이트 **하나**(H1: volume acceleration ≥ 1.0)만 더한 전략을, 발견 표본과 겹치지 않는 2026-01-02 ~ 2026-05-15(93세션)에서
B-E0와 같은 PASS 기준으로 검증한다.

## 2. 가설 H1

- 내용: 최근 5분 거래량이 직전 5분 이상인(참여가 줄지 않는) HOD 돌파만 순기대값을 가진다.
- feature: 기존 `volume_acceleration`(최근 5분 ÷ 직전 5분, 실제 봉, SESSION_LOCAL). 새 feature 없음.
- 임계값: **≥ 1.0**(포함). "거래량이 줄지 않는다"는 의미로 정했다. 발견 표본의 0.90이나 탐색으로 찾은 값은 쓰지 않는다.
- 값이 없을 때(첫 10분 INSUFFICIENT_HISTORY, ZERO_BASELINE, NO_DATA): H1 FAIL.
- 적용 방식(사용자 결정 2026-09-22): 각 candidate episode의 **첫 E0 ENTRY_SIGNALLED 기회에서 정확히 한 번** 판정한다.
  통과하면 그 뒤는 B-E0와 똑같이 진행한다. 실패하면 그 episode는 B-E1-A에서 종료되고, 나중에 값이 1.0 이상이 돼도 다시 신호하지 않는다.
  B-E0 수명주기 규칙으로 새 episode가 열리면 그 episode의 첫 신호에서 다시 한 번 판정한다.
- 실패 기록은 실험 계층의 `H1_FILTERED_OUT` outcome이다. 공용 Strategy B FSM의 상태·`DropReason` enum·전이는 바꾸지 않는다.
- lift: 정의는 상속 그대로다. treatment는 H1을 통과해 ENTRY_SIGNALLED에 도달한 episode이고, H1_FILTERED_OUT은 어느 cohort에도 넣지 않고 따로 센다.

## 3. 그대로 유지하는 것 (B-E0 동결 계약에서 복사, 동일성 확인)

자본 7,428.92 USD / 비용 BASE 25bps(수수료 10 + 체결 15, 수수료 기준가 PRE_SLIPPAGE_REFERENCE_PRICE) / NEXT_BAR_OPEN /
signal TTL 2분 / 사이징·포지션 한도 / FSM·HOD 규칙(rules `a082b998`) / PASS 판정 순서·표본 게이트(30거래·20세션)·평균 net R ≥ 0.10·세션 부트스트랩
(seed 20260921, 10,000회) / lift 게이트 / 데이터 어댑터 D1~D6·readiness / A/B 체결 parity.

## 4. 제외

- H2 과확장 회피, H3 청산·손절: 이번 실험 제외
- Stage-1 Kiwoom 순위 제약: 제외. 과거 run은 B-E0처럼 universe 전체를 본다. 실시간 승격은 별도 **Live-Parity Gate**(NO KIWOOM REPRODUCTION = NO PROMOTION).

## 5. Run

| 라벨 | H1 | 비용 | 판정 입력 | 질문 |
| --- | --- | --- | --- | --- |
| BASE | 켬 | BASE | **예** | B-E0 + H1이 B-E0 PASS 기준을 독립 구간에서 통과하는가 |
| CONTROL_E0_RULES | 끔 | BASE | 아니오 | 같은 구간의 B-E0 규칙 성과(H1 증분 측정용) |
| DIAG_ZERO_COST | 켬 | 0 | 아니오 | 게이트 통과 신호의 총 edge |

## 6. 데이터 상태 - 실행 전 blocker

- universe: 추가 수집 없이 PIT로 만들 수 있다(grouped daily 2024-09~, CS 스냅샷 2025-10-01·2026-01-02·2026-04-01). 아직 만들지 않았다.
- **분봉: 없음.** B universe 분봉은 2026-04(B-E0 워밍업)부터만 저장돼 있다. 2024-09부터 있는 166종목은 A/C 연구용으로 선정된 종목이라 B universe로 쓰면 look-ahead다.
- 상태 `DATASET_NOT_COLLECTED`는 freeze blocker가 아니고(사용자 결정), 모든 authoritative 검증 run의 hard blocker다. 수집 전 run은 DATASET_NOT_READY로 거부된다. 수집은 사용자 별도 승인 후에만 시작한다.
- 수집 추정: 약 4,000요청, 15~20시간, Drive 2.5~3GB(B-E0 실적 비례, 추정치).
- universe sha256, dataset digest, code digest는 나중에 `data/runtime/strategy_b_e1a/freeze/b_e1a_freeze_ledger.jsonl`의 append-only 레코드로 묶는다. 동결 계약 파일은 고치지 않는다.

## 7. 구현 상태

미구현. 구현할 때는 게이트를 끈 상태에서 B-E0 run `be0-995dec075c1f4aea610a`의 핵심 산출물을 바이트 단위로 재현해야 하고,
E1-A 코드 digest는 freeze ledger의 CODE_BINDING 레코드로 묶는다(동결 계약과 B-E0 바인딩은 건드리지 않는다).

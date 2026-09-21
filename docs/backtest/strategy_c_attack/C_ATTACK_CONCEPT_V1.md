# C-ATTACK-V0 개념 V1 (Right-Tail Harvest)

작성 2026-09-21. 새 연구 ID다. Strategy C 방향성 계열을 재개하지 않는다.

```text
C-1 / C-M                 = FAIL
C-2 / C-E0                = FAIL
C-3 / EQM-V0              = FAIL
C-4 / Contextual Analog   = FAIL
STRATEGY C DIRECTIONAL FAMILY = CLOSED   (유지, 이 문서가 바꾸지 않음)
```

## 1. 질문

기존 질문은 "후보를 사서 며칠 들고 있으면 종가 수익률이 좋은가"였고, 답은 "아니다"였다.

이번 질문은 다르다.

> 기존 C 연구에서 유일하게 살아남은 현상, 즉 **상방 Tail 발생 빈도 증가**를 비대칭 Exit(고정 TP / 고정 SL /
> 10거래일 시간청산)로 **실현 수익**으로 바꿀 수 있는가? 그리고 그 결과가 같은 Exit를 적용한 매칭 고변동
> 통제군보다 나은가?

MFE가 크다는 사실만으로는 PASS하지 않는다. TP-first / SL-first 순서, 갭, 동일 봉 모호성, 비용, 낙폭을 모두
반영한 **실현 기대값**으로만 판정한다.

## 2. 근거가 된 관찰 (사전지식, 튜닝 입력 아님)

| 출처 | 관찰 |
| --- | --- |
| C-M V1 | 10D +15% MFE matched Lift 1.358 (CI 하한 1.232). 10D 종가 초과 -0.06%p |
| C-E0 | M_ONLY 10D +15% 적중 30.28% vs 통제 20.07% (+10.21%p). EM은 +1.56%p |
| C-E0 | M_ONLY MAE5 초과 -2.13%p, MAE10 -2.35%p: 하방도 같이 넓다 |
| C-V2B | 로그 기준 하방 확장 ≥ 상방. D일 TR을 통제하면 변동성 초과 ~90% 소멸 |

마지막 두 줄이 이 가설의 가장 큰 위험이다. 상방 꼬리만큼 하방 꼬리도 두꺼우면, 비대칭 Exit는 SL을 먼저 맞고
끝날 수 있다. 그래서 순서 판정과 통제군 비교가 핵심이다.

## 3. 무엇을 쓰고 무엇을 쓰지 않는가

| 쓴다 | 쓰지 않는다 |
| --- | --- |
| C-M0 동결 규칙, primary 240일, 매칭 셀(날짜·가격·ATR·ADV) | C-M 임계값 수정, 새 변형 |
| C-E0 동결 status 중 M_ONLY | Event Presence / Event Quality / Analog F3 재조합 |
| C-M PIT·기업행위 계약(F(t), CA 제외) | 분봉 데이터(Primary gate에서) |
| 새 사전등록 `c_attack_rules_v1.json` | 결과를 본 뒤 TP/SL grid 세분화, Primary pair 교체 |

M_ONLY를 Primary로 고정한 이유는 C-E0에서 Tail 초과가 거기에 몰려 있었기 때문이다. 이 사실로 임계값을 새로
조정하지는 않는다. 전체 C-M0은 기술통계로만 함께 보고한다.

## 4. 폐쇄 문서와의 관계

`C_FAMILY_CLOSEOUT_V1.md` §5는 "C 계열 가설 위에 트레이딩 백테스터를 만드는 것"을 금지하고, "다른 가설은 새
연구 ID와 새 사전등록으로만 시작한다"고 적는다. `C_E0_RESULTS_V1.md` §13은 변동성 연구를 "별도 이름의 새 전략으로
새로 선언할 때만" 허용한다.

C-ATTACK-V0는 사용자가 명시적으로 지시한 **새 연구 ID**이고, 방향성(종가 드리프트) 가설이 아니라 Exit 구조의
기대값 가설이다. 폐쇄 문서는 바이트 그대로 두고, 방향성 FAIL 판정도 유지한다. 이 긴장은 여기 기록해 둔다.
PASS가 나와도 "C 방향성 알파가 있었다"는 뜻이 아니다.

## 5. 전략 성격

공격형이다. 낮거나 중간 승률, 큰 평균 이익, 제한된 평균 손실로 양의 기대값을 노린다. 승률만으로 FAIL하지 않고,
기대값·Profit Factor·낙폭·집중도·시간 안정성을 본다.

## 6. 사전등록 핵심 (정본은 JSON)

| 항목 | 값 |
| --- | --- |
| Primary pool | C-M0 M_ONLY, 2025-09-18..2026-09-01 (240 신호일) |
| Entry | open(D+1). D 종가·프리마켓 진입 금지 |
| Exit family | A 10/5, B 15/5, **C 15/7 (Primary)**, D 20/7, E 20/10 |
| 최대 보유 | 10거래일 (D+1..D+10), 3D·5D는 기술통계 |
| 동일 봉 TP·SL | Primary는 SL_FIRST로 간주, TP_FIRST 가정은 진단용 |
| 갭 | 시가가 SL 아래면 시가 청산(명목 SL보다 큰 손실), TP 위면 시가 청산 |
| 비용 | `C_ATTACK_COST_V1` 가격대별 왕복 25~100bp, stop 청산 +20bp |
| 통제군 | 같은 셀 C-M0 적격 비후보, 동일 Exit·비용 |
| Gate 통계 | 후보 net - 통제 net, 날짜 block bootstrap 95% CI 하한 > 0 |
| Checksum | `0e3b4aa52ea757b625dc2b0bb7292eae176e34ab9934a829c0c1ecdb9e252785` |

## 7. 결과별 의미

- PASS: C는 안정적 방향성 전략이 아니지만, Right-Tail Harvesting용 후보 생성기로 공격형 가치가 있을 수 있다.
  다음 단계는 분봉 순서 정밀화 → 실행비용 → 포지션 사이징 → OOS/Forward Shadow → Paper → Live. 바로 실전 금지.
- FAIL: C Stable Directional = FAIL, C Attack Tail Harvest = FAIL. Strategy C long-side 연구를 종료한다.
  변동성 중립·옵션 전략은 완전히 새로운 Strategy ID로만.
- INCONCLUSIVE: 표본·데이터 무결성 문제, 또는 동일 봉 모호성이 25%를 넘고 순서 가정에 따라 판정이 뒤집힐 때만.
  수익이 나쁘다는 이유로는 쓰지 않는다.

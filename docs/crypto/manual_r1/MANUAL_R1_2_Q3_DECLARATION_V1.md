# MANUAL-R1.2 Q3 사용자 선언 반영 V1

작성 2026-09-28. **기존 문서 7편과 원장은 수정하지 않았다.** provenance overlay만 갱신했다.

`MANUAL_R1_PROVENANCE_CORRECTION_V1.md`과 `MANUAL_R1_TRADE_PROVENANCE_AUDIT_V1.md`이
남긴 미해결 항목 Q3의 답이다. R1.1은 13건을 UNKNOWN으로 두고 멈췄고,
그 13건을 가릴 수 있는 유일한 증거는 문서가 아니라 사용자 진술이었다.

---

## 0. 선언

> 실제 투자 판단으로 수행한 거래는 #17 완료 익절 거래와 현재 OPEN SHORT 두 건뿐이다.
> 그 이전 완료 거래는 전부 개발/테스트 과정에서 발생했다.

이 진술을 `TIER_1_DIRECT` 증거로 채택한다. 원장이 끝까지 제공하지 못한 정보이고,
사용자 본인에게 불리한 방향으로 작동한다. 600.56 USDT 청산 1건이 이 진술 하나로
개발 비용 쪽으로 넘어가는데, 그것을 뒷받침하는 문서는 지금도 없다.

선언이 확정하는 것과 확정하지 않는 것을 구분해야 한다.

| | |
|---|---|
| 확정 | **귀속**. 완료 17건 전부 누구 거래인지 |
| 미확정 | **테스트 종류**. 각 개발 거래가 어떤 검증이었는지 |

R1.1이 바로잡은 오류가 하나의 라벨에 두 주장을 실은 것이었다.
그래서 오버레이는 이제 두 축으로 기록한다.

| 축 | 질문 | 성과 집계 영향 |
|---|---|---|
| `confidence` | 누구 거래인가 | **있음**. 이 축만 돈을 옮긴다 |
| `detail_confidence` | 어떤 테스트인가 | **없음** |

---

## 1. 거래별 정정

| # | 기존 origin | 기존 confidence / tier | **정정 origin** | **confidence / tier** | detail |
|---|---|---|---|---|---|
| 1 | DEV_SMOKE | MEDIUM / TIER_3 | **TEST_UNSPECIFIED** | CONFIRMED / TIER_1 | 미주장 |
| 2 | DEV_SMOKE | MEDIUM / TIER_3 | **TEST_UNSPECIFIED** | CONFIRMED / TIER_1 | 미주장 |
| 3 | DEV_SMOKE | MEDIUM / TIER_3 | **TEST_UNSPECIFIED** | CONFIRMED / TIER_1 | 미주장 |
| 4 | DEV_SMOKE | MEDIUM / TIER_3 | **TEST_UNSPECIFIED** | CONFIRMED / TIER_1 | 미주장 |
| 5 | DEV_SMOKE | MEDIUM / TIER_3 | **TEST_UNSPECIFIED** | CONFIRMED / TIER_1 | 미주장 |
| 6 | SIZING_TEST | HIGH / TIER_2 | SIZING_TEST | **CONFIRMED / TIER_1** | HIGH / TIER_2 유지 |
| 7 | TEST_UNSPECIFIED | MEDIUM / TIER_3 | TEST_UNSPECIFIED | **CONFIRMED / TIER_1** | 미주장 |
| 8 | TEST_UNSPECIFIED | MEDIUM / TIER_3 | TEST_UNSPECIFIED | **CONFIRMED / TIER_1** | 미주장 |
| 9 | TEST_UNSPECIFIED | MEDIUM / TIER_3 | TEST_UNSPECIFIED | **CONFIRMED / TIER_1** | 미주장 |
| 10 | TEST_UNSPECIFIED | MEDIUM / TIER_3 | TEST_UNSPECIFIED | **CONFIRMED / TIER_1** | 미주장 |
| 11 | TEST_UNSPECIFIED | MEDIUM / TIER_3 | TEST_UNSPECIFIED | **CONFIRMED / TIER_1** | 미주장 |
| 12 | TEST_UNSPECIFIED | MEDIUM / TIER_3 | TEST_UNSPECIFIED | **CONFIRMED / TIER_1** | 미주장 |
| **13** | **UNKNOWN** | UNKNOWN / TIER_3 | **TEST_UNSPECIFIED** | **CONFIRMED / TIER_1** | 미주장 |
| **14** | **UNKNOWN** | UNKNOWN / TIER_3 | **TEST_UNSPECIFIED** | **CONFIRMED / TIER_1** | 미주장 |
| 15 | SIZING_TEST | CONFIRMED / TIER_1 | SIZING_TEST | CONFIRMED / TIER_1 | CONFIRMED / TIER_1 유지 |
| 16 | DEV_SMOKE | CONFIRMED / TIER_1 | DEV_SMOKE | CONFIRMED / TIER_1 | CONFIRMED / TIER_1 유지 |
| 17 | USER_MANUAL | CONFIRMED / TIER_1 | **USER_MANUAL** | CONFIRMED / TIER_1 | 선언으로 재확인 |
| OPEN | USER_MANUAL | CONFIRMED / TIER_1 | **USER_MANUAL** | CONFIRMED / TIER_1 | 선언으로 재확인 |

상세 종류를 유지한 것은 **#6·#15·#16** 셋뿐이다. 각각 문서나 배포 메모가
수량·방향·인접 거절 이벤트까지 일치시킨 건이다.

**#1~5는 DEV_SMOKE를 내려놓았다.** 근거였던 배포 메모는 "스모크 체결 4건"이라 적혀 있는데
이 묶음은 5건이라 건수가 어긋났다. 0.001 BTC 최소 수량, 9분 내 LONG/SHORT 교대,
`REVERSE_NOT_ALLOWED` 거절이라는 형태 증거는 여전히 사실이지만 종류를 특정하지 못한다.
선언은 귀속을 확정할 뿐 이 증거를 강화하지 않으므로, 종류는 주장하지 않는다.

**#13·#14는 여전히 문서가 없다.** 귀속만 선언으로 해결됐고
"의도적 테스트였는지 세션 종료 시 방치된 포지션인지"는 미확정으로 남는다.

---

## 2. 성과 재계산

| 그룹 | 기존(V1) | | **정정(V2)** | |
|---|---:|---:|---:|---:|
| | 건수 | net PnL | 건수 | net PnL |
| USER | 1 | +330.75 | **1** | **+330.75** |
| TEST | 3 | -101.13 | **16** | **-744.61** |
| 어디에도 미포함 | 13 | -643.48 | **0** | **0.00** |
| 원장 사실(참고) | 17 | -413.86 | 17 | **-413.86** |

핵심은 **USER 열이 한 푼도 움직이지 않았다**는 점이다.
643.48 USDT가 전부 TEST 쪽으로 갔다. 이 정정으로 사용자 성과가 좋아진 것은 없다.

`-744.61 USDT`는 **운영 계좌에서 개발·검증을 수행한 비용**이다.
600.56 청산 1건이 그 대부분이다.

원장 생애 PnL `-413.86`은 그대로다. 오버레이는 원장을 다시 쓰지 않는다.

---

## 3. 판정

| 항목 | 판정 |
|---|---|
| MANUAL-R1 전체 집계 | `INVALID_MIXED_PROVENANCE` (변경 없음) |
| USER_MANUAL | `INSUFFICIENT_SAMPLE` (변경 없음) |
| **귀속** | **`COMPLETE_BY_DECLARATION`** (신규) |
| Provenance audit | `PARTIAL` (변경 없음) |
| AUTO | `NOT AUTHORIZED` (변경 없음) |

귀속은 완료됐지만 audit은 PARTIAL로 둔다. 개발 16건 중 13건이 아직 테스트 종류가 없고,
`TEST_UNSPECIFIED`는 그 공백을 메운 게 아니라 공백이라고 적은 것이다.

**USER 표본은 1건 그대로다.** Q3는 표본을 늘리는 유일한 즉시 수단이었고,
답은 "늘어나지 않는다"였다. 이것도 결과다. 사용자 거래가 13건 섞여 있었을 가능성이 닫혔다.

---

## 4. 가설 정정

| 가설 | 판정 |
|---|---|
| H1 추격 | `CONTAMINATED` |
| **H2 LONG/SHORT 구별 불가** | **사용자 행동 finding에서 제거** |
| H3 | `CONTAMINATED` |
| H4 | `NOT_EVALUABLE` |
| H5 비용·레버리지 | 사실이나 대상은 테스트 비용 |

H2는 "사용자는 LONG/SHORT를 구별하는 정보가 없다"였다.
이 측정의 대상 16건이 **전부 개발 작업으로 확정**됐다.
주문 경로를 훑는 개발자는 방향에 대한 견해를 표현하지 않으므로
구별되지 않는 것이 정상이다. 이 발견은 테스트 하네스를 묘사한 것이고
사용자에 대해서는 아무 말도 하지 않는다.

단서를 붙여 살려두지 않고 **사용자 행동 진술로서 철회한다.**
완료 1건과 미결 1건으로는 진입 타이밍·사이징·방향 어느 것도 계산되지 않는다.

---

## 5. 하지 않은 것

| 항목 | 상태 |
|---|---|
| 원장 삭제·수정·이벤트 재작성 | **0** |
| 운영 코드 변경 | **0** (`paper/engine.py`·`terminal/api.py`·`paper/session.py` 미접촉) |
| 기존 문서 7편 수정 | **0** |
| v1 데이터 산출물 덮어쓰기 | **0** |
| commit·push | **0** |
| 과거 거래 종류 추정 채우기 | **0** |

---

## 6. 산출물

| 파일 | 상태 |
|---|---|
| `data/research/crypto/manual_r1/trade_provenance_v2.parquet` | 신규 |
| `data/research/crypto/manual_r1/manual_subset_results_v2.json` | 신규 |
| `trade_provenance_v1.parquet`·`manual_subset_results_v1.json` | **보존**. R1.1 문서가 인용하는 값 |
| `research/manual_r1/provenance.py` | 2축 기록으로 갱신 |
| `research/manual_r1/subset.py` | v2 출력, 선언 블록 추가 |

**주의**: `docs/crypto/manual_r2/MANUAL_R2_IMPLEMENTATION_PLAN_V1.md` S3은
`trade_provenance_v1.parquet`을 side table로 적재한다고 적혀 있다.
그 계획을 실행할 때 **v2를 적재해야 한다.** R2 계획 문서는 이번에 수정하지 않았다.

테스트: `backend/tests/crypto/test_manual_r2.py` 65 passed,
crypto 전체 1084 passed. 저장소 루트에서 실행.

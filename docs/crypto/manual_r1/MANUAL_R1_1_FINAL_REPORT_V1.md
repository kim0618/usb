# MANUAL-R1.1 정정 + MANUAL-R2 설계 최종 보고 V1

작성 2026-09-28. 미커밋.

> 보고 순서는 원 지시 §39의 36항목 목록을 그대로 복원하지 못해(원문이 세션에 남지 않음)
> 요구된 산출물 기준으로 재구성했다. 항목 누락 여부는 §14 체크리스트로 확인할 수 있다.

---

## 1. 판정

| 대상 | 판정 |
|---|---|
| MANUAL-R1 전체 집계 | **`INVALID_MIXED_PROVENANCE`** |
| USER_MANUAL subset | **`INSUFFICIENT_SAMPLE`** |
| Provenance 감사 | **`PARTIAL`** |
| MANUAL-R2 기반 | **`READY`** (설계·검증기·테스트 완료, 배포 대기) |
| AUTO | **`NOT AUTHORIZED`** |

---

## 2. 무엇이 잘못됐나

MANUAL-R1은 Paper 원장의 완료 거래 **17건 전부를 사용자 재량매매로 간주**하고
`NO_VERIFIED_EDGE`를 선고했다. 실제로는 대부분이 **운영 계좌에 남은 개발·검증 주문**이었다.

**제 오류다.** 증거는 작업 당시 이미 있었다.

| 있던 증거 | 내용 |
|---|---|
| `CRYPTO_D4_1_SAFE_SIZING_V1.md` §1 | 1.082 BTC 진입·NO_LIQUIDITY 실측 기록 |
| 같은 문서 §8 | "`paper-server-001`은 사용자 실사용 계좌. 개발 smoke는 격리 인스턴스에서" |
| 배포 메모리 | "MAX 0.085 체결 성공 / MAX+1스텝 INSUFFICIENT_MARGIN" |
| 배포 메모리 | "내 smoke 2건 확인 후 cleanup 완료" |

**원장을 열기 전에 이 문서들을 확인하지 않았다.** "수동 계좌의 거래 = 사용자 거래"로
건너뛴 것이 오류의 전부다.

D4.1 §8.4 "기존 거래를 개발 테스트라고 가정하지 않는다"는 **양방향 규칙**이고,
나는 반대 방향으로 어겼다.

---

## 3. 구조적 원인

원장을 아무리 정확히 읽어도 provenance는 나오지 않는다.

| 확인 | 결과 |
|---|---|
| `reason` | FILL 35건 **전부 `MANUAL`** |
| `request_id` | 전부 `open-<ts>-<seq>` / `close-<ts>-<seq>` |
| 주문 경로 | 전부 동일 UI/API 라우트 |
| `origin` | **없음** |

스냅샷 원장 **전 이벤트가 `UNKNOWN_LEGACY`**임을 테스트로 고정했다
(`test_every_event_in_the_real_snapshot_is_legacy`).

**분석자의 부주의이면서 동시에 시스템의 공백이다.**

---

## 4. 감사 방법

| 항목 | 내용 |
|---|---|
| 어휘 | closed vocabulary 13종 |
| 신뢰도 | CONFIRMED / HIGH / MEDIUM / UNKNOWN |
| 증거 등급 | TIER_1(문서·사용자가 그 거래를 지목) / TIER_2(수량·방향·시각 일치) / TIER_3(기간·모양만) |
| **집계 규칙** | **CONFIRMED·HIGH만 성과에 들어간다.** MEDIUM·UNKNOWN은 보고되되 어디에도 안 들어감 |
| 기본값 | 근거 없으면 `UNKNOWN`. 추정 금지 |

각 분류는 **근거 문서·라인을 명시**한다. 독자가 라벨이 아니라 추론에 반대할 수 있어야 한다.

---

## 5. 17건 분류

| origin | 건수 | 신뢰도 |
|---|---|---|
| `USER_MANUAL` | 1 (#17) | CONFIRMED TIER_1 |
| `SIZING_TEST` | 2 (#6 HIGH, #15 CONFIRMED) | TIER_2 / TIER_1 |
| `DEV_SMOKE` | 6 (#1~#5 MEDIUM, #16 CONFIRMED) | TIER_3 / TIER_1 |
| `TEST_UNSPECIFIED` | 6 (#7~#12) | MEDIUM TIER_3 |
| `UNKNOWN` | 2 (#13, #14) | UNKNOWN |

**#13(청산 -600.56)은 UNKNOWN으로 남겼다.** 운영 계좌 청산 테스트를 기록한 문서가 없다.
TEST로 라벨하면 600 USDT 손실이 근거 없이 사용자 장부에서 빠진다.

미결 포지션 SHORT 0.921 @ 84038.20 = **`USER_MANUAL` CONFIRMED TIER_1**.

---

## 6. 정정된 성과

| 그룹 | 거래 | net (USDT) |
|---|---|---|
| **USER** | **1** | **+330.75** |
| TEST | 3 | -101.13 |
| 어디에도 미포함 | 13 | -643.48 |
| (원장 합계, 참고용) | 17 | -413.86 |

미결 사용자 포지션 **미실현 +852.20** (70.5시간 보유).

### 비율 지표는 계산하지 않았다

USER 그룹은 `ratios: WITHHELD_INSUFFICIENT_SAMPLE`.
**PF·승률·MDD·기대값을 표본 1건에서 내지 않는다.** "승률 100%"는 사실이지만 정보가 아니다.
임계값은 10건이고 코드에 상수로 박혀 있다.

### 가장 잘못 읽힌 문장

> "체감과 원장이 다릅니다."

사용자는 자기 거래 2건을 근거로 수익을 체감했고 **그 2건은 실제로 둘 다 플러스다.**
체감이 틀린 게 아니라 **내가 남의 거래를 사용자 것으로 합산**했다.

---

## 7. 회계 검증

| 검사 | 결과 |
|---|---|
| 세 그룹이 원장을 겹침 없이 분할 | PASS |
| 세 그룹 net 합 == 원장 합계 | PASS (1e-6) |
| USER net == 거래 #17 net | PASS |
| MANUAL-R1의 3항목 대조(gross·fees·funding) | **여전히 유효** |

**숫자는 틀리지 않았다. 해석이 틀렸다.**

---

## 8. 가설 재판정

| 가설 | 재판정 |
|---|---|
| H1 사용자는 하락을 추격한다 | **CONTAMINATED** (SHORT 6건 중 사용자 1건) |
| **H2 LONG/SHORT 진입 시점이 구별 안 됨** | **CONTAMINATED** |
| H3 상승 둔화 포착 모양 없음 | CONTAMINATED |
| H4 기다린 거래가 이겼다 | **NOT_EVALUABLE** |
| H5 손실은 판단이 아니라 비용·레버리지 | **VALID, 단 대상이 바뀜** |

**H2 정정이 가장 중요하다.** "17/17 전부 control 중앙값 아래"는 **개발 테스트의 타이밍**이다.
개발자는 기능을 시험했을 뿐 방향을 판단하지 않았으므로 LONG과 SHORT가 구별 안 되는 건
**당연한 결과**이지 사용자 행동의 발견이 아니다.

> "사용자가 LONG/SHORT를 구별하는 정보가 없다"를 **사용자 행동에 대한 결론에서 제거한다.**

H5는 사실이되 **개발 테스트가 운영 계좌에 남긴 비용**이다. 확인된 것만 net -101.13.
이 항목은 오히려 provenance 분리 필요성의 근거가 된다.

**그룹별 진입행동 비교는 재실행하지 않았다.** USER 진입이 1개라 비교할 분포가 없다.

---

## 9. Provenance 계약 (MANUAL-R2)

정본 [MANUAL_R2_PROVENANCE_CONTRACT_V1.md](docs/crypto/manual_r2/MANUAL_R2_PROVENANCE_CONTRACT_V1.md)

| 핵심 | 내용 |
|---|---|
| **기본값** | 미지정 호출 = **`UNKNOWN`**. **`USER_MANUAL`은 절대 기본값 불가** |
| 부여 시점 | 주문 의도(order intent). 엔진이 보기 전 |
| 전파 | OrderRequest → session command → **tape COMMAND** → engine → 원장 이벤트 → Trade → 집계 |
| FEE | **origin 필수.** 이번 사고의 -101.13이 여기서 샜다 |
| FUNDING / LIQUIDATION | 포지션의 origin 상속. 청산이 주인을 바꾸지 않음 |
| smoke helper | `require_origin()` - **기본값 없음**, `USER_MANUAL` 주장 불가 |
| 기존 이벤트 | `UNKNOWN_LEGACY`. **원장 재작성 0** |
| 재생 | 기존 테이프 **바이트 동일** 필수 |

**`UNKNOWN`과 `UNKNOWN_LEGACY`를 나눈 이유**: 전자는 시스템이 물었는데 답이 없는 것,
후자는 애초에 묻지 않은 것. **전자만 결함이다.**

**테이프에도 실어야 하는 이유**: 원장에만 넣으면 재시작 재생에서 origin을 잃는다.

---

## 10. 확인된 위반 2곳

| 위치 | 코드 |
|---|---|
| [api.py:127](backend/app/crypto/terminal/api.py#L127) | `reason: str = "MANUAL"` |
| [engine.py:194](backend/app/crypto/paper/engine.py#L194) | `reason: str = "MANUAL"` |

둘 다 실물 grep으로 확인했다.

검증기는 **레거시 문자열까지 해석한다**: 운영 기본값은 `"USER_MANUAL"`이 아니라 맨 `"MANUAL"`이라,
새 어휘만 보는 검사기는 **문제의 그 줄을 그냥 지나친다.** `LEGACY_REASON_ATTRIBUTION`으로 매핑해
`check_no_unsafe_default("MANUAL")`이 실제로 걸리도록 했고 테스트로 고정했다.

**수정하지 않았다.** 운영 코드 변경 0.

---

## 11. 인간 판단 데이터 계약

정본 [MANUAL_R2_HUMAN_DECISION_DATA_CONTRACT_V1.md](docs/crypto/manual_r2/MANUAL_R2_HUMAN_DECISION_DATA_CONTRACT_V1.md)

| 핵심 | 내용 |
|---|---|
| 필수 | `decision_id`·`decided_at_ms`·`server_received_ms`·`side`·`reason_code`·`conviction(1-5)` |
| reason_code | closed 10종. **`GUT` 포함** |
| **PIT** | 주문 **전** 기록. 첨부 시세는 `decided_at_ms` **이전** 스냅샷 |
| 사후 입력 | `RETROSPECTIVE` - **연구 제외** |
| 수정 | append-only 개정 레코드. 연구는 **최초 레코드만** |
| 누락 | `MISSING`. **추정 금지.** 단 성과에는 포함 |
| 저장 | `decisions.jsonl` - **원장 밖**. 리셋에도 생존 |
| 표본 수 | **지금 정하지 않는다.** 판정 기준은 수집 전 사전등록(R3) |

**`GUT`을 넣은 이유**: 없애면 근거를 말 못 하는 사용자가 그럴듯한 코드를 고르고,
**그 하나가 아니라 라벨 전체가 오염된다.**

**원장 밖에 두는 이유**: 원장은 회계 무결성 대상이라 재생이 바이트 일치해야 한다.
판단 스키마는 더 자주 바뀌고 개정을 허용해야 한다. 섞으면 라벨 변경이 회계 재생을 깬다.

---

## 12. 테스트

| 영역 | 내용 |
|---|---|
| provenance 감사 | 17건 전수 분류, 어휘 폐쇄성, CONFIRMED=TIER_1, 증거 필수, #13·#14가 UNKNOWN |
| 회계 | 3그룹 무중복 분할, net 합 == 원장 합, USER == #17, 비율 withheld |
| provenance 계약 | 기본값 UNKNOWN, smoke 기본값 없음, `USER_MANUAL` 사칭 불가, **레거시 `"MANUAL"` 포착** |
| 분석 분리 | UNKNOWN은 reportable 아님, MANUAL≠AUTO, origin 1:1 그룹, MDD는 필터 후 |
| 스냅샷 PIT | 첨부 시세가 결정보다 미래면 거부, 수신<결정 거부, 사후 라벨=RETROSPECTIVE |
| 하위 호환 | origin 없는 이벤트=LEGACY, 실제 스냅샷 전건 LEGACY, 미인식 값은 좋은 값으로 강제 안 됨 |

**`test_manual_r2.py` 55 passed.**
**crypto 전체 1015 passed** (저장소 루트에서 실행. `backend/`에서 돌리면 상대경로 때문에 8 error가 나는데 내 변경과 무관하다).

테스트 중 하나가 실패했다: 금지 토큰을 직렬화 문자열에서 찾다가 **"이 지표들을 보고하지 않는다"는
설명문 자체를 잡았다.** 키 기준 검사로 고쳤다.

---

## 13. 운영 안전

| 항목 | 결과 |
|---|---|
| 주문·CLOSE·RESET·EMERGENCY | **0** |
| 서비스 재시작 | **0** |
| 소스 배포 | **0** |
| 운영 원장 수정·삭제 | **0** |
| 과거 이벤트 수정 | **0** |
| 기존 MANUAL-R1 문서 4편 | **수정 0, 보존** |
| 운영 crypto 경로(`paper`·`terminal`·`server`) 변경 | **0** (git status 확인) |

읽은 것은 `data/research/crypto/manual_r1/snapshot/` **로컬 사본**뿐이다.

---

## 14. 산출물 체크리스트

| 산출물 | 상태 |
|---|---|
| `manual_r1/MANUAL_R1_PROVENANCE_CORRECTION_V1.md` | 완료 |
| `manual_r1/MANUAL_R1_TRADE_PROVENANCE_AUDIT_V1.md` | 완료 |
| `manual_r1/MANUAL_R1_1_FINAL_REPORT_V1.md` | 이 문서 |
| `manual_r2/MANUAL_R2_PROVENANCE_CONTRACT_V1.md` | 완료 |
| `manual_r2/MANUAL_R2_HUMAN_DECISION_DATA_CONTRACT_V1.md` | 완료 |
| `manual_r2/MANUAL_R2_IMPLEMENTATION_PLAN_V1.md` | 완료 |
| `manual_r1/trade_provenance_v1.parquet` | 완료 (17행) |
| `manual_r1/manual_subset_results_v1.json` | 완료 |
| `manual_r2/provenance_contract_v1.json` | 완료 |
| `manual_r2/human_decision_schema_v1.json` | 완료 |
| 코드 `research/manual_r1/provenance.py`·`subset.py` | 완료 |
| 코드 `research/manual_r2/`(contract·decision·runner) | 완료 |
| 테스트 | 55 + 회귀 1015 |
| 운영 배포 | **0** |

---

## 15. 사용자 결정 대기

| # | 결정 |
|---|---|
| **Q1** | 판단 수집 형태: **A(주문 모달) / B(별도 메모) / C(수집 없음)** |
| **Q2** | 구현 계획 S1~S5 중 어디까지 실행할지 |
| **Q3** | **UNKNOWN 13건 중 사용자 본인 거래가 있는지** |

**Q3가 표본을 늘리는 유일한 즉시 수단이다.** 사용자가 지목하면 TIER_1로 승급되고
USER 표본이 1건에서 늘어난다. **내가 추정하지 않는다.**

C를 고르면 R3는 성과 검정만 가능하고 **H2는 영구히 미해결**이라는 점만 기록해 둔다.

---

## 16. 교훈

| # | 내용 |
|---|---|
| 1 | **성과 계산 전에 provenance를 묻는다.** 계좌가 "수동 계좌"라는 사실은 그 안의 거래가 사용자 것이라는 뜻이 아니다 |
| 2 | 기록되지 않은 것은 복원되지 않는다. **origin은 사후에 만들 수 없다** |
| 3 | 개발 테스트를 운영 계좌에서 하면 **그 비용이 영구히 사용자 성과에 섞인다** |
| 4 | D4.1 §8.4는 양방향 규칙이다. 반대 방향으로도 어길 수 있다 |
| 5 | 표본 수가 병목이 아니라 **라벨이 병목이다**. 거래를 100건 쌓아도 이유가 없으면 H2는 그대로다 |

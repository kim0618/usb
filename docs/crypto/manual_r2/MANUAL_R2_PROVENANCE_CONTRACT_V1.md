# MANUAL-R2 Provenance 계약 V1

작성 2026-09-28. **설계 문서다. 운영 배포 0, 운영 코드 수정 0.**

MANUAL-R1.1이 확인한 공백을 메운다: **원장이 주문의 출처를 담지 않는다.**
개발자의 smoke와 사용자의 판단이 같은 라우트를 지나며 둘 다 `reason: MANUAL`로 기록된다.

---

## 0. 요구사항 한 줄

> 어떤 거래든 **누가 어떤 목적으로 만들었는지**가 원장에 남고,
> 분석이 그 기준으로 성과를 **완전히 분리**할 수 있어야 한다.

"완전히"가 핵심이다. 한 레이어에만 필드를 넣고 나머지에서 잃어버리는 구조는 금지다(§19).

---

## 1. 현재 구조 조사 결과

| 레이어 | 현재 | 문제 |
|---|---|---|
| `terminal/api.py` `OrderRequest` | **`reason: str = "MANUAL"`** | **기본값이 사용자 주문을 뜻한다.** 아무것도 지정하지 않은 호출이 사용자 거래가 된다 |
| `paper/engine.py` `submit_order` | `reason: str = "MANUAL"` | 같은 문제 |
| 원장 이벤트 | `reason`이 FILL·FEE·POSITION_*에 전파됨 | 전파 경로 자체는 이미 있다 |
| `paper/analytics.py` `_origin()` | `reason`에서 origin 파생 | `MANUAL`/`EMERGENCY`/`LIQUIDATION`/`AUTO`만 구분. **USER와 TEST를 나누지 못한다** |
| `analytics.summarize` | `by_origin` 그룹 존재 | 그릇은 있는데 내용이 없다 |

**좋은 소식**: `reason`이 이미 진입점에서 원장 이벤트까지 전 경로를 지난다.
`origin`은 **같은 길을 따라가면 된다.** 새 배관을 놓을 필요가 없다.

**나쁜 소식**: `reason`의 기본값이 `MANUAL`이다. D4.1 §8이 `SMOKE_TEST`를 설계했으나
"설계만 기록하고 넣지 않았다"고 명시돼 있고, 그 사이 17건이 섞였다.

---

## 2. Origin 어휘 (closed vocabulary)

문자열 자유입력을 허용하지 않는다. 자유입력은 분류를 분할(partition)이 아니게 만든다.

| origin | group | 의미 |
|---|---|---|
| `USER_MANUAL` | USER | **사용자가 UI에서 직접 방향을 판단해 낸 주문** |
| `DEV_SMOKE` | TEST | 개발 중 기능 확인 |
| `DEPLOY_SMOKE` | TEST | 배포 후 확인 |
| `SIZING_TEST` | TEST | MAX / SAFE MAX / 프리셋 검증 |
| `PNL_TEST` | TEST | 손익·미리보기 검증 |
| `RESET_TEST` | TEST | 리셋 동작 검증 |
| `LIQUIDATION_TEST` | TEST | 강제청산 검증 |
| `ACCOUNTING_TEST` | TEST | 회계 항등식 검증 |
| `RESEARCH_TEST` | RESEARCH | 연구 재생·실험 |
| `TEST_UNSPECIFIED` | TEST | 테스트인 것은 확실하나 종류 미상 |
| `AUTO_PAPER` | AUTO | 미래 AUTO 전략 |
| `UNKNOWN` | UNKNOWN | 판정 불가 |
| `UNKNOWN_LEGACY` | UNKNOWN | **origin 도입 이전의 기존 이벤트** |

---

## 3. 안전한 기본값 (가장 중요한 규칙)

| # | 규칙 |
|---|---|
| **D1** | **`USER_MANUAL`은 절대 기본값이 될 수 없다.** 어떤 경로에서도 자동으로 붙지 않는다 |
| **D2** | origin이 주어지지 않은 호출의 기본값은 **`UNKNOWN`**이다 |
| **D3** | `USER_MANUAL`은 **사용자가 UI에서 직접 누른 경로에서만** 명시적으로 부여된다 |
| **D4** | smoke helper·CLI·테스트 fixture는 **origin을 필수 인자로 요구한다.** 기본값 없음 |
| **D5** | origin 도입 이전 이벤트는 읽을 때 **`UNKNOWN_LEGACY`**로 해석한다. 과거 이벤트 재작성 금지 |

**D1이 이번 사고의 직접적 재발 방지책이다.** 현재 `api.py`의 `reason: str = "MANUAL"`은
D1 위반 구조이며, 수정 없이는 같은 일이 반복된다.

---

## 4. 전파 경로

origin은 `reason`이 지나는 **모든 지점**을 함께 지난다.

```
UI 주문 버튼
  └─ OrderRequest{ side, qty, intent, origin }        ← origin 필수, 기본값 없음
       └─ session.command({... "origin": ...})
            └─ tape COMMAND record (origin 포함)       ← 재생 시 복원 가능
                 └─ engine.submit_order(origin=...)
                      ├─ ORDER_SUBMITTED  (origin)
                      ├─ FILL             (origin)
                      ├─ FEE              (origin)
                      ├─ POSITION_OPEN    (origin)
                      ├─ POSITION_INCREASE(origin)
                      └─ POSITION_CLOSE   (origin)
                           └─ analytics.build_trades → Trade.origin
                                └─ summarize(by_origin, by_origin_group)
```

| # | 규칙 |
|---|---|
| P1 | **FEE에도 origin이 붙어야 한다.** 이번 사고에서 테스트 수수료 -101.13 USDT가 사용자 성과에 섞였다 |
| P2 | `FUNDING`은 포지션에 귀속되므로 그 포지션의 origin을 따른다 |
| P3 | `LIQUIDATION`은 청산된 포지션의 origin을 상속한다. 청산 자체가 origin을 바꾸지 않는다 |
| P4 | 테이프 COMMAND 레코드에 origin이 실려야 **재생이 origin을 복원**한다. 원장에만 넣으면 재생 시 잃는다 |

---

## 5. 하위 호환

| # | 규칙 |
|---|---|
| B1 | **기존 원장·테이프를 재작성하지 않는다** |
| B2 | origin 없는 이벤트를 읽으면 `UNKNOWN_LEGACY` |
| B3 | 기존 테이프 재생이 **바이트 동일**해야 한다. origin은 새 레코드에만 실린다 |
| B4 | `ledger.append(**payload)`는 키 추가가 가산적이므로 스키마 버전 상승 없이 가능 |
| B5 | 구버전 분석 코드가 새 원장을 읽어도 깨지지 않는다(모르는 키 무시) |

**B3가 배포 가능성을 결정한다.** 운영 런은 재시작 시 테이프를 재생해 복구하므로,
재생이 달라지면 `LEDGER_DIVERGENCE`로 기동이 거부된다.

---

## 6. 분석 분리

| 화면 | 필터 | 비고 |
|---|---|---|
| **Manual 성과** | `origin_group == USER` | 정본. 사용자가 보는 숫자 |
| **AUTO 성과** | `origin == AUTO_PAPER` | 별도 계좌·별도 원장이어도 origin 유지 |
| 테스트 | `origin_group == TEST` | **성과 화면 기본 제외**. 필요 시 별도 조회 |
| UNKNOWN | `origin_group == UNKNOWN` | **어느 성과에도 기본 미포함** |
| 전체 원장 | 필터 없음 | 감사용. 모두 보존 |

| # | 규칙 |
|---|---|
| A1 | 원장은 **통합 이력**을 유지한다. 분리는 집계 단계에서만 |
| A2 | `UNKNOWN`을 사용자 성과에도, 테스트 성과에도 넣지 않는다 |
| A3 | Manual과 AUTO 성과를 **절대 합산하지 않는다** |
| A4 | 자본 곡선·MDD도 origin 필터 이후에 계산한다 |

---

## 7. 개발 테스트 정책 (D4.1 §8 강화)

| # | 규칙 |
|---|---|
| T1 | 개발 smoke는 **격리 인스턴스**(별도 포트·별도 run dir)에서 한다. 1순위 |
| T2 | 운영에서 불가피한 테스트는 **반드시 명시적 TEST origin**을 붙인다 |
| T3 | 사용자 포지션 보유 중 운영 주문·RESET 금지 |
| T4 | **기존 거래를 개발 테스트라고 가정하지 않는다** (D4.1 §8.4, 원문 유지) |
| **T5** | **기존 거래를 사용자 거래라고도 가정하지 않는다** (MANUAL-R1.1이 추가) |

T5가 이번에 추가된 조항이고, MANUAL-R1이 어긴 것이 정확히 이것이다.

---

## 8. 이번 단계에서 하지 않은 것

| 항목 | 상태 |
|---|---|
| 운영 코드 수정 | **0** |
| 운영 배포 | **0** |
| 원장 마이그레이션 | **0** |
| API 변경 | **0** (설계만) |
| UI 변경 | **0** |

로컬 프로토타입(`backend/app/crypto/research/manual_r2/`)은 **연구 네임스페이스**이며
운영 경로에 연결되지 않는다.

---

## 9. 구현 시 변경 범위 (사전 보고)

실제 적용 시 손대야 하는 기존 파일은 다음과 같다. **승인 전 수정하지 않는다.**

| 파일 | 변경 | 위험 |
|---|---|---|
| `terminal/api.py` `OrderRequest` | `origin` 필드 추가, `reason` 기본값 제거 | **중간** - 기존 프론트가 origin 없이 호출하면 `UNKNOWN`이 된다(안전한 실패) |
| `paper/engine.py` `submit_order` | `origin` 인자 추가·전파 | 낮음 - 키 추가는 가산적 |
| `paper/ledger.py` | 변경 없음 | - |
| `paper/analytics.py` `_origin()` | 이벤트의 origin을 우선 읽고, 없으면 `UNKNOWN_LEGACY` | **중간** - 기존 `by_origin` 카테고리가 변한다 |
| 프론트 주문 호출 | `origin: "USER_MANUAL"` 명시 | 낮음 |

**대안**: `server.py`에 새 라우트를 얹는 기존 패턴(`api.py` 무접촉)을 쓰면 위험이 더 낮다.
D3.5 이후 이 저장소가 반복해 쓴 방식이다.

# MANUAL-R1 거래 Provenance 감사 V1

작성 2026-09-28. 생성물. 정정 배경은 `MANUAL_R1_PROVENANCE_CORRECTION_V1.md`.

**원장은 수정하지 않았다.** 이 문서는 원장 위에 얹는 해석 레이어이며,
각 분류는 근거와 tier를 함께 적는다.

---

## 0. 증거 강도 정의

| tier | 의미 | 사용 |
|---|---|---|
| **Tier 1 (직접)** | 문서나 사용자가 **이 거래를 특정**한다 | `CONFIRMED` |
| Tier 2 (강한 정황) | 수량·방향·시각이 문서화된 행동과 **정확히 일치** | `HIGH` |
| Tier 3 (약한 정황) | 시기나 모양만 맞음 | `MEDIUM` 또는 `UNKNOWN` |

**성과 집계 규칙**: `CONFIRMED`와 `HIGH`만 자기 그룹의 성과에 들어간다.
**`MEDIUM`과 `UNKNOWN`은 보고되지만 어느 쪽에도 세지 않는다.**
불확실한 거래가 조용히 한쪽 편을 드는 것을 막는 유일한 방법이다.

---

## 1. 전체 17건 분류

| # | 진입(UTC) | 방향 | 수량 | 레버리지 | net(USDT) | origin | confidence | evidence tier |
|---|---|---|---|---|---|---|---|---|
| 1 | 09-23 06:20 | LONG | 0.001 | 10x | -0.10 | DEV_SMOKE | MEDIUM | Tier 3 |
| 2 | 09-23 06:21 | SHORT | 0.001 | 10x | -0.13 | DEV_SMOKE | MEDIUM | Tier 3 |
| 3 | 09-23 06:22 | LONG | 0.001 | 10x | -0.09 | DEV_SMOKE | MEDIUM | Tier 3 |
| 4 | 09-23 06:22 | SHORT | 0.001 | 10x | -0.10 | DEV_SMOKE | MEDIUM | Tier 3 |
| 5 | 09-23 06:30 | LONG | 0.001 | 10x | -0.12 | DEV_SMOKE | MEDIUM | Tier 3 |
| 6 | 09-23 06:45 | LONG | 0.085 | 10x | -8.10 | **SIZING_TEST** | HIGH | Tier 2 |
| 7 | 09-23 07:18 | LONG | 0.010 | 10x | -1.11 | TEST_UNSPECIFIED | MEDIUM | Tier 3 |
| 8 | 09-23 07:30 | LONG | 0.084 | 10x | -10.15 | TEST_UNSPECIFIED | MEDIUM | Tier 3 |
| 9 | 09-23 07:33 | LONG | 0.083 | 10x | -12.45 | TEST_UNSPECIFIED | MEDIUM | Tier 3 |
| 10 | 09-23 08:03 | SHORT | 0.082 | 10x | +1.34 | TEST_UNSPECIFIED | MEDIUM | Tier 3 |
| 11 | 09-23 08:06 | LONG | 0.404 | 50x | +9.72 | TEST_UNSPECIFIED | MEDIUM | Tier 3 |
| 12 | 09-23 08:15 | SHORT | 0.408 | 50x | -35.14 | TEST_UNSPECIFIED | MEDIUM | Tier 3 |
| 13 | 09-23 08:26 | LONG | 0.389 | 50x | -600.56 | UNKNOWN | UNKNOWN | Tier 3 |
| 14 | 09-24 03:31 | SHORT | 0.051 | 50x | +5.41 | UNKNOWN | UNKNOWN | Tier 3 |
| 15 | 09-24 04:40 | LONG | 1.082 | 50x | -92.11 | **SIZING_TEST** | CONFIRMED | **Tier 1** |
| 16 | 09-24 04:41 | LONG | 0.010 | 50x | -0.92 | **DEV_SMOKE** | CONFIRMED | **Tier 1** |
| 17 | 09-24 05:16 | SHORT | 4.313 | 50x | +330.75 | **USER_MANUAL** | CONFIRMED | **Tier 1** |

## 2. 미결 포지션

| 진입(UTC) | 방향 | 수량 | 레버리지 | 미실현 | origin | confidence | tier |
|---|---|---|---|---|---|---|---|
| 09-26 07:52:13 | SHORT | 0.921 | 10x | **+852.20** | **USER_MANUAL** | CONFIRMED | **Tier 1** |

근거: 9/27 V2.7 배포 기록이 재시작 전후 원장 불변을 확인하며
"**사용자 SHORT 0.921@84038.20**"이라고 적고 있다. 수량·진입가가 정확히 일치한다.

---

## 3. 집계

| 그룹 | 건수 | 성과 집계 대상 | gross | 수수료 | **net** |
|---|---|---|---|---|---|
| **USER (countable)** | **1** | #17 | +729.50 | 398.75 | **+330.75** |
| **TEST (countable)** | **3** | #6·#15·#16 | +7.67 | 108.80 | **-101.13** |
| 집계 제외 (MEDIUM) | 11 | - | - | - | (-42.90) |
| 집계 제외 (UNKNOWN) | 2 | #13·#14 | - | - | (-595.15) |

집계 제외 13건의 net 합계는 **-643.48 USDT**다. 괄호로 적은 것은
**어느 성과에도 넣지 않는다**는 뜻이고, 참고로만 둔다.

`by_origin`: DEV_SMOKE 6 / TEST_UNSPECIFIED 6 / SIZING_TEST 2 / UNKNOWN 2 / USER_MANUAL 1
`by_group` : TEST 14 / UNKNOWN 2 / USER 1

---

## 4. Tier 1 근거 원문

### 거래 15 - SIZING_TEST

`docs/crypto/CRYPTO_D4_1_SAFE_SIZING_V1.md` §1:

> 실측(운영, 2026-09-24):
> 포지션 진입   **1.082 BTC**   ask depth 10.740  -> 체결
> 전량 청산 시도 **1.082 BTC**   bid depth  0.157  -> NO_LIQUIDITY

원장에도 **1.082 수량의 `NO_LIQUIDITY` 거부가 2건** 있다. 같은 사건이다.

### 거래 16 - DEV_SMOKE

배포 메모리:

> 이번에 **내 smoke 2건** 확인 후 cleanup 완료 → 최종 운영 상태 정확히 10,000,000원·
> 현재손익 0·FLAT·LIVE, **전체 16건 이력 보존**

거래 15·16은 2번째와 3번째 `ACCOUNT_RESET` 사이의 **유일한 2건**이고,
3번째 리셋이 그 cleanup이다. 그 시점 이력이 정확히 16건이다.

### 거래 17 - USER_MANUAL

사용자 선언(MANUAL-R1.1 §0): "최근 완료된 익절 거래 1건 ... **약 +330.75 USDT**".
원장에서 net이 +330.75인 거래는 **이것 하나뿐**이고,
개발자 cleanup 리셋이 계좌를 FLAT으로 되돌린 **직후 첫 거래**다.

### 거래 6 - SIZING_TEST (Tier 2)

배포 메모리:

> 실측: **MAX 0.085 체결 성공** + 예고 마진/청산가가 실제와 완전 일치,
> **MAX+1스텝은 INSUFFICIENT_MARGIN**

거래 6은 LONG **0.085**이고, 원장에 **0.086 수량의 `INSUFFICIENT_MARGIN` 거부**가 있다.
수량·방향·인접 거부가 모두 맞는다.

---

## 5. UNKNOWN으로 남긴 2건과 그 이유

### 거래 13 - 청산 -600.56 USDT

09-23 08:26:55 진입, **5.8시간 뒤 14:13:00 강제청산**.

**어떤 작업 보고나 배포 기록에도 운영 계좌의 청산 테스트가 없다.**
의도적 테스트인지, 세션이 끝나며 남겨진 포지션인지, 사용자의 거래인지 확정할 수 없다.

이 1건이 기존 MANUAL-R1 손실의 대부분을 만들었으므로,
**귀속을 추측하지 않는 것이 특히 중요하다.**

### 거래 14 - SHORT 0.051 @ 50x

09-24 03:31:27, 46.5분 보유, net +5.41.
거래 13에서 **798분 뒤**, 거래 15(D4.1 세션)에서 **68분 전**.
어느 블록에도 속하지 않는다.

---

## 6. 원장이 provenance를 담지 못하는 구조적 이유

| 확인 항목 | 결과 |
|---|---|
| `reason` 필드 | FILL 35건 **전부 `MANUAL`** |
| `request_id` 형식 | **전부 `open-<ts>-<seq>` / `close-<ts>-<seq>`** |
| origin 필드 | **없음** |

개발자의 smoke와 사용자의 판단이 **같은 UI 라우트**를 통과하므로 원장에서 구별되지 않는다.
D4.1 §8이 `SMOKE_TEST` reason을 설계했으나
"이번 작업 범위에서는 **설계만 기록하고 넣지 않았다**"고 명시돼 있다.

**과거 이벤트를 재작성하지 않는다.** 이 감사는 원장 밖의 해석 레이어로 남는다.

---

## 7. 감사 판정: **PARTIAL**

| 항목 | 값 |
|---|---|
| Tier 1으로 확정 | **3건** (#15·#16·#17) + 미결 1건 |
| Tier 2로 확정 | 1건 (#6) |
| MEDIUM (집계 제외) | 11건 |
| UNKNOWN (집계 제외) | 2건 |
| **완전 분류 달성** | **아니오** |

프롬프트 §8의 원칙("정확성 > 완전 분류")대로,
확정할 수 없는 13건을 억지로 분류하지 않았다.

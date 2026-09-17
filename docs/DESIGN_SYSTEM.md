# USB Visual Design System

Stage 9.16의 visual language는 Black / White / Blue다. Logo 형태와 글꼴, Backend/API 및 매매 semantics는 이 문서의 범위가 아니다.

## Theme tokens

| Token | Dark | Light |
|---|---|---|
| background | `#000000` | `#F7F8FA` |
| sidebar / header | `#050505` | `#FFFFFF` |
| surface-1 | `#0A0A0A` | `#FFFFFF` |
| surface-2 | `#111111` | `#F8FAFC` |
| surface-hover | `#151515` | `#EFF6FF` |
| border | `#242424` | `#E5E7EB` |
| border-subtle | `#181818` | `#EEF0F3` |
| text-primary | `#F5F5F5` | `#111827` |
| text-secondary | `#B0B0B8` | `#64748B` |
| text-muted | `#85858F` | `#7C899C` |
| primary | `#2563EB` | `#2563EB` |
| primary-hover | `#3B82F6` | `#1D4ED8` |
| success | `#22C55E` | `#16A34A` |
| warning | `#F59E0B` | `#F59E0B` |
| danger | `#EF4444` | `#EF4444` |

각 status color에는 테마별 soft background token이 함께 존재한다.

## Semantic rules

- Primary Blue Action: 각 화면에서 사용자의 다음 핵심 단계 하나에 사용한다. Filled blue와 white text로 Secondary보다 강하게 표시한다.
- Secondary Blue Action: 보기, 상세, 미리보기 같은 보조 작업에 사용한다. Neutral surface의 blue outline이며 hover에서만 soft blue background를 사용한다.
- Selected Blue: navigation, tab, segmented control의 선택 상태에 사용하며 CTA 계층과 구분한다.
- Success Green: 정상, 연결, 완료, 채택, 양수 PnL/R.
- Danger Red: 중지, 오류, 거절, 손실, destructive action.
- Warning Amber: 확인 필요, Safe Mode, 미체결 등 주의 상태.
- Neutral: 일반 콘텐츠, 미결정, 분석 완료, 미연결 및 정보성 상태.

일반 숫자와 heading은 primary text를 사용하며 navigation 색과 financial signal 색을 섞지 않는다. Card와 Table은 surface/border token만 사용하고 gradient, glow, glassmorphism, 강한 shadow를 사용하지 않는다.

Trading 계좌 요약은 동일한 크기와 구조를 유지하면서 `총 자산 > 투자 중 ≈ 보유 현금 > 평가 손익 ≈ 오늘 손익` 순으로 border와 inner highlight 강도를 조절한다. 총 자산만 primary blue highlight를 사용하며 나머지는 각 의미의 절제된 accent를 유지한다. Decorative icon은 숫자보다 약하지만 즉시 식별 가능한 대비를 갖는다.

Secondary와 muted text는 Dark/Light 모두 작은 helper, metadata, table header를 읽을 수 있어야 한다. Primary와의 단계 차이는 유지하며 muted text를 primary white/black으로 승격하지 않는다.

Trading table은 body surface와 구분되는 neutral header surface, 읽기 쉬운 600 weight header, subtle row separator와 neutral hover를 사용한다. 단일 카드 안의 병렬 metric은 responsive grid와 얇은 divider로 구분하고, helper text는 13px과 여유 있는 line-height를 기준으로 하되 title보다 낮은 위계를 유지한다.

## Analysis Navigation

- 후보 종목, GPT 분석, 채택 후보는 같은 shared compact navigation tab을 사용한다.
- Active tab은 soft blue background, blue border/text로 표시하고 inactive tab은 neutral surface와 secondary text를 사용한다.
- 탭은 36px 높이와 button system의 radius/weight를 공유하되 CTA보다 낮은 시각적 위계를 유지한다.
- Candidates에서는 프롬프트 복사가 Primary, 미리보기와 GPT 분석 이동이 Secondary다.
- Research에서는 분석 결과 입력이 Primary, 상세보기가 Secondary Compact이며 채택/거절은 Green/Red semantic action을 유지한다.

## Brand Logo

Official Mark는 shield/U silhouette와 중앙 diagonal geometry를 결합한 geometric blue symbol 및 `USB` wordmark다. Symbol geometry는 모든 테마와 크기에서 동일하게 유지한다.

- Dark: blue symbol + white (`#F5F5F5`) wordmark
- Light: blue symbol + dark (`#0F172A`) wordmark
- Full Lockup: desktop sidebar, documentation, login/future landing
- Symbol Only: favicon, compact sidebar, future app icon
- 투명 배경의 flat vector만 사용하며 glow, shadow, 3D, teal/cyan, serif monogram을 사용하지 않는다.
- Full Lockup의 wordmark는 path 또는 현재 UI font stack을 사용하며 logo 전용 외부 font dependency를 추가하지 않는다.

## Strategy Status Colors

Backend의 raw strategy state는 변경하지 않고 공통 display mapper가 한국어 label과 semantic tone을 함께 반환한다. 새로운 raw state는 그대로 표시하고 neutral tone을 사용한다.

| Raw state | Display | Tone |
|---|---|---|
| `WAITING_ENTRY`, `ENTRY_SIGNALLED` | 진입 대기, 진입 신호 | Success / Green |
| `POSITION_OPEN` | 보유 중 | Info / Blue |
| `PYRAMID_WAITING` | raw fallback (향후 Backend 상태) | Warning / Amber |
| `PYRAMID_ADDED` | 추가매수 완료 | Indigo |
| `OVERNIGHT_REVIEW` | 익일 보유 검토 | Warning / Amber |
| `OVERNIGHT_HELD`, `DAY2_ACTIVE` | 익일 보유, 2일차 보유 | Info / Blue |
| `EXIT_SIGNALLED` | 청산 신호 | Warning / Amber |
| `EXITED` | 청산 완료 | Neutral |
| `SAFE_MODE`, `BLOCKED` | 안전·차단 상태 | Warning / Amber |
| `HALTED`, `ERROR`, `FORCE_STOPPED` | 중지·오류 상태 | Danger / Red |
| Unknown | raw value | Neutral |

## Currency Presentation

채택 후보 Human Review는 기존 `table-wrap`, compact `StatusBadge`, Drawer,
blue action, green approve, red reject를 재사용한다. 채택 후보는 success,
검토 필요는 warning, 제외는 danger-subtle 의미이며 순위 상승/하락/유지는
success/warning/neutral로 표시한다. 순위 열과 Drawer 헤더 순위는 기존 rank
tone인 primary blue(`text-primary`)를 재사용하며 새 색상을 추가하지 않는다.
순위 header는 한 줄로 유지하기 위해 `whitespace-nowrap`을 사용한다. `risk_score`
label은 `안전도`이고 높을수록 안전하다. 단기 가격 상태는 `판단 요약`과
동일한 `border-line` compact grid를 사용하고 양수·음수 tone은 Candidates와
같은 `signedMetricTone` / `rvolTone` helper를 재사용한다. 좁은 화면에서는
표를 수평 스크롤하고 핵심 강점·핵심 주의 열만 `lg` 미만에서 감추며 Drawer는
전체 폭과 sticky 최종 결정 footer를 유지한다. 별도 색상 token이나 tab
system은 추가하지 않는다.

### Money 표현 규칙

USD는 미국주식 계좌와 거래가격의 기준 통화다. 환율은 FIXED이며 단일 원본은 `frontend/lib/fx.ts`다(정책은 `docs/FRONTEND_V1.md`의 Currency / FX). 화면·컴포넌트·Mock에 환율 숫자를 따로 두지 않는다.

#### Stock Price: USD only

```text
$5.72
```

- 대상: 현재가, 진입가, 청산가, Stop, Bid, Ask, Scanner price, 평균단가, 개별 fill 가격
- KRW를 병기하지 않는다.

#### Money: USD Primary + KRW Secondary

```text
$7,957.04
≈ ₩10,742,000
```

- 대상: 현재 자산, 투자 중, 보유 현금, 평가 손익, 실현 손익, 거래 손익, 순손익, 평균 이익, 평균 손실, Equity
- USD가 시각적으로 더 강해야 한다. USD는 해당 위치의 기본 숫자 스타일(primary text, 손익이면 success/danger tone)을 쓴다.
- KRW는 USD 아래 줄의 더 작은 `text-muted` secondary text다. primary text로 승격하지 않는다.
- 신규 화면은 `components/money.tsx`의 `<Money>`를 사용한다. 크기는 위치별로 고정한다.

| size | 위치 | KRW 줄 | 형식 |
|---|---|---|---|
| `card` | 계좌 카드 | `text-sm font-medium text-muted` | `≈ ₩10,742,000` |
| `figure` | KPI 값 | `text-xs font-medium text-muted` | `≈ ₩742,000` |
| `cell` | Table cell | `text-[11px] text-muted` | `₩742,000` (행 높이를 위해 `≈` 생략) |

- 한 줄 helper text는 `moneyText()`의 `$7,428.92 (≈ ₩10,000,000)` 형식을 사용한다.
- 부호 있는 손익은 USD와 KRW 모두 부호를 붙인다(`+$60.92` / `≈ +₩82,000`).
- 수익률, 승률, Profit Factor, 기대값, 최대 낙폭, 평균 R, 매매 횟수 같은 비율·횟수 지표에는 통화를 붙이지 않는다.
- 자산 곡선 축은 USD(`$7.5K`)로 표시하고 tooltip은 USD 위, KRW 아래로 표시한다.
- Strategy A 계좌 카드와 일별 성과 표는 `<Money>` 이전의 기존 formatter(`usdToDisplayKrw`)를 그대로 쓴다. USD 아래 더 작은 KRW 줄(`≈` 없음)이라는 위계는 같지만, 부호 있는 손익의 KRW 줄은 muted가 아니라 손익 tone을 따른다. 이 차이는 A 화면 불변 원칙에 따라 V1에서 유지한다.
- `환산 약` 접두는 사용하지 않는다.

#### Header FX

```text
USD/KRW : 1,346.09 · 고정
```

- Header 기준거래일·가상매매 시작과 같은 `text-xs text-muted` metadata 형식이며 숫자만 `tabular-nums text-foreground-secondary`다.
- 숫자는 `formatFxRate()`, 상태는 `FX_MODE_LABELS`(`FIXED` → `고정`)에서 온다.
- `현재 환율`, `실시간 환율`, `Live FX`는 사용하지 않는다. 고정값이 시세처럼 읽히면 안 된다.

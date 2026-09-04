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

- USD는 미국주식 계좌와 거래가격의 primary source currency다.
- KRW는 환산값임을 알 수 있도록 `환산 약`을 붙인 secondary display이며 primary USD보다 작은 muted text를 사용한다.
- Production KRW 표시는 Kiwoom 또는 다른 신뢰 가능한 FX source에서 변환값이 전달될 때만 활성화한다.
- Frontend에 고정 USD/KRW 환율을 두거나 누락된 값을 추론하지 않는다.
- 평균단가, 현재가, Stop 및 개별 fill 가격은 USD 중심으로 유지한다.

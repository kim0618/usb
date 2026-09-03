# USB Frontend V1

## 목적과 기술 구성

Stage 9.13.3 Frontend는 Stage 9 `/api/v1` 계약을 사용하는 한국어 중심 운영 UI다. Next.js 15, React, TypeScript, App Router, Tailwind CSS를 사용한다. Quant, GPT 분석 검증, 승인 제한, Risk, Strategy, Execution, Runtime 전환의 Source of Truth는 Backend이며 Frontend는 값을 재계산하거나 가짜 데이터로 대체하지 않는다.

## 언어 정책과 메뉴

화면명, 섹션명, 설명, 작업 버튼, Empty State, 필드명은 한국어를 우선한다. 전문 지표인 `TOP8`, `VWAP`, `ATR`, `R`은 원문을 유지하며 필요한 곳에 한글 설명을 함께 제공한다.

## Display Localization Policy

- Backend enum, DB 저장값, API request/response는 안정적인 영어 원문을 유지한다.
- 운영 UI는 공통 display mapper를 통해 상태의 한국어 label을 우선 표시한다.
- 장애 코드는 로그 검색을 위해 영어 raw code를 유지하고, 알려진 코드에는 한국어 설명을 보조 표시한다.
- Runtime 및 Strategy 상세처럼 디버깅 가치가 있는 위치만 raw enum을 작은 보조 정보나 tooltip으로 병기한다.
- 새 enum을 Frontend mapper가 아직 모르면 빈 값이나 `알 수 없음`으로 숨기지 않고 raw value를 그대로 표시한다.
- 색상은 의미를 보조할 뿐이며, 핵심 상태는 항상 text label로도 전달한다.

Sidebar는 사용자의 일일 운영 흐름에 따라 다음 4개 상위 메뉴만 표시한다.

```text
트레이딩
종목 분석
  - 후보 종목
  - GPT 분석
전략 성과
시스템
  - 시스템 상태
  - 설정
```

`/`는 Next.js App Router의 server-side redirect로 `/trading`에 이동하며, USB Logo도 `/trading`으로 이동한다. 별도 Dashboard UI는 V1 navigation에서 제거되었지만 Backend `GET /api/v1/dashboard` 계약은 유지한다. `/trading`은 Portfolio / Account primary operating view다. TOP8 상세, GPT 분석, Shadow 성과, 장애 상세와 Runtime 제어는 각각의 전용 화면에서 확인한다.

Sidebar 단순화는 업무 route 통합이 아니다. 6개 content page와 `/` redirect route를 유지하며, 분석 및 시스템의 하위 화면은 content 영역의 공통 탭으로 이동한다. `/candidates`와 `/research`에서는 분석이, `/runtime`과 `/settings`에서는 시스템이 Sidebar active 상태가 된다.

| 경로 | Sidebar 영역 | 화면 | 주요 기능 |
|---|---|---|---|
| `/` | - | Redirect | `/trading`으로 server-side 이동 |
| `/candidates` | 종목 분석 | 후보 종목 | TOP8, Quant 상세, GPT 프롬프트 미리보기/복사 |
| `/research` | 종목 분석 | GPT 분석 | 결과 입력·비교, 상세 확인, APPROVE/REJECT |
| `/trading` | 트레이딩 | 매매 현황 | 계좌 요약, 현재 포지션, 진입 대기, 통합 매매 내역 |
| `/shadow` | 전략 성과 | 전략 성과 | 7일·30일·전체 Shadow A–E aggregate 성과 비교, C 기준 전략 |
| `/runtime` | 시스템 | 시스템 상태 | 현재 상태, 미해결 장애, 운영·비상 제어 |
| `/settings` | 시스템 | 설정 | 연결 상태, 환경, compact 버전 정보 |

## 운영 흐름

후보 종목에서 프롬프트 복사 → ChatGPT 분석 → 반환 JSON 붙여넣기 → 상세 확인 → 채택/거절 순서로 진행한다. GPT API 자동 호출은 없다. Evidence는 출처 품질과 핵심 주장 근거 범위 기반 점수이며 사실일 확률로 해석하지 않는다. 최대 2개 채택 제한과 충돌 처리는 Backend 응답을 따른다.

분석 화면은 역할을 분리한다. Candidates는 Quant-first 화면으로 Quant 순위·점수, RVOL, 상대강도, 거래대금, 모멘텀과 workflow 상태를 보여준다. 큰 요약 카드 대신 거래일, 완료 시각, 전체 후보, TOP 수, Quant 버전을 compact metadata로 표시한다. RVOL은 배수, 수익률 비율은 백분율, 거래대금은 compact USD로 표시하지만 raw 값이나 계산은 변경하지 않는다. TOP 후보 전체에 회사명이 없으면 정보 가치가 없는 회사 열만 숨긴다.

Prompt 생성·미리보기·복사는 Candidates 화면의 책임이다. Research는 GPT 결과 입력 → 결과 비교 → 상세 확인 → 채택/거절만 담당하며 Prompt copy 기능을 제공하지 않는다. Research는 GPT-first 화면으로 GPT/Quant 순위, GPT 평가, Evidence와 Human 결정을 비교한다. Table은 후보 간 비교, 상세 접근, 현재 최종 결정 상태만 담당하며 직접 decision mutation을 제공하지 않는다. Symbol 또는 `상세보기`를 누르면 오른쪽 Candidate Detail Drawer가 열리고, 최종 Human decision은 Drawer의 선택 상태가 명확한 `채택 / 거절` control에서만 변경한다. 최신 분석이 있으면 결과 table을 먼저 표시하고 JSON textarea는 `분석 결과 입력` 동작으로만 펼친다. 분석이 없을 때만 import 영역을 기본 표시한다.

HumanDecision 표시 용어는 `APPROVE = 채택`, `REJECT = 거절`, decision 없음은 `미결정`이다. 이는 Frontend label이며 Backend enum과 API payload는 계속 `APPROVE / REJECT`를 사용한다. 채택은 즉시 매수가 아니라 오늘 자동매매 감시 대상으로 허용한다는 의미이고, 실제 주문은 Premarket, Opening, Strategy, Risk 조건을 추가로 통과해야 한다.

Table은 후보 간 Quant/GPT 점수 비교 화면이고, Candidate Detail Drawer는 시장 맥락, Research 근거, Human 최종 판단 화면이다. Quant/GPT 점수 grid는 의도적으로 Drawer에서 반복하지 않으며 Quant/GPT 순위만 compact하게 유지한다. Drawer는 회사 정보 → 최근 주가 흐름 → 주요 재료 → 위험 → 근거 → 전략 참고 → 확인되지 않은 항목 → 최종 결정 순서로 표시한다.

Drawer의 GPT narrative와 Sources는 Candidate Detail API에서 조회하고, Quant 지표와 현재 제공되는 회사명/시가총액은 해당 분석이 참조하는 동일 ScannerRun snapshot에서 조회한다. 최근 주가 흐름은 같은 snapshot의 기준 거래일, 최신 일봉 종가·거래량, 이미 계산되어 저장된 RVOL만 표시한다. Frozen API가 제공하지 않는 일간 등락률·고가/저가·Premarket·Postmarket 값은 Frontend에서 계산하거나 GPT narrative에서 추출하지 않고 `데이터 미제공`으로 표시한다. Frontend는 점수로 설명을 생성하거나 누락된 회사·업종·재료·위험 정보를 추론하지 않는다. 계약에 없는 다른 값은 숨기거나 `정보 없음`으로 표시한다. Drawer와 Table의 결정은 같은 Backend endpoint를 사용하며 성공 후 Backend를 다시 조회한다.

## Trading account와 portfolio

Trading 화면은 Account Summary → Current Positions → Today PnL / Entry Waiting → Unified Trading History 순서다. Backend의 Order, Fill, Trade persistence와 `GET /api/v1/trading/orders`, `/fills`, `/trades` 계약은 각각 독립된 source-of-truth로 유지한다. Frontend에서만 세 응답을 display-only canonical row로 조합하고 `매매 내역` 단일 table과 `전체 / 주문 / 체결 / 종료 매매` local filter로 표현한다. Order는 `submitted_at`, Fill은 `filled_at`을 사용해 최신순으로 정렬한다. frozen Trade 응답에는 종료 timestamp가 없으므로 Trade 시각은 `—`로 표시하고 timestamp가 있는 이벤트 뒤에 ID 기준으로 안정 정렬한다.

계좌와 포지션은 broker mode에 무관한 동일 DTO/display path를 사용하며 향후 SIMULATION, PAPER, LIVE가 같은 화면에 공급된다. Broker account/position source-of-truth가 없으면 총자산, 투자금, 현금, 손익, 수량, mark와 stop을 `—`로 표시한다. 저장된 주문·체결·종료 매매를 account truth로 확대 해석하거나, 평균단가·수량·환율로 금융 값을 재계산하지 않는다. Trade 방향·수량·통화처럼 API가 제공하지 않는 값도 다른 이벤트에서 추론하거나 환산하지 않는다. 각 history row의 상세 영역은 원본 API가 제공한 ID, 수량, 가격, 비용, PnL/R, 보유 기간과 청산 사유를 그대로 표시한다.

미국주식 계좌의 source currency인 USD를 항상 primary로 표시한다. KRW는 향후 Kiwoom 또는 신뢰 가능한 FX source가 명시적으로 제공한 환산 금액이 있을 때만 `환산 약 ₩…` 형식의 작은 secondary line으로 표시한다. 현재 API에는 trusted USD/KRW rate가 없으므로 production 화면은 KRW를 계산하거나 표시하지 않는다. Frontend formatter와 null-safe secondary component만 준비하며 고정 환율을 사용하지 않는다.

## Empty State

공통 Empty State는 작은 섹션에서 compact를 기본으로 하고, 주요 workflow 안내만 medium 크기를 선택할 수 있다. 데이터가 있으면 콘텐츠 높이에 따라 자연스럽게 확장한다. Fresh DB, 미실행, 미수신, 기록 없음은 의미에 맞는 한국어로 표시하며 demo data를 만들지 않는다. Backend 연결 실패는 현재 화면 안에서 재시도 가능하게 표시한다.

## Runtime 제어

시스템 상태 화면은 현재 health와 Runtime mode를 분리해 표시하고, 시세 데이터·주문 실행·상태 대조·시스템 신호만 compact 구성요소 표로 제공한다. 포지션과 미체결 주문은 Trading 화면의 책임이다. 장애 표는 미해결 기록을 기본으로 하며 필요할 때만 해결된 전체 이력을 펼친다. 장애의 component와 raw code는 API/history에 보존하지만 기본 표에서는 사용자 중심 유형과 내용으로 축약한다.

제어는 `시스템 제어`와 별도 `비상 중지`로 구분한다. Safe Mode는 amber, Halt/Kill은 red, Recover/Reconcile은 neutral 역할을 유지한다. 모든 mutation은 성공 뒤 Backend 상태를 다시 조회한다. 장애 해결 처리는 기록 상태만 변경한다. Kill Switch는 Runtime 화면에만 있으며 사유 입력과 청산 가능성 확인 checkbox의 2단계 확인을 유지한다.

설정 화면은 capability badge matrix 대신 키움 연결, 시장 데이터, 매매 모드를 Backend settings/capabilities 응답에서 표시한다. 환경과 `USB Internal V1`을 기본 시스템 정보로 제공하며 9개 기술 버전은 `상세 버전 보기`를 눌렀을 때만 표시한다. capability와 version API 계약은 그대로 유지한다.

## Shadow Variant

전략 성과의 사용자 UI는 최근 7일, 최근 30일, 전체 기간의 A–E aggregate 비교표를 제공하며 기본값은 최근 30일이다. 최근 기간은 현재 `America/New_York` 날짜를 포함한 calendar day 범위이고, 선택할 때 해당 시작·종료일로 Summary API를 다시 조회한다. 전체는 기간 parameter 없이 기존 all-time API를 사용한다. 완료 성과의 날짜 기준은 `exit_at`의 ET 날짜이며, 미진입 등 비종료 path는 연결된 scanner trading date로 같은 기간을 적용한다.

표는 전략 설정, 매매·미진입·승·패 횟수, 승률, 누적·평균 R, 익일 보유 횟수를 같은 열로 비교하며 C를 기준 전략으로 표시한다. 기간 변경 중에는 기존 표를 유지하고 compact loading status를 표시한다. 선택 기간에 path가 없으면 해당 기간명을 포함한 compact empty message를 표시한다. 개별 Shadow trade record와 비용·모호 봉·보유 기간 데이터, 필터 가능한 `/api/v1/shadow/trades` 계약은 디버깅·Research·향후 Paper 비교를 위해 그대로 보존하지만 primary UI에는 노출하지 않는다. A–E는 모든 Candidate path에 병렬 fan-out되므로 이 수치는 운영 전략 선택 빈도가 아니라 각 전략에서 실제 종료된 매매와 결과를 뜻한다.

Backend `VARIANT_CONFIGS`와 일치하는 사용자 설명은 다음과 같다.

| Variant | 설명 |
|---|---|
| A | 당일 청산 · ATR 1.5 |
| B | Day2 허용 · ATR 1.0 |
| C | Day2 허용 · ATR 1.5 · 기준 전략 `CONTROL` |
| D | Day2 허용 · ATR 2.0 |
| E | 당일 청산 · 구조 손절 |

Fixture와 Synthetic Replay 데이터는 구현과 전략 경로 검증용이며 실제 수익성을 의미하지 않는다. 이 기술적 semantics와 데이터는 유지하되 primary 전략 성과 UI에는 별도 badge나 경고를 렌더링하지 않는다.

## Kiwoom과 보안

키움증권은 `NOT CONNECTED` placeholder만 표시한다. API key 입력이나 연결 UI는 제공하지 않는다. USB에는 애플리케이션 로그인이 없으므로 외부 인터넷에 직접 공개하지 않고 방화벽, 사설망 또는 Reverse Proxy 인증으로 접근을 제한해야 한다. Secret은 `NEXT_PUBLIC_*`에 두지 않는다.

## 데이터와 시간

Decimal 금융 값은 문자열로 유지하며 표시용 포맷 외 계산을 하지 않는다. 시장 관련 timestamp는 America/New_York 기준으로 표시한다. Global 상태와 Trading 운영 요약의 Dashboard/Research 조회는 20초, Runtime과 Trading 실행 조회는 10초 간격으로 polling하며 수동 새로고침과 API 오류 Toast를 유지한다.

## Theme System

USB Frontend는 동일한 semantic token 구조를 공유하는 Dark/Light 두 테마를 지원한다. 기본값은 Dark이며 `data-theme="dark|light"`와 CSS variables로 적용한다. Header 우측 toggle에서 선택한 값은 `usb-theme` localStorage key에 저장되고, document head의 초기화 script가 React hydration 전에 복원하여 theme flash를 줄인다.

- Dark는 `#000000` page background, `#050505` sidebar/header, black 기반 surface를 사용한다.
- Light는 `#F7F8FA` page background와 white sidebar/header/card를 사용한다.
- Blue는 selected navigation, active tab, link, CTA 등 interaction에만 사용한다.
- Green은 success, 정상, 체결, 채택, profit에 사용한다.
- Red는 danger, reject, halt, loss에 사용한다.
- Amber는 warning, attention, safe mode에 사용한다.
- 일반 heading·숫자·본문은 primary/secondary/muted neutral text token을 사용한다.

페이지와 컴포넌트는 Tailwind palette 이름이나 임의 hex 대신 `background`, `surface`, `line`, `foreground`, `primary`, `success`, `warning`, `danger` semantic token을 사용한다. Table, Card, Modal, Drawer, Toast, Button과 Tabs는 이 공통 token을 통해 두 테마에서 같은 hierarchy와 의미를 유지한다.

## 검증

```bash
cd frontend
npm run lint
npm run typecheck
npm test
npm run build
```

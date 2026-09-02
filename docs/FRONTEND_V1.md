# USB Frontend V1

## 목적과 기술 구성

Stage 9.9 Frontend는 Stage 9 `/api/v1` 계약을 사용하는 한국어 중심 운영 UI다. Next.js 15, React, TypeScript, App Router, Tailwind CSS를 사용한다. Quant, GPT 분석 검증, 승인 제한, Risk, Strategy, Execution, Runtime 전환의 Source of Truth는 Backend이며 Frontend는 값을 재계산하거나 가짜 데이터로 대체하지 않는다.

## 언어 정책과 메뉴

화면명, 섹션명, 설명, 작업 버튼, Empty State, 필드명은 한국어를 우선한다. 전문 지표인 `TOP8`, `VWAP`, `ATR`, `R`은 원문을 유지하며 필요한 곳에 한글 설명을 함께 제공한다.

## Display Localization Policy

- Backend enum, DB 저장값, API request/response는 안정적인 영어 원문을 유지한다.
- 운영 UI는 공통 display mapper를 통해 상태의 한국어 label을 우선 표시한다.
- 장애 코드는 로그 검색을 위해 영어 raw code를 유지하고, 알려진 코드에는 한국어 설명을 보조 표시한다.
- Runtime 및 Strategy 상세처럼 디버깅 가치가 있는 위치만 raw enum을 작은 보조 정보나 tooltip으로 병기한다.
- 새 enum을 Frontend mapper가 아직 모르면 빈 값이나 `알 수 없음`으로 숨기지 않고 raw value를 그대로 표시한다.
- 색상은 의미를 보조할 뿐이며, 핵심 상태는 항상 text label로도 전달한다.

Sidebar는 사용자의 업무 흐름에 따라 다음 5개 상위 메뉴만 표시한다.

```text
대시보드
분석
  - 후보 종목
  - GPT 분석
매매
전략 성과
시스템
  - 시스템 상태
  - 설정
```

Sidebar 단순화는 route 통합이 아니다. 기존 7개 route와 직접 링크는 모두 유지하며, 분석 및 시스템의 하위 화면은 content 영역의 공통 탭으로 이동한다. `/candidates`와 `/research`에서는 분석이, `/runtime`과 `/settings`에서는 시스템이 Sidebar active 상태가 된다.

| 경로 | Sidebar 영역 | 화면 | 주요 기능 |
|---|---|---|---|
| `/` | 대시보드 | 대시보드 | 후보, 승인, 매매, 안전 상태 요약 |
| `/candidates` | 분석 | 후보 종목 | TOP8, Quant 상세, GPT 프롬프트 미리보기/복사 |
| `/research` | 분석 | GPT 분석 | JSON 불러오기, 순위·근거, APPROVE/REJECT |
| `/trading` | 매매 | 매매 현황 | 포지션, 전략 상태, 주문, 체결, 결과 |
| `/shadow` | 전략 성과 | 전략 성과 | Shadow A–E 비교, C CONTROL, 필터 |
| `/runtime` | 시스템 | 시스템 상태 | 상태, 장애, 대조, 위험 제어 |
| `/settings` | 시스템 | 설정 | 버전, 지원 기능, 연결 상태 |

## 운영 흐름

후보 종목에서 프롬프트 복사 → ChatGPT 분석 → 반환 JSON 붙여넣기 → 승인/거절 순서로 진행한다. GPT API 자동 호출은 없다. Evidence는 출처 품질과 핵심 주장 근거 범위 기반 점수이며 사실일 확률로 해석하지 않는다. 승인 개수 제한과 충돌 처리는 Backend 응답을 따른다.

## Empty State

공통 Empty State는 작은 섹션에서 compact를 기본으로 하고, 주요 workflow 안내만 medium 크기를 선택할 수 있다. 데이터가 있으면 콘텐츠 높이에 따라 자연스럽게 확장한다. Fresh DB, 미실행, 미수신, 기록 없음은 의미에 맞는 한국어로 표시하며 demo data를 만들지 않는다. Backend 연결 실패는 현재 화면 안에서 재시도 가능하게 표시한다.

## Runtime 제어

시스템 상태 화면은 `운영 제어`, `복구 및 검증`, `비상 중지 (Kill Switch)`로 구분한다. Safe Mode는 amber, Halt/Kill은 red, Recover/Reconcile은 neutral 역할을 유지한다. 모든 mutation은 성공 뒤 Backend 상태를 다시 조회한다. 장애 해결 처리는 기록 상태만 변경한다. Kill Switch는 Runtime 화면에만 있으며 사유 입력과 청산 가능성 확인 checkbox의 2단계 확인을 유지한다.

## Shadow Variant

Backend `VARIANT_CONFIGS`와 일치하는 사용자 설명은 다음과 같다.

| Variant | 설명 |
|---|---|
| A | 당일 청산 · ATR 1.5 |
| B | Day2 허용 · ATR 1.0 |
| C | Day2 허용 · ATR 1.5 · 기준 전략 `CONTROL` |
| D | Day2 허용 · ATR 2.0 |
| E | 당일 청산 · 구조 손절 |

Synthetic Replay는 구현과 전략 경로 검증용 가상 데이터이며 실제 수익성을 의미하지 않는다고 항상 표시한다.

## Kiwoom과 보안

키움증권은 `NOT CONNECTED` placeholder만 표시한다. API key 입력이나 연결 UI는 제공하지 않는다. USB에는 애플리케이션 로그인이 없으므로 외부 인터넷에 직접 공개하지 않고 방화벽, 사설망 또는 Reverse Proxy 인증으로 접근을 제한해야 한다. Secret은 `NEXT_PUBLIC_*`에 두지 않는다.

## 데이터와 시간

Decimal 금융 값은 문자열로 유지하며 표시용 포맷 외 계산을 하지 않는다. 시장 관련 timestamp는 America/New_York 기준으로 표시한다. Dashboard는 20초, Runtime과 Trading은 10초 간격으로 polling하며 수동 새로고침과 API 오류 Toast를 유지한다.

## 검증

```bash
cd frontend
npm run lint
npm run typecheck
npm test
npm run build
```

# C-M Selection Rules V1 (C-P3 사전 등록)

- 정본: `c_m_selection_rules_v1.json`
- canonical sha256 (sort_keys, 공백 없는 separators, UTF-8): `c769aea5f25bcc46cfdc40a7d74fe325b5059f630714c007d1285cb2d9865d54`
- 선언 시각: 2026-09-17 15:38 KST. C용 Massive 요청은 15:42 KST에 시작했다(선언 이후). 이 시점에 C 후보나 label을 본 적이 없다.
- 코드: `rules.DECLARED_RULES_CHECKSUM`과 다르면 `load_rules()`가 `RulesChanged`로 실행을 거부한다.
- 모든 임계값 상태: `RESEARCH_BASELINE_NOT_OPTIMIZED`. 최적화 결과가 아니다.

## 1. Hard filter

| 항목 | 값 | 근거 |
| --- | --- | --- |
| close(D) | >= $3.00 | 페니스톡 틱 크기/조작 노이즈 회피용 관행값 |
| ADV20 거래대금 | >= $5M, D-20..D-1 평균 | 소형주를 너무 일찍 잘라내지 않되 일봉 체결 가능성 최소 보장. D를 빼서 신호일 거래량으로 필터가 통과되지 않게 함 |
| 종목 유형 | CS, XNYS/XNAS/XASE/ARCX/BATS | ETF, ADR, 우선주, 워런트, OTC 제외 |

## 2. 변형 (nested family)

| 변형 | 조건 (모두 충족) | 이력 |
| --- | --- | --- |
| C-M0 Price Momentum + Volume | return_1d >= 3%, return_5d >= 5%, rvol_20 >= 2.0 | base |
| C-M1 M0 + Relative Strength | M0 + rs_spy_1d >= 3%, rs_spy_5d >= 5% | base |
| C-M2 M1 + 52W Structure | M1 + distance_52w_high >= -10% | m2 (252세션) |
| C-M3 M2 + Sector/Peer | NOT_IMPLEMENTED: PIT sector/peer 데이터 없음 | - |
| C-M4 M3 + Liquidity/Risk | NOT_IMPLEMENTED: M3 의존. 유동성은 hard filter에 이미 있음 | - |

임계값 선택 이유(결과를 보기 전):

- 1일 +3%, 5일 +5%: 일간 변동 1~3% 종목에서 "평소와 다른" 수준이면서 이미 폭등을 끝낸 종목만 남지 않는 낮은 문턱. "초기" 탐지 목적이라 상한은 두지 않았다(상한은 추가 파라미터라 V1에서 제외).
- RVOL 2.0: 20일 평균의 2배. 거래량 이상의 가장 흔한 관행 문턱.
- RS: M0의 절대 문턱을 SPY 차감 기준으로 다시 요구한다. 시장 전체 상승일의 동반 상승을 걸러내는 효과만 추가한다.
- 52W 거리 -10%: 신고가 부근 구조. 신고가 돌파 여부(breakout_52w)는 기록만 하고 규칙에 넣지 않았다.

## 3. Label과 판정

- 기준가 D+1 시가, 창 D+1..D+k, k = 1/3/5/10.
- 급등 정의 5종: 5D MFE >= 5%, 5D >= 10%, 10D >= 10%, 10D >= 15%, 10D >= 20%.
- **Primary metric: 10D MFE >= +15% matched lift.**
- 매칭 셀: 같은 날짜 x 가격 bucket(3/5/10/20/50/100) x 신호 전 ATR% bucket(0/2/3/4/6/8/12%) x ADV20 bucket(5M/20M/100M), 셀당 label 유효 대조군 5개 이상.
- 통계: 신호일 moving block bootstrap(블록 10세션, 2,000회, seed 20260917). 변형 3개 Bonferroni로 게이트 CI는 98.33%, 95%도 함께 보고.
- 평가창: primary = 세션 인덱스 251..N-10(모든 변형 공통, 약 240 신호일). secondary = 60..N-10(M0/M1만, 서술용).
- 게이트 9조건과 PASS/INCONCLUSIVE/FAIL 결정 규칙은 JSON `gate` 절 그대로.

## 4. 사후 변경 금지

결과를 본 뒤 이 파일이나 JSON을 고치면 checksum이 바뀌어 실행이 거부된다. 새 임계값은
`c_m_selection_rules_v2.json`으로 새로 선언하고, V1 결과와 별개의 연구로 기록한다.

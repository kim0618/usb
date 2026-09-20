# Strategy C-4 CONTEXTUAL_ANALOG_MOMENTUM_V0: Concept

작성 2026-09-20. 상태: 선언 동결(`c4_internal_rules_v1.json`, canonical `c4f1d243…`).
이 문서는 개념과 경계를 적는다. 판정과 수치는 `C4_INTERNAL_SCREEN_V1.md`에 있다.

```text
C-1 / C-M      = CLOSED, FAIL   (변경 없음)
C-2 / C-E0     = CLOSED, FAIL   (변경 없음)
C-3 / EQM-V0   = CLOSED, FAIL   (변경 없음)
D              = 별도 Chart Analog 라인 (규칙·코드 불변)
C-4            = 네 번째 가설, research_id c4-analog-v0
```

## 1. 가설

> C-M0가 찾아낸 "크게 움직일 수 있는 종목" 중에서, 현재의 시장행동·이벤트·이벤트품질·거래량흐름·시장맥락을
> 한 좌표로 놓았을 때 그 좌표와 가장 가까웠던 과거 상황 50개의 실제 5일 초과수익 분포가,
> 이 후보의 향후 5일 방향을 선별하는가. 그리고 그 정보가 시장좌표만 쓴 F0보다 추가적인가.

역할 분담을 고정한다.

```text
C-M0  = Candidate Generator   (임계값 불변, 재조정 금지)
C-4   = Direction Selector    (후보 안에서의 순위)
```

## 2. 메커니즘

```text
Query  = frozen C-M0 후보 (D, ticker)
           ↓  좌표 x_D  (Layer A~E, 전부 [0,1], 등가중)
Library = base-eligible 전 행 (후보에 한정하지 않음), analog_date <= D - 20
           ↓  Euclidean 최근접, 동일 ticker/FIGI 제외, ticker cap 1, analog_date cap 5
Top-50  → analog_median_excess_5d = median(이웃의 5D 초과수익)
           ↓
평가     = 같은 날 후보를 이 값으로 정렬했을 때 실제 5D 초과수익과의 Spearman IC
```

예측값은 이웃 결과의 **중앙값 하나**다. weighted sum도, composite score도, 학습된 가중치도 없다.
C에서 극단 상승 몇 건이 평균을 끌어올렸기 때문에 median을 Primary로 고정한다.

## 3. C-1~3과 무엇이 다른가

| | C-1 | C-2 | C-3 | C-4 |
| --- | --- | --- | --- | --- |
| 판단 | 고정 임계값 AND | 이벤트 유무 코호트 | 매출 YoY bucket 코호트 | 유사 과거 50건의 실제 결과 분포 |
| Feature 역할 | 규칙 입력 | 코호트 라벨 | 코호트 라벨 | **거리 좌표** |
| 출력 | 후보/비후보 | 코호트 | 코호트 | 연속 예측값, 후보 내 순위 |
| 평가 | 매칭 통제군 초과 | 코호트 차이 | 코호트 차이 | 순위상관(IC), 분위 스프레드 |

세 실패에서 배운 것을 설계에 직접 넣었다.

1. C-1은 MFE·MAE가 함께 커지고 종가 초과가 0이었다 → 게이트에 MAE 조건(C6)과 "MFE만 개선이면 FAIL"(C5)을 둔다.
2. C-1 V2B는 C-M0가 방향이 아니라 변동성 군집을 탐지한다고 결론지었다 → **D일 True Range와 ATR 확장을 좌표에 넣어**
   이웃이 같은 변동성 상태에서 뽑히게 한다. C 종료문서가 변동성 재연구의 최소요건으로 요구한 "D일 TR을 매칭에 넣는다"와
   같은 방향이다.
3. C-2는 이벤트 존재가 오히려 M_ONLY보다 나빴다 → 이벤트를 positive로 읽지 않는다. 좌표로만 쓰고, baseline에 이벤트 유무를 둔다.
4. C-3은 8-K에 XBRL fact가 0건이고 규모는 정기보고서 매출 YoY만 가능하다고 확정했다 → Layer C는 그것만 쓴다.
   계약금액·계약기간·질적 중요도는 제외한다. 텍스트를 읽지 않는다.

## 4. Strategy D와 무엇이 다른가

```text
D   = CHART ANALOG      가격 시계열 창의 모양
C-4 = SITUATION ANALOG  상황을 요약한 스칼라 좌표
```

| 축 | D (`HISTORICAL_ANALOG_V1`) | C-4 |
| --- | --- | --- |
| 매칭 대상 | 길이 W+1 z-경로 / 길이 W 누적로그수익 경로 (W=20/40/60) | 37차원 스칼라 맥락 벡터 |
| 거리 | Pearson(A) / Euclidean(B), 경로 위 | Euclidean, rank 벡터 위 |
| Query | 매일 유니버스에서 해시 무작위 300종목 | **C-M0 후보 행** |
| Library | stride 5 세션, horizon별 별도 | 매 세션, 5D·10D 라벨 유효 행 전체 |
| Embargo | `d + h <= D - W` | `d <= D - 20` |
| 시장 외 정보 | 없음 | SEC 이벤트, XBRL 매출, OBV, VWAP, SPY |
| 질문 | 이 차트 다음엔 무엇이 왔나 | 이 상황 다음엔 무엇이 왔나 |

**중첩 경고를 게이트로 만들었다.** D의 baseline N2a는 5개 스칼라 피처의 per-date 백분위 위에서 같은 cap·embargo로
kNN을 돈다. C-4의 F0는 그것과 구조가 거의 같다. 그래서 조건 C10은 `IC_F3 > IC_F0`이고 그 차이의 부트스트랩 하한도
0을 넘어야 한다. F3가 F0를 못 넘으면 이벤트·품질·OBV 맥락을 얹은 C-4를 독립 전략으로 유지할 근거가 없다.

## 5. 네임스페이스 분리

| 항목 | 값 |
| --- | --- |
| strategy_id | `CONTEXTUAL_ANALOG_MOMENTUM_V0` |
| 선언 | `docs/backtest/strategy_c4_analog/c4_internal_rules_v1.json` (`c4f1d243…`) |
| 코드 | `backend/app/backtest/strategy_c4_analog/` |
| 테스트 | `backend/tests/strategy_c4_analog/` |
| 결과 | `data/runtime/strategy_c4/` |
| 증분 저장소 | `data/runtime/strategy_c4/{sec,xbrl}` (동결 저장소는 읽기만) |
| 판정 | `C4_INTERNAL_SCREEN = PASS / FAIL / INCONCLUSIVE` |

D 패키지는 한 줄도 바꾸지 않았고 한 줄도 import하지 않았다(테스트가 강제한다). D의 `code_digest`는 패키지 전체
`*.py` 해시이므로 파일 하나만 추가해도 진행 중이던 D4 run identity가 깨진다. C-4는 필요한 유틸을 자체 구현했다.

## 6. 이번 판정의 지위

```text
C4_INTERNAL_SCREEN = PASS   -> Forward Shadow로 끌고 갈 가치가 있다
C4_INTERNAL_SCREEN = FAIL   -> C-4 종료. 2027년을 기다리지 않는다
C4_INTERNAL_SCREEN = INCONCLUSIVE -> 수익률이 아니라 커버리지·매핑·데이터 무결성 문제
```

PASS라도 최종 알파 승인이 아니다. Entry/Exit/Risk/Cost/Backtester는 범위 밖이고, 최종 OOS 게이트는
Forward Shadow(2026-09-17 이후 신호일 120일, 라벨 완결 2027-03-25 이후)에서만 판정한다.

FAIL이면 K 변경, 가중치 변경, 피처 삭제, 이벤트 클래스 선별, horizon 교체로 되살리지 않는다.
다른 가설은 새 연구 ID와 새 선언으로만 시작한다.

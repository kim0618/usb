# AOA Expert Execution Research Plan V1 (E1 후보)

작성 2026-09-27. E0 결과(`AOA_E0_PREFLIGHT_REPORT_V1.md`)를 바탕으로 한 **제안**이다. 실행 여부와 범위는 사용자가 정한다.
전략 구현·AUTO·Score·Probability·운영 계좌 연결은 E1에서도 범위 밖이다.

---

## 0. 이 원장으로 할 수 있는 것과 없는 것

할 수 있는 것:
- 한 숙련 계좌가 **어떻게 실행했는가**를 fill 단위로 정확히 기술 (E0로 회계 검증 완료)
- 그 행동 기술을 시장 경로와 붙여 "진입 직후 가격이 어떻게 움직였나", "maker 체결 뒤 역선택이 있었나"를 측정
- US-B가 막힌 지점(taker 비용)과 이 계좌의 실행 비용 구조 비교

할 수 없는 것:
- 이 행동이 **좋은 전략인지** 판정 (표본 1, 생존편향, 실패 계좌 없음)
- 이 사람의 신호를 역설계해 US-B 전략으로 옮기기 (신원 미확인, 2018~2021 BitMEX 구조, 대화형 판단)
- "무손절이 좋다", "물타기가 좋다" 같은 결론. 원장은 그 행동을 한 계좌 중 **살아남은 하나**만 보여 준다

## 1. 우선순위

| 순위 | 과제 | US-B 관련성 | 필요 데이터 |
|---|---|---|---|
| 1 | **maker 체결의 역선택 측정** | D5·D5.1 CASE B가 "maker로 가면 비용이 풀린다"는 가정 위에 있음. 실제 숙련 계좌의 maker fill 뒤 가격 경로로 그 가정의 크기를 잰다 | quote(best bid/ask) + trade, XBTUSD 2019~2021 |
| 2 | 외부 claim 1·3·4·6·9 재현 | 외부 분석의 신뢰도 확정. 정의 후보를 먼저 동결 | 1m OHLCV (trade 기반), mark/index |
| 3 | drawdown case study 3건 | 손실 사건에서 크기·반전·레버리지 경로 (기술 통계) | 1m mark |
| 4 | 2018 → 2021 행동 변화 기술 | 보유시간 95배, 주문당 fill 15배, 연손익 11배 변화의 동행 지표 | 원장 + 1m |

1번이 가장 직접적이다. E0에서 이 계좌는 거래가치의 71%를 maker로 체결했고 순수수료가 편도 0.44bp다.
그러나 **maker 체결은 공짜가 아니다**: 지정가가 체결되는 순간은 대개 가격이 그 방향으로 불리하게 움직일 때다.
원장 fill 시각 + 당시 호가 + 이후 1~60초·1~30분 mid 경로로 "리베이트 + spread 이득 - 역선택 손실"을 분해할 수 있다.
이 값이 D5.1이 가정한 maker 왕복 약 4bp와 비교해 어떤지가 US-B에 주는 핵심 정보다.

## 2. E1-A 시장데이터 수집 (선행)

| 항목 | 내용 |
|---|---|
| 소스 | `https://s3-eu-west-1.amazonaws.com/public.bitmex.com/data/trade/YYYYMMDD.csv.gz`, `.../data/quote/YYYYMMDD.csv.gz` (E0에서 2018-03-01·2021-12-31 존재 확인) |
| 기간 | 2018-03-01 ~ 2022-01-01 (원장 앞뒤 여유) |
| 심볼 | XBTUSD 필수(가치 91.8%), ETHUSD 권장(6.5%). 나머지는 0.2% 이하라 제외 |
| 규모 | 원본 trade 약 21GB + quote 비슷 (하루 9~20MB gz, 전 심볼). 심볼 필터 후 1m 집계는 수백 MB |
| 저장 | `data/research/expert_execution/market/` (git 제외). 기존 D2 `data/market`·`data/runtime/crypto`와 분리 |
| mark / index | **소스 미확정**. 공개 덤프 목록에서 mark 또는 `.BXBT` index 이력 존재 여부를 먼저 확인. 없으면 funding 행 mark(8h)만 사용하고 그 한계를 문서화 |
| 검증 | 원장 fill 가격이 같은 시각 공개 trade 범위 안에 있는지 전수 대조 (원장과 시장데이터의 시간축 일치 확인) |

## 3. E1-B claim 재현 규칙 (사전 동결 항목)

외부 정의를 모르므로 계산 전에 아래를 문서로 동결하고 hash를 남긴다.

| claim | 동결할 정의 후보 |
|---|---|
| 1 | "시장가 진입" = (a) `ordtype == Market` (b) 주문 taker 비중 > 50%. 직전 60분 수익률 = 진입 첫 fill 직전 완결 1m 봉 기준 60개 |
| 3·4 | "진입" = OPEN+ADD 주문 첫 fill. 4h forward = 첫 fill 이후 첫 1m 봉 open부터 240분. 방향 부호 적용. hit = 부호 반영 수익률 > 0 |
| 6 | 1m mark 경로에서 포지션 보유 분 중 미실현 < 0 비중. 만기 선물 포함 여부 둘 다 |
| 9 | 일별 1m realized vol 3분위, 분위별 일 실현손익 (XBT, 자본 대비 %) |
| 2 | mark 기준 재판정. E0 체결가 기준 69.1%와 비교 |

PIT 규칙은 `alignment.py`에 이미 고정했다: 이벤트는 자신이 속한 봉을 보지 않고(`last_closed_bar_index`),
forward는 이벤트 이후 봉부터(`first_forward_bar_index`). 테스트 `test_alignment_never_uses_the_bar_containing_the_event`.

## 4. E1 산출물 제안

- `docs/crypto/expert_execution/AOA_E1_CONTRACT_V1.md` (정의 동결, sha256)
- `AOA_E1_MAKER_ADVERSE_SELECTION_V1.md` (1순위 결과)
- `AOA_E1_CLAIM_REPRODUCTION_V1.md`
- `AOA_E1_DRAWDOWN_CASES_V1.md`

## 5. STOP 조건

아래면 E1을 하지 않는 것이 맞다:
- US-B가 maker 실행(지정가 호가 관리)을 앞으로도 검토하지 않는다. 그 경우 이 원장이 US-B에 주는 가치는 "비용 구조가 한 자릿수 bp가 아니라 1bp 미만일 수 있다"는 E0 사실 하나로 끝난다.
- mark/index 소스가 없고, trade·quote만으로 1순위 과제를 할 수 없다고 판단될 때 (1순위는 quote만으로 가능하므로 이 조건은 2순위 이하에만 해당).

## 6. 기록해 둘 편향

| 편향 | 내용 |
|---|---|
| 생존편향 | 성공 계좌 하나만 공개됐다. 공개 자체가 성공했기 때문에 일어난 사건이다 |
| 선택편향 | 공개자가 기간(2018-03 ~ 2021-12)을 골랐다. 이전·이후 성과는 없다 |
| 신원 | 원장과 공개자의 연결을 독립 확인할 방법이 E0에는 없다 |
| 시대 | BitMEX 점유율이 높던 시기, maker 리베이트 존재, inverse 계약. 2026 Bybit USDT linear와 체결 경제학이 다르다 |
| 다중 비교 | claim 재현에서 정의 후보를 여럿 계산하면 우연히 맞는 정의가 생긴다. 판정은 동결한 1차 정의로만 한다 |

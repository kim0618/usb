# USB — V2 / V3 Backlog

이 문서는 V1 Scope 밖의 아이디어를 기록하기 위한 Backlog다.

중요:

> Backlog에 있다고 V1에서 구현하지 않는다.

V2는 V1 Shadow/Paper 데이터가 충분히 쌓인 뒤 실제 결과를 근거로 결정한다.

---

# V2 후보

## 1. GPT API 자동 Research

V1:

```text
Prompt Copy
→ ChatGPT
→ JSON
→ Import
```

V2 후보:

```text
Bot
→ OpenAI / Claude API
→ Research
→ JSON
→ Rank
```

유료 비용이 발생하므로 V1에서 가치가 검증된 후 도입한다.

---

## 2. Strategy 분리

Catalyst 유형별로 별도 전략을 검토한다.

후보:

- Earnings Momentum
- Guidance Revision
- Major Contract
- News Catalyst
- Sector Sympathy
- Gap Continuation

각 전략은 별도 성과를 가져야 한다.

---

## 3. 재진입

V1:

```text
1 symbol / day / 1 entry
```

V2:

- Stop 후 VWAP Recovery
- 새로운 High Breakout
- Volume Re-expansion

등 조건부 재진입 연구.

---

## 4. Multi-step Pyramiding

V1:

```text
추가매수 최대 1회
```

V2:

```text
Initial
→ Add 1
→ Add 2
```

승자만 강화한다.

---

## 5. Advanced Trailing

V1:

```text
ATR Profile
```

V2 후보:

- ATR + VWAP
- ATR + Swing Low
- ATR + Volume Exhaustion
- Lower High
- Momentum Decay

---

## 6. Holding Period Extension

V1:

```text
Max Day 2
```

V2 후보:

- 3 Day
- 5 Day
- Catalyst별 보유기간

---

## 7. ML Ranking

V1에서 충분한 자체 Dataset 축적 후 검토.

후보 Feature:

- Quant components
- GPT scores
- Evidence Confidence
- Premarket Gap
- Opening Structure
- ATR
- Sector
- Catalyst type

후보 Model:

- Logistic Regression
- XGBoost
- LightGBM

ML은 V1 데이터 없이 선행 개발하지 않는다.

---

## 8. Automatic Approval

충분한 거래 표본에서 특정 조건의 기대값이 검증된 경우:

```text
Quant
+
GPT
+
Evidence
+
Premarket
```

조합을 자동 승인하는 기능 검토.

---

## 9. Shorter Timeframe Exit

10s / 15s 등 초단위 데이터를 통한 ambiguous bar 해소.

V1 필수 아님.

---

## 10. PostgreSQL

다음 조건에서 검토:

- 다중 서버
- 다수 Worker
- 실시간 대량 이벤트
- 여러 사용자
- SQLite write contention 증가

---

# V3 후보

## 1. Selective Full Auto

검증된 조건에 한해서 Human Approval 제거.

---

## 2. Multi Strategy Portfolio

여러 독립 전략을 동시에 운용하고 Portfolio Risk를 통합 관리.

---

## 3. Multi Broker

- Kiwoom
- KIS
- IBKR

등 Broker Adapter 추가.

---

## 4. Multi Asset

공통 Engine을 활용하여:

- Crypto
- Futures

확장 검토.

---

## 5. Portfolio-level ML / Allocation

전략별 Edge, Correlation, Drawdown을 이용한 자본 배분.

---

# Backlog 운영 규칙

- 새로운 아이디어는 V1 코드에 바로 추가하지 않는다.
- 이 문서에 먼저 기록한다.
- V2 항목은 V1 데이터를 근거로 채택/폐기한다.
- 성과가 좋아 보인다는 이유만으로 Variant를 중간 변경하지 않는다.

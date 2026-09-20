# EQM-V0 데이터 실현가능성 (EQM-P1)

작성 2026-09-20. **어떤 forward return도 읽기 전에** 수행한 read-only 실측이다.
질문은 하나다. Q1: 이벤트 중 경제적 규모를 정량화할 수 있는 이벤트가 실제로 존재하는가.

결론부터 적는다.

```text
Q1 = 부분적으로 YES

계약 금액 / 거래 금액 / 계약 기간   → 구조화 불가 (UNKNOWN)
실적 규모 (매출·손익·EPS)          → 구조화 가능, 단 정기보고서(E3b)에 한정
```

따라서 EQM-V0의 magnitude는 **정기보고서가 동반된 Early Momentum**에만 정의된다. 이 범위 제한은
결과를 보기 전에 데이터가 정한 것이며, 결과를 보고 고른 event class가 아니다.

## 1. 표본 구성 (결정적 추출)

C-E0 `candidate_status.parquet`(6,680행, run `ce01-234478e8f7ff27570e13`)에서 `status = EM`인
2,550행을 뽑고, `signal_date, ticker`로 정렬한 뒤 균등 stride로 추출했다. 난수는 쓰지 않았다.

| 표본 | 모집단 | 추출 |
| --- | --: | --: |
| E3b (W_PRIMARY에 10-Q/10-K/10-KT) | 1,133 | 40 |
| E1 (W_PRIMARY에 8-K 1.01) | 193 | 25 |
| 표본이 덮는 고유 CIK | | 63 |

63개 CIK의 `data.sec.gov/api/xbrl/companyfacts/CIK##########.json`를 가져왔다. HTTP 63건 전부 200,
재시도 0, 원본 144.9MB(중앙값 저장 0.2MB/CIK, gzip). 5 req/s, User-Agent는 C-E0와 같은 실연락처다.

## 2. 실측 1: 8-K에는 XBRL 수치가 없다

| 대상 | 건수 | XBRL fact를 가진 accession |
| --- | --: | --: |
| 8-K 1.01 (중요 계약), 표본 행의 W_PRIMARY | 24 | **0** |
| 8-K 2.02 (실적 발표), 표본 CIK의 연구구간 전체 | 251 | **0** |

8-K은 재무제표 XBRL 태깅 대상이 아니므로, 계약 금액도 실적 수치도 accession에 붙어 오지 않는다.
숫자를 얻으려면 EX-10 계약서나 EX-99 보도자료 **본문을 읽어야** 하고, 그것은 이 연구가 금지한
event text 의미 추론이다. 정규식으로 달러 금액을 긁는 방법도 (a) 총계약액·연간액·최대치·옵션
포함액을 구분할 수 없고 (b) 정답 라벨이 없어 coverage를 검증할 수 없어 채택하지 않는다.

**따라서 `contract_value`, `transaction_value`, `duration_months`, `is_multi_year`,
`is_recurring`, `is_new_customer`는 V0에서 전부 `UNKNOWN`이다.**

표본 수로도 이미 막힌다. E1 모집단은 193행으로 사전등록 coverage 하한(material 후보 300)에
미달하고, E2(8-K 2.01)는 EM 전체에서 14행이다. 금액을 읽어낼 수 있었더라도 E1/E2 단독으로는
primary 가설을 지지할 표본이 되지 않는다.

## 3. 실측 2: 정기보고서에는 규모가 구조화되어 있다

E3b 표본 40행 각각에 대해, W_PRIMARY에 접수된 10-Q/10-K accession이 companyfacts에서 **그
accession으로 태깅한 fact**를 갖는지 확인했다.

| 항목 | 40행 중 |
| --- | --: |
| 해당 accession의 fact 존재 | **40** |
| 매출(us-gaap 5개 태그 중 하나) | 39 |
| 같은 accession 안의 분기 YoY 비교기간 | 32 |
| 같은 accession 안의 연간 YoY 비교기간 | 8 |
| 순이익(NetIncomeLoss/ProfitLoss) | 40 |
| EPS | 38 |
| 매출총이익(GrossProfit, 마진용) | 23 |
| dei 발행주식수(시가총액용) | 34 |

핵심은 **비교기간이 같은 accession 안에 함께 온다**는 점이다. 10-Q는 전년 동기를, 10-K는 전년도를
같은 제출물에 태깅한다. 그래서 YoY 성장률을 계산할 때 나중에 제출된 어떤 문서도 필요하지 않고,
사용한 모든 fact의 공개 시각이 그 accession의 `acceptanceDateTime` 하나로 확정된다. 이것이
EQM-V0가 PIT를 지킬 수 있는 구조적 이유다.

### 제안 알고리즘과 그 실측 coverage

```text
1. 이벤트 accession이 태깅한 USD fact만 읽는다 (다른 제출물의 fact는 읽지 않는다)
2. 매출 태그 우선순위: RevenueFromContractWithCustomerExcludingAssessedTax
   > ...IncludingAssessedTax > Revenues > SalesRevenueNet > RevenuesNetOfInterestExpense
3. 기간 길이 bucket(분기 80~100일, 반기, 3분기 누계, 연간 350~380일)별로
   end가 가장 최근인 기간을 현재기간으로, end가 355~375일 이전인 같은 길이 기간을 전년 동기로 삼는다
4. revenue_growth_yoy = 현재기간 매출 / 전년 동기 매출 - 1
   전년 동기가 0 또는 음수면 ZERO_BASE / NEG_BASE로 기록하고 성장률을 만들지 않는다
```

| 결과 | 40행 중 |
| --- | --: |
| `revenue_growth_yoy` 계산 성공 | **39** |
| 비교기간을 찾지 못함 | 1 |
| 사용된 기간 길이 | 분기 32, 연간 7 |

성장률 분포(표본 39): 최소 -32.6%, p10 -6.0%, p25 +2.2%, **중앙값 +12.2%**, p75 +25.6%,
p90 +45.4%, 최대 +4,158%. 경계별 비중은 `+10% 이상` 21/39, `+25% 이상` 11/39, `+50% 이상` 3/39,
`+100% 이상` 2/39이다. 이 분포는 bucket 경계와 coverage 하한을 **표본 수 기준으로** 정하기 위한
것이며, 어떤 수익률과도 대조하지 않았다.

## 4. 실측 3: TTM 매출은 V0에서 쓰지 않는다

`event_value_to_ttm_revenue`를 계산하려면 TTM 매출이 필요하다. 신호일 이전에 제출된 fact만으로
연속 4개 분기 매출을 조립할 수 있는지 확인한 결과 **40행 중 2행**뿐이었다. 대부분의 filer가
분기 대신 누계(6개월, 9개월) 기간으로 태깅하기 때문이며, 차분으로 분기를 복원하는 것은 가능하지만
결측 조합마다 규칙이 갈라져 coverage를 사전에 보장할 수 없다.

애초에 TTM의 용도는 계약 금액의 분모였고 그 분자가 §2에서 사라졌으므로, **TTM 매출과
`event_value_to_ttm_revenue`, `event_value_to_market_cap`은 V0 Primary에서 제외한다.**
V0의 규모는 비율이 아니라 **YoY 성장률**로 정의한다.

## 5. Feature feasibility 표 (사전등록 확정본)

| Feature | Source | Available | Historical | PIT | Coverage (표본) | EQM-V0 |
| --- | --- | --- | --- | --- | --- | --- |
| Contract value | 8-K 1.01 본문 | NO | - | - | 0/24 | **제외** |
| Transaction value | 8-K 2.01 본문 | NO | - | - | 표본 14행 | **제외** |
| Duration / multi-year | 계약서 본문 | NO | - | - | - | **제외** |
| TTM revenue | XBRL 분기 조립 | 부분 | YES | YES | 2/40 | **제외** |
| Market cap (PIT) | dei 주식수 × 종가 | 부분 | YES | YES | 34/40 | 기술통계 |
| **Revenue YoY growth** | XBRL 같은 accession 비교기간 | YES | YES | **YES** | **39/40** | **Primary magnitude** |
| Net income (YoY/부호) | XBRL | YES | YES | YES | 40/40 | 보조 feature |
| EPS | XBRL | YES | YES | YES | 38/40 | 기술통계 |
| Gross margin | XBRL GrossProfit | 부분 | YES | YES | 23/40 | 기술통계 |
| Financing / dilution / distress flag | C-E0 taxonomy E4 | YES | YES | YES | 6,680/6,680 | **Risk filter** |
| Novelty proxy (90d/180d 동일 클래스 건수) | C-E0 이벤트 저장소 | YES | YES | YES | 전수 | Raw feature만 |

## 6. Q1 판정과 범위 선언

```text
Q1 = 부분 YES
MAGNITUDE OBSERVABLE CLASS = E3b (10-Q / 10-K / 10-KT)
MAGNITUDE = revenue YoY growth (same-accession comparative)
E1 / E2 / E3a / E5 MAGNITUDE = UNKNOWN
```

여기서 정직하게 적어 둔다. EQM-V0가 검증할 수 있는 것은 "Event Quality 일반"이 아니라
**정기보고서가 동반된 급등의 실적 규모**다. 최종 보고서는 이 범위를 결론 문장에 그대로 달고,
`Event Quality가 Selection Alpha를 더한다`는 일반 명제로 확대해 쓰지 않는다.

또 하나. E3a(8-K 2.02 실적 발표)에는 수치가 붙어 오지 않으므로, 8-K 2.02 이벤트의 규모를 나중에
제출된 10-Q에서 끌어오는 것은 **look-ahead**다. EQM-V0는 그 경로를 구조적으로 차단한다.
사용하는 fact는 이벤트 accession이 직접 태깅한 것뿐이다.

## 7. 모집단 크기 (사전등록 coverage 하한의 근거)

| 구분 | 행 | 고유 ticker |
| --- | --: | --: |
| C-M0 primary 후보 | 6,680 | 2,300 |
| M_ONLY | 3,552 | - |
| EM (Event Presence) | 2,550 | 1,535 |
| EM ∩ E3b (EQ1 모집단 상한) | **1,133** | **807** |
| EM ∩ E3b ∩ recent_dilution_20 | 25 | - |
| EM ∩ E1 | 193 | - |
| EM ∩ E2 | 14 | - |

EQ1 상한 1,133행에 §3의 성공률 39/40을 적용하면 약 1,100행이다. material bucket 경계를 어디에
두느냐에 따라 EQ2는 이보다 작아지며, 정확한 크기는 전수 추출(EQM-P3) 후 coverage 게이트(EQM-P4)
에서 확정한다. 그 숫자로 `eqm_v0_rules_v1.json`의 하한을 동결한 뒤에야 수익률을 읽는다.

## 8. 수집 범위

- 대상: C-E0 status 표에서 W_PRIMARY에 E3b를 가진 모든 행의 CIK **834개**
- 엔드포인트: `data.sec.gov/api/xbrl/companyfacts/` 1 CIK당 1요청, 5 req/s
- 저장: `data/runtime/strategy_eqm/v0/raw/companyfacts/` provider bytes gzip + 요청 원장
- SEC submissions는 **재수집하지 않는다**. C-E0 저장소를 그대로 읽는다

# C4 데이터 타당성 (읽기 전용 감사 + 증분 수집)

작성 2026-09-20. 이 문서의 수치는 전부 **수익률을 보기 전**에 측정했다.

## 1. 가격 그리드

| 항목 | 값 | 근거 |
| --- | --- | --- |
| 공급자 | Massive Stocks Basic, 롤링 2년, T-1, `adjusted=false` | `docs/MASSIVE_DATA_SOURCE_SPIKE.md`, `COMMON_HISTORICAL_STORE_V2.md` |
| usable grid | **2024-09-17 ~ 2026-09-16, 501 XNYS 세션**, 내부 결손 0 | `strategy_c/raw/grouped` 502파일 중 2024-09-16은 403 stub |
| 세션당 종목 | 5,053 ~ 5,242 (median 5,144) | C-M run `data_audit` |
| 패널 | 6,341 ticker (CS 스냅샷 합집합 + SPY) | |
| 2024-09 이전 | **없음.** 로컬·Drive·API 어디에도 없고 API 재요청은 403 또는 무음 truncation | |

이 501세션이 내부 스크리닝의 전부다. 추가 유료 데이터는 사지 않는다.

## 2. 아날로그 라이브러리 모집단

라이브러리 행 = base-eligible ∧ ¬ca_excluded ∧ 5D·10D 라벨 유효 ∧ 모든 연속 좌표 존재, 세션 60 이상.

| 항목 | 값 |
| --- | --- |
| 맥락 이전 라이브러리 행 | **1,102,275** |
| 고유 ticker | 3,961 |
| 고유 날짜 | 431 (2024-12-11 ~ 2026-09-01) |
| CIK 매핑된 행 | 1,101,626 (**99.94%**) |
| FIGI 있는 행 | 1,001,677 (90.87%) |
| 필요한 고유 CIK | **3,873** |

선언된 커버리지 하한(행 50,000 / ticker 1,000 / 날짜 200)은 맥락을 붙이기 전부터 큰 여유로 충족된다.

## 3. `vw` (세션 VWAP) 커버리지

grouped daily 원시 행에는 `vw`와 `n`이 들어 있고 **기존 어떤 전략도 이 두 필드를 읽지 않는다.**
라이브러리 행에서 `vw` 유효(존재하고 > 0) 비율은 **1.0000**이다. 선언한 커버리지 게이트 0.99를 통과하므로
`close_to_vwap_pct`, `open_to_vwap_pct` 두 좌표를 F3에 포함한다. 이 결정은 어떤 예측값보다 먼저 내려졌고
API 호출은 0이다.

## 4. SEC 제출물 증분 수집

C-E0 저장소는 C-M0 **primary 후보**의 CIK 2,339개만 덮는다. 라이브러리는 후보에 한정하지 않으므로
1,536개가 모자랐다.

| 항목 | 값 |
| --- | --- |
| 새 저장소 | `data/runtime/strategy_c4/sec` (동결 C-E0 저장소는 읽기만) |
| 요청 CIK | 1,536 |
| HTTP | 1,610건 전부 200, 재시도 0, 210.9MB |
| 결과 | `OK` 1,534, `TRUNCATED_PAGES` 2 |
| 새로 읽은 filing 행 | 1,276,218 |
| digest | `5afe4f4e8bdd98fb…` |

`TRUNCATED_PAGES` 2건(CIK 0000019617, 0000895421)은 창 안의 제출물이 12페이지 상한을 넘는 초대형 filer다.
두 CIK는 covered로 치지 않으므로 그 행들은 UNKNOWN 범주가 된다. 보수적 처리이고 축소하지 않는다.

최종 covered CIK = **3,873** (C-E0 2,339 + C-4 1,534).

## 5. 이벤트 파이프라인 재현 검증

C-4는 이벤트 정의를 다시 쓰지 않고 C-E0의 taxonomy·PIT 배치·윈도·우선순위를 그대로 호출한다.
그 주장을 숫자로 확인했다: **C-E0가 판정한 6,680개 후보 행에 대해 C-4 파이프라인의 코호트가 6,680/6,680 일치**했고,
알려진 행의 `event_count`도 6,324/6,324 일치했다.

| C-E0 status | 행 | C-4 결과 |
| --- | --: | --- |
| EM | 2,550 | EM 2,550 |
| EM_NEGATIVE_RISK | 222 | EM_NEGATIVE_RISK 222 |
| M_ONLY | 3,552 | M_ONLY 3,552 |
| UNKNOWN_* / EXCLUDED_MA_TARGET | 356 | UNKNOWN_OR_EXCLUDED 356 |

이 검사를 게이트에 **E9**로 추가했다. 선언이 요구하지 않는 추가 조건이므로 기준을 느슨하게 만들지 않는다.

## 6. XBRL companyfacts 증분 수집

품질 좌표는 정기보고서(10-K/10-KT/10-Q)가 W_PRIMARY에 있을 때만 관측 가능하다. 정기보고서 이벤트를 가진
CIK는 **3,387개**이고 EQM-V0 저장소에는 834개뿐이라 **2,553개**를 새로 받았다.

| 항목 | 값 |
| --- | --- |
| 새 저장소 | `data/runtime/strategy_c4/xbrl` (동결 EQM 저장소는 읽기만) |
| 대상 CIK | 2,553 |

정기보고서가 **없는** 행은 UNKNOWN이 아니다. 그 행은 `periodic_event_flag = 0`,
`quality_observable_flag = 0`으로 **관측된 사실**이다. UNKNOWN은 CIK 미매핑·저장소 미커버·acceptance 결측·
분류불가 양식·M&A 대상 핀에만 붙고, 그 경우 `event_type_UNKNOWN = 1`과 나머지 좌표의 중립값 0.5로 표현된다.
UNKNOWN을 0이나 NO_EVENT로 바꾸지 않는다.

## 7. 계산 불가로 제외한 것

| 대상 | 사유 |
| --- | --- |
| 계약 금액·계약 기간·질적 중요도 | 8-K accession에 XBRL fact가 0건(EQM-V0 확정). 텍스트 파싱은 금지 |
| REAL CVD | 체결 방향 데이터가 없다 |
| CVD_PROXY | 분봉 커버리지가 시장 전체에서 일관되지 않다(30종목 2년 + 3,883종목 4개월). Primary 제외 |
| sector / peer | PIT 섹터 매핑이 없다(C PIT 계약과 동일 사유) |
| 52주 고점 거리 | C-M2에서 방향성 증분이 확인되지 않았고 252세션 warmup이 라이브러리를 반토막낸다. F0 제외 |

## 8. 최종 수치

라이브러리·쿼리 행수, 이벤트/품질 맥락 보유 행수, Top-50 확보 비율은 실행 산출물
`data/runtime/strategy_c4/runs/<run_id>/summary.json`의 `coverage` 블록에 기록되며
`C4_INTERNAL_SCREEN_V1.md` B절에 옮겨 적는다.

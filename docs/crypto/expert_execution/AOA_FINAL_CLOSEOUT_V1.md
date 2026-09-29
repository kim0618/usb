# AOA Expert Execution Study: Final Closeout V1

작성 2026-09-27. **AOA Expert Execution Study = CLOSED.**
대상: 공개 BitMEX 원장 `aoa_public_2021-12-31_with_letter.zip` (sha256 `b6f1dc7a…d01b9a`), 2018-03 ~ 2021-12, 1,444,583 fill.

---

## 1. 단계별 최종 상태

| 단계 | 판정 | 정본 문서 |
|---|---|---|
| E0 ingest·감사 | **PASS** | `AOA_E0_PREFLIGHT_REPORT_V1.md`, `AOA_DATA_DICTIONARY_V1.md`, `AOA_CLAIM_AUDIT_V1.md` |
| E1 maker 역선택 | **HISTORICAL MIXED / BYBIT_CF_NEGATIVE**, maker 연구 STOP | `AOA_E1_MAKER_ADVERSE_SELECTION_CONTRACT_V1.md`(sha256 `87f7c878…`), `..._RESULTS_V1.md`, `AOA_E1_MAKER_TAKER_COMPARISON_V1.md` |
| E2 포지션 관리 | **MIXED**. LOW_LEVERAGE만 위험 연구 후보로 유지 | `AOA_E2_POSITION_MANAGEMENT_CONTRACT_V1.md`(sha256 `321a03e1…`), `..._RESULTS_V1.md`, `AOA_E2_EPISODE_CASE_STUDIES_V1.md`, `AOA_E2_FEATURE_GATE_V1.md` |
| Closeout | D4.1 비교 완료, 시장 원본 27.6GB 삭제 | `AOA_D4_1_SAFE_SIZING_LOW_LEVERAGE_COMPARISON_V1.md`, 이 문서 |

## 2. 기능별 최종 판정

| 기능 | 최종 | 근거 요약 |
|---|---|---|
| Maker 실행 | **CLOSED** | maker 60초 순edge -1.51bp (역선택 -5.61 > 리베이트 +2.41 + spread +1.69), 연도별 악화, Bybit VIP 0 가정 -7.61bp |
| LIMIT 구현 | **NOT AUTHORIZED** | 위와 같음. queue·미체결 비용 UNKNOWN |
| ADD / 물타기 | **CLOSED** | NO-ADD 대비 +0.10%p (CI 0 걸침), 기계적 규칙보다 평균 낮음, 대형 손실 119 vs 51 |
| 무손절 장기보유 | **CLOSED** | -2% 손절 대비 +1.13%p (CI 0 걸침), 깊은 역행의 48%만 회복, 생존편향에 가장 취약 |
| 진입 신호 모방 | **CLOSED** | 최초 진입 4h drift 보정 +0.044% (CI 0 걸침) |
| 손실 후 크기 규칙 | **CLOSED** | 자본 대비 1.22 ~ 1.30배, 빈도 증가 |
| Partial close | **HOLD / NOT AUTHORIZED** | 규칙상 SUPPORTED였으나 CF3 포지션 부풀림(최대 9.4배)이 판정을 만듦. 공정 비교는 MAE +0.34%p 대 수익 -0.52%p. 다시 보려면 크기 맞춘 CF 사전등록 필요 |
| **LOW_LEVERAGE** | **RETAINED_AS_RISK_RESEARCH_CANDIDATE** | 최고 노출/자본 3분위별 대형 손실 0.7 / 3.5 / 11.6%, 청산 79%가 최상위, 2019~2021 노출 중앙 약 1배·실질 청산 0. **Edge가 아니라 생존·위험 원칙** |

## 3. 남는 교훈 (US-B용)

1. **레버리지는 edge가 아니라 risk multiplier다.** 이익·손실·수수료를 같은 배수로 키운다. taker 왕복 11bp를 못 넘는 신호는 어떤 레버리지로도 양수가 되지 않는다.
2. **SAFE MAX ≠ 안전한 포지션 크기.** SAFE MAX는 "이 스냅샷에서 진입·즉시 청산 가능"이라는 실행 안전이다. 깊은 호가에서 50x SAFE MAX는 자본의 48.7배 노출이고 1.65% 역행에 청산된다.
3. **US-B 엔진은 격리형이라 레버리지 설정이 청산 거리를 정한다**(수량과 무관): 1x 없음, 3x -33%, 5x -20%, 10x -9.7%, 20x -4.7%, 50x -1.7%.
4. AOA 후기(2019~2021) 운용은 노출/자본 약 1배(US-B 1x MAX와 같은 영역)였고, 이 기간 실질 청산은 없었다. 낮은 노출과 생존이 함께 나타났다(인과 아님).
5. 한 계좌의 성공을 복제 대상으로 쓰지 않는다: 생존편향, 신원 미확인, 2018~2021 BitMEX와 2026 Bybit의 구조 차이.

## 4. 변경하지 않은 것

| 항목 | 상태 |
|---|---|
| US-B 레버리지 선택지 (1/3/5/10/20/50) | UNCHANGED |
| D4.1 SAFE MAX | UNCHANGED |
| Paper Engine / UI / 경고(`HIGH_RISK_LEVERAGE = 20`) | UNCHANGED |
| 자동 레버리지 제한 | NOT AUTHORIZED |
| 운영 서버 / 운영 Paper 계좌 | 접근 0 |
| D2 / D5 / D5.1 데이터·결과 | 변경 0 |

## 5. 데이터 보관 상태

| 항목 | 상태 |
|---|---|
| 원본 ZIP (Windows Downloads) | 유지, 수정 0 |
| `aoa_raw/` 해제본 (read-only) | 유지 574MB |
| E0 정규화 `aoa_normalized/` | 유지 150MB |
| E1 결과 (`e1/*.parquet`, `e1_summary.json`, 계약 동결) | 유지 |
| E1 시장 manifest (`e1/market_manifest.json`, `e1/market/manifest/` 1,400개, `archive_listing.json`) | 유지 |
| **E1 시장 원본 `e1/market/quote`·`trade`** | **삭제 (2,800 파일, 27,637,798,037 bytes)**, 2026-09-27T09:05:52Z |
| E2 1분 격자 `e2/minute_grid.parquet` | 유지 53MB |
| E2 결과 (episode/add/reduce/CF parquet, summary, 계약 동결) | 유지 |
| closeout 계산 (`closeout/compute_leverage_table.py`, `leverage_table.json`) | 유지 |
| 삭제 기록 | `data/research/expert_execution/AOA_CLEANUP_MANIFEST_V1.json` |
| 연구 폴더 남은 크기 | 약 885MB |

재현성: E0·E2는 남은 데이터로 재실행 가능. E1 재실행은 시장 원본 재구축이 필요(공개 archive, 약 89GB·2.5시간, 파일별 sha256으로 검증 가능, 절차는 cleanup manifest).

## 6. 코드·테스트

| 항목 | 상태 |
|---|---|
| 연구 코드 | `backend/app/crypto/research/expert_execution/` (`ingest`, `contracts`, `positions`, `alignment`, `e0`, `market`, `e1`, `grid`, `e2`) |
| 테스트 | `backend/tests/crypto/test_expert_execution_e0/e1/e2.py` 39개, 시장 원본 삭제 후에도 전부 통과 (합성 데이터만 사용) |
| 격리 | crypto 엔진에서 이 패키지 import 0. 패키지는 `app.*`를 import하지 않음. closeout 스크립트만 엔진을 읽기 전용으로 사용하며 패키지 밖(`data/.../closeout/`)에 있음 |
| Git | 전부 untracked, commit/push 0 |

## 7. 재개 조건

이 연구 라인은 닫혔다. 다시 연다면 조건은 하나다: US-B 자체 historical/paper 결과로 레버리지·노출 시뮬레이션을 **별도 사전등록**해 수행하는 것.
AOA 원장을 전략 복제 목적으로 다시 쓰지 않는다.

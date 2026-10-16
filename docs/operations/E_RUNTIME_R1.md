# E-RUNTIME-R1: E-MAX V1 런타임 복구 (2026-10-16)

전략은 바꾸지 않았다. H5, RVOL 3.0, R1, Top3, no-backfill, B2, 2x/3x, 09:30 진입, COST_10BP,
유니버스, RVOL window 20 / minimum 5는 모두 동결값 그대로다(`strategy_e_max_v1_rules.json`
canonical `b30a3e95…`). 바뀐 것은 런타임 구현뿐이다.

## 1. 원인 (10/16 읽기전용 감사)

| # | 결함 | 시작 | 영향 |
|---|---|---|---|
| 1 | A-MOVER-LIVE 컷(09:15~09:25)과 스캔·GPT handoff가 E 프로세스 안에서 동기 실행됨 | 10/05 | E의 09:25 확정 단계(T0)가 09:27~09:52에 시작. 10/07, 10/12~15는 결정 0건, 10/05·06·08·09는 부분 유니버스(STALE 17~83%)로 Top3, seal은 09:30 이후 |
| 2 | RVOL 수집기가 ET 자정 이후(00:35) 돌면서 당일 D 행을 0봉·staged=0·complete=1로 미리 기록. append-only `put`이 D의 실제 데이터를 CONFLICT로 버림 | 09/24 | 화~금마다 443~647종목이 영향. 분모 창이 실제 최근 세션 대신 더 오래된 세션으로 밀림(10/09 evidence 82%, 평균 4.65세션) |
| 2b | 그 결과 KHC의 staged 세션이 20개 미만으로 떨어져 bootstrap이 24초마다 무한 재시도 | 10/13 | `bootstrap_complete=false`가 되어 PROVISIONAL로 강등, 장부가 10000으로 리셋됨 |
| 3 | 청산이 09:35 봉 시가로 체결(09:34 종가는 proxy로 기록만) | 09/28 | 25건 중 7건이 상이, 최대 30.4bp, 런타임이 개발 기준보다 +$43.24 |

## 2. 수정

- **A/E 분리**: `run_e_rt2_dryrun.py`에서 A 호출을 전부 제거했다(import, attach, run_cut, rolling order).
  A는 같은 코드와 같은 데이터 트리를 쓰되 자기 프로세스 `usb-a-mover-live`(`app.dev.run_a_mover_live`)에서 돈다.
  두 프로세스는 상태를 공유하지 않고, 키움 앱키만 `app/integrations/kiwoom/lane_share.py`의 고정 ET 시간창으로 나눠 쓴다.

  | ET | minute API (usa06011) | tick API (usa06010) |
  |---|---|---|
  | 04:00-09:15 | E 2.45 / A 2.45 | A 4.9 |
  | 09:15-09:20:40 | A 4.9 (E 대기) | A 4.9 |
  | 09:20:40-09:25 | E 4.9 (10/05 이전과 같은 tick-shard refresh) | A 4.9 |
  | 09:25-09:29:45 | E 4.9 단독 | E 4.9 단독 |

  A의 키움 호출은 09:25:00에 끝나고, 컷 이후 처리(스캔, GPT)는 키움을 쓰지 않는다. E의 T0는 시계로만 정해진다.
- **RVOL 저장소**: `RvolStore.put`은 D의 09:35 ET 이전 행을 `PREMATURE_REJECTED`로 거부한다.
  `collect_symbol`도 닫히지 않은 세션은 세지도 쓰지도 않는다. 쓰기 불가 CONFLICT만 만난 종목은
  `STORE_CONFLICT`로 기록해 재큐잉하지 않는다(무한 bootstrap 제거, 해당 종목은 pending으로 남아 fail-closed).
- **손상 행 인벤토리와 표적 수리**: `run_e_rt3_rvol inventory`(API 호출 없음)와
  `run_e_rt3_rvol repair --inventory … --apply`. 인벤토리에 있는 (symbol, session) 쌍만 다시 조회한다.
  수리는 기존 checksum이 그대로일 때만 하고, `kiwoom_repair_log`에 사유 `PREMATURE_DAILY_APPEND_V1`,
  옛 값, 옛/새 checksum, 수리 시각을 남긴다. `repair-replay`는 다른 저장소의 수리 로그를 키움 호출 없이 재적용한다.
- **청산**: 실행 버전 `e_cost_v2_exact_0934_close`에서 09:34 봉 **종가**로 체결한다(`SimBroker price_field="close"`, 기본값은 open 그대로).
  09:34 봉이 없으면 `UNRESOLVED_EXIT`이고 fallback은 없다. 표준 PnL에서 진입비용째 제외한다(E-D3).
- **실행 차단 가드**: 결정은 항상 계산·기록하되, 다음 중 하나라도 해당하면 체결하지 않고 `NO_DECISION`으로 남긴다.
  `DECISION_AFTER_0930`(seal ≥ 09:30), `DECISION_INCOMPLETE_UNIVERSE`(문서화되지 않은 STALE > 0),
  `RVOL_UNEXPECTED_MISSING`, `RVOL_BOOTSTRAP_INCOMPLETE`. 문서화된 예외는 저장소에 영구 소스 거부
  (`MARKET_DATA_UNAVAILABLE`/`INVALID_SYMBOL`)로 기록된 종목뿐이다(현재 PS).
- **새 장부**: `paper_state_v2/OFFICIAL_KIWOOM_PAPER`와 `paper_state_v2/SHADOW_VALIDATION`, accounting V2,
  initial_equity 10000. V0, V1, PROVISIONAL 장부는 열지 않으며 합산하지 않는다.
- **노출 기록**: 세션 summary에 `target_exposure`, `actual_exposure`, `exposure_gap`을 남긴다. 정수주라 생기는 차이는 실행 제약이고 규칙은 바꾸지 않았다.
- **증거 보존**: `premarket_raw.json.gz`(병합된 프리마켓 봉 ≤09:24)와 `feature_frame.csv`를 seal 이후에 기록한다(독립 재계산용).
- **UI(API)**: `run/runtime_mode.json`이 있으면 `RUNTIME_PAUSED_FOR_REPAIR` / `RUNTIME_SHADOW_VALIDATION` /
  `OFFICIAL_PAPER_RUNNING` 중 하나로 표시하고, 차단된 세션은 차단 사유를 보여준다. headline은 활성 official 장부뿐이다
  (V2가 생기기 전에는 V1 official을 LEGACY로 표시하며 PROVISIONAL은 절대 쓰지 않는다).
  bootstrap은 `COMPLETE` / `COMPLETE_WITH_EXCEPTIONS` / `INCOMPLETE`(pending 개수 포함)로 표시한다.
- **증거 라벨**: `docs/operations/e_runtime_r1_evidence_labels_v1.json`.
  09/28~10/02는 `LEGACY_RUNTIME_EXIT_PROXY`, 10/05~10/16은 `INVALID_RUNTIME_EVIDENCE`. 장부는 수정하지 않는다.

## 3. shadow 검증 (`app.dev.verify_e_runtime_session`)

러너가 남긴 입력(원시 프리마켓 봉, D-1 유니버스 artifact, seal 시점의 RVOL 저장소: 이후 수집분 제외, 이후 수리분은
옛 값으로 되돌려 읽음)에서 다음을 다시 계산해 기록과 대조한다.
universe identity, completeness, T0, seal < 09:30, 미래 봉 0, 피처, RVOL(prior-only), H5, R1/Top3(no-backfill),
B2/노출, 수량, 09:30 시가 진입, 09:34 종가 청산, 10bp 비용, 세션 PnL, 장부 체인.
전부 통과해야 `E-RUNTIME-R1 SHADOW PASS`이고, 그때만 다음 세션부터 official을 켠다.

## 4. 배포 순서 (각 단계는 사용자 승인 후 `deploy/e_runtime_r1/deploy_r1.sh`)

0. 이미 적용됨(10/16): `usb-e-paper.service.d/r1-paper-off.conf`로 E는 `--paper` 없이(장부 기록 없이) 돌고, A mover는 계속 돈다.
   오늘 밤 23:30 KST에 staging 수리가 서버 사본 저장소에만 실행된다(transient unit `e-rvol-repair-staging-r1`).
1. `preflight`: 서버 파일이 R1 이전 git base(978714d)와 같은지 확인한다(읽기 전용).
2. `rvol`: RVOL 코드를 배포하고, 운영 저장소를 백업한 뒤 staging 수리 로그를 replay한다.
   **10/20(화) 00:35 ET 실행 전에 해야** 새로운 조기 기록 행이 생기지 않는다.
3. `runtime`: E 러너, A 워커 유닛(`usb-a-mover-live.timer`), API를 배포하고 `RUNTIME_PAUSED_FOR_REPAIR`로 둔다. usb-backend만 재시작한다.
4. `shadow`: 다음 거래일을 SHADOW_VALIDATION 장부로 실행한다.
5. `verify <session>`: PASS를 확인한다.
6. `official <session>`: PASS 보고서가 있을 때만 그다음 세션부터 V2 official로 전환한다.
7. `rollback`: 교체한 파일과 유닛을 되돌린다. RVOL 수리는 감사 로그와 함께 유지하고, 수리 전 저장소 사본은 백업에 있다.

## 5. 남은 위험

- **키움 용량**: A 모듈 문서에 적힌 대로 두 lane 모두 한도에 가깝다. 분리 후 겹치는 종목은 두 번 조회한다.
  E의 확정 구간(09:20:40~09:29:45)은 10/05 이전과 같은 예산을 받지만, A 컷은 09:20:40에 minute lane을 잃는다.
  그래서 A의 usable 종목 수가 줄 수 있다. shadow 세션에서 A 보고서(`strategy_a_mover_live/run/<D>/a_mover_live.json`)로 실측한다.
- **메모리**: 두 프로세스가 동시에 상주한다(호스트 여유 약 280MB). A는 MemoryMax 300M, Nice 10, CPUWeight 20으로 둔다.
  E의 seal 여유를 shadow에서 확인한다.
- **A 코드 버전**: A 워커는 EP 트리의 A 코드(지금 운영 중인 버전)를 그대로 쓴다. HEAD의 4ad46e2/3e2a8ad A 수정은
  이 트리에 없다(R1 이전부터 그랬고, A 쪽 결정 사항이다).
- **A 데이터 결합**: A의 grouped daily와 splits는 `usb-e-universe`가 EP 트리에 받아둔 파일이다(R1 이전과 같다).

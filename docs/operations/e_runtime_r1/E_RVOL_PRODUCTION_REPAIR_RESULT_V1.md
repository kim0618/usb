# E-RUNTIME-R1 RVOL 운영 수리 결과 (V1)

- 실행: 2026-10-17 12:58 ~ 13:28 KST (사용자 승인: rvol phase만)
- 판정: **E-RUNTIME-R1 RVOL PRODUCTION REPAIR PASS** (수리 자체). 단 11절의 수집기 결함은 runtime phase 전에 해결해야 한다.
- 코드: d35fc85 (전략 규칙 checksum `b30a3e95` 변경 없음)
- 범위 밖(이번에 하지 않음): runtime 배포, A 변경, E paper 재개, shadow, official V2, push

`deploy_r1.sh rvol`의 단계(store 백업 → RV 3파일 배포 → replay dry-run → apply → inventory)를 그대로 따랐다.
단, 스크립트는 dry-run과 apply를 한 블록에서 연달아 실행하므로 단계별로 나눠 실행하고 그 사이에 hard gate를 두었다.

## 1. Store freeze / preflight

| 항목 | 값 |
|---|---|
| freeze | `usb-e-rvol-once.timer` stop (12:58 KST), 실행 중 collector 없음, 열린 핸들 없음 |
| store | `/root/usb_runtime/strategy_e_rvol/kiwoom_premarket.sqlite3` |
| pre sha256 | `b6ebfb4d…752b`, mtime 2026-10-16 23:23:52 KST, WAL/journal 없음 |
| integrity | ok |
| rows | sessions 114,529 / collection_state 2,660 / repair_log 테이블 없음 |
| inventory | 조기 기록 행 6,996 = repo `E_RVOL_CORRUPTED_ROWS_V1.csv`(sha256 `e197c372…`)와 키·체크섬 전부 일치 |
| RV 코드 | 3파일 모두 base 978714d 해시 (md5 cb3be8e8 / 4f7727b4 / 267bd806) |

## 2. 백업

`/root/usb_runtime/backups/e_runtime_r1/20261017_125908/`

| 파일 | sha256 | integrity | rows |
|---|---|---|---|
| `kiwoom_premarket.raw_copy.sqlite3` (바이트 복사) | `b6ebfb4d…` (운영과 동일) | ok | 114,529 / 2,660 |
| `kiwoom_premarket.sqlite3` (sqlite backup API) | `b797729e…` | ok | 114,529 / 2,660 |
| `RV/` | 배포 전 RV 3파일 원본 | | |
| `STAMPS.txt` | 서버 HEAD, DB sha, 코드 md5/sha256, 서비스 상태 | | |

같은 경로가 `backups/e_runtime_r1/STAMPS`에 추가돼 `deploy_r1.sh rollback`이 이 백업을 찾는다.
(12:27에 만든 `20261017_122711/`은 freeze 전 백업이며 이번 apply의 기준이 아니다.)

## 3. 예방 코드 배포

d35fc85의 RV 3파일만 `/root/usb_runtime/strategy_e_rvol/src`에 배포했다.

| 파일 | sha256 (로컬 d35fc85 = 서버) |
|---|---|
| `backend/app/dev/run_e_rt3_rvol.py` | `714891035ea9…` |
| `backend/app/strategy_e_max_rt/rvol_store.py` | `0ae03b751988…` |
| `backend/app/strategy_e_max_rt/rvol_bootstrap.py` | `43f5586b9782…` |

- 배포 후 RV 트리가 staging 수리를 실제로 수행한 트리(`strategy_e_rvol_repair_r1/src`)와 완전히 같다(`diff -rq` 차이 0).
- import ok. `SESSION_CLOSED_AT=09:35`, window 20, minimum 5.
- 임시 DB에서 guard를 확인했다. D행을 00:35 ET에 쓰면 `PREMATURE_REJECTED`, 09:34:59 ET에도 `PREMATURE_REJECTED`, 09:35 ET에는 `WRITTEN`.

## 4. Dry-run (배포된 실제 `repair-replay`, `--apply` 없음)

| outcome | 값 |
|---|---|
| WOULD_REPAIR | 6,968 |
| OLD_ROW_CHANGED (= checksum conflict) | 0 |
| SOURCE_LOG_INCONSISTENT | 0 |
| ALREADY_REPAIRED | 0 |
| UNEXPECTED_TARGET (log 키 중 inventory 밖) | 0 |
| UNREPAIRABLE (inventory - log) | 28 |

리포트: `20261017_125908/repair_replay_dryrun.json`. `RvolStore` 생성 시 SCHEMA가 실행돼 빈 `kiwoom_repair_log` 테이블이 이때 생겼다
(sha256 `00fed42f…`, 행 변경 없음, integrity ok). Hard gate PASS.

## 5. Apply

`repair-replay --apply` (키움 호출 없이 staging repair log만 재적용, 행마다 old checksum 일치 시에만 교체)

| 항목 | 값 |
|---|---|
| attempted | 6,968 |
| REPAIRED | 6,968 |
| skipped / conflict / inconsistent / failed | 0 / 0 / 0 / 0 |
| exit | 0 |
| repaired_at | 2026-10-17 04:01:39 ~ 04:17:05 UTC |
| post sha256 | `5598dca8…69db`, mtime 13:19:42 KST, WAL/journal 없음 |

리포트: `20261017_125908/repair_replay_apply.json`

## 6. 사후 감사 (pre = 바이트 복사 백업, post = 운영)

| 항목 | 결과 |
|---|---|
| integrity | ok |
| sessions | 114,529 → 114,529, 추가 0 / 삭제 0, 수정 6,968 (전부 REPAIRED 대상, 대상 밖 수정 0) |
| collection_state | 2,660 → 2,660, 수정 1,322 (전부 repair log 종목) |
| repair_log | 0 → 6,968 (빈 값 0, 사유 `PREMATURE_DAILY_APPEND_V1`만) |
| repair log 내용 | old checksum·old 값이 pre 행과 6,968/6,968 일치, new checksum 재계산 6,968/6,968 일치 |
| staging과 비교 | 운영 행 값·체크섬이 staging과 전부 같음(`collected_at` 제외), log도 `repaired_at` 외 동일 |

### 남은 28행 (inventory를 다시 돌려도 28이 정상, 0이 아님)

post inventory: `20261017_125908/E_RVOL_CORRUPTED_ROWS_AFTER_REPAIR.json` = 28행 / 15종목 / staged_true 0.
키가 staging의 미수리 28행과 정확히 같다.

| 구분 | 행 | 종목 | 의미 |
|---|---|---|---|
| UNCHANGED | 10 | SMP, NVR, MTRX, SOBO, SSB, RDN, UHAL.B, ENVA, ULS, WMK | 키움에서도 0봉/09:30봉 없음. 원천이 0을 확인했다 |
| NO_CHART_ROWS | 5 | ET, LUMN | 해당 날짜 키움 봉 자체가 없음 |
| UNMAPPED | 13 | PSKY, WBD, QRVO | 키움 종목 매핑 불가 |

28행 모두 staged=0이라 분모(staged=1, 값>0만 사용)에 들어가지 않는다.
inventory 규칙(collected_at < 09:35 ET)은 계속 잡지만, 바꿀 실제 값이 없는 기록된 예외다.

### 종목별 확인

| 항목 | before → after |
|---|---|
| KHC | staged 11 → 25, 분모 320,491 → 282,707, pending 해소 |
| OIG | history_exhausted 1 → 0, staged 12 → 13 (staging과 동일, universe 밖이라 bootstrap 영향 없음) |
| PS | `MARKET_DATA_UNAVAILABLE` 그대로, unreachable(문서화된 원천 거부) 유지 |
| exhausted | BH.A, BRK.A, DPC, ETRA, MCHPP, OBX |
| universe 안 unreachable | BH.A, BRK.A, DPC, DSGR, ETRA, OBX, PS (7, 전후 동일) |

### Store health (universe 2,552, 세션 2026-10-19 기준)

| 항목 | before | after |
|---|---|---|
| 조기 기록 행 | 6,996 | 28 |
| staged·complete 행 | 83,151 | 90,071 |
| >=20 sessions (fully_ready) | 2,544 | 2,545 |
| minimum >=5 | 2,550 | 2,550 |
| 분모 사용 가능 | 2,550 | 2,550 |
| zero history | 2 | 2 |
| pending (bootstrap 대기) | 1 (KHC) | 0 |
| bootstrap_complete | false | true |
| 분모 변경 | | 1,026종목 (5% 초과 904, None 전환 0, 중앙값 비율 1.095) |

- 분모 재계산: 표본 204종목(무작위 200 + KHC/PS/ET/NVR)에서 독립 구현한 prior-only rolling median(window 20, minimum 5, frozen 기본값)이 `RvolStore.preload`와 전부 일치했다(불일치 0).
- bootstrap 상태: `COMPLETE_WITH_EXCEPTIONS`. planner queue가 0이고, 예외는 문서화된 unreachable 7종목이다.
- KHC가 일으키던 반복(1종목 101페이지, staged_written 0)은 queue에 다시 들어갈 종목이 없으므로 재발할 수 없다.
- bootstrap_complete=true는 bootstrap 사유의 PROVISIONAL이 풀린다는 뜻일 뿐이다. OFFICIAL 판정은 별도로 세션 `rvol_missing=0`과 shadow PASS가 필요하다.

## 7. 10/16 누락 (별도 이슈, 이번 수리 대상 아님)

- 운영 store의 10/16 행은 525개(staged 520)이고, universe 중 **2,027종목에 10/16 행이 없다**.
- 원인: 10/16 10:00 ET 실행이 append 리포트가 이미 있어 append를 건너뛰었고, 그 실행도 23:23 KST 키움 종목목록 오류로 실패했다.
- 이번 apply에서 임의로 채우지 않았다. 다음 정상 collector 실행 뒤 다시 확인한다.

## 8. Timer / 서비스

- `usb-e-rvol-once.timer`: 13:28 KST에 다시 start. 이제 새 코드로 실행된다.
- 다음 실행은 13:35 KST(00:35 ET)이고, 강제 실행은 하지 않았다.
- `Persistent=true`지만 중지된 동안 지나간 예정 시각이 없어 catch-up 실행은 없다.
- 재시작한 유닛: 없음. usb-backend, usb-frontend, usb-e-paper, universe, morning-scan, h-forward 모두 ActiveEnter 시각이 그대로다.
- E paper: `r1-paper-off.conf` 유지, ExecStart에 `--paper` 없음. 장부 마지막 세션은 10/15, `paper_state_v2`와 `runtime_mode.json`은 없다.
- 서버 `/root/usb`의 다른 세션 dirty 파일(HEAD 8f19e63)은 건드리지 않았다.

## 9. 테스트

- `tests/strategy_e_max/test_e_runtime_r1_rvol.py`: 11 passed.
- `tests/strategy_e_max` 전체: 436 passed, 1 failed (`test_strategies_api.py::test_strategy_a_endpoints_are_untouched`).
  - 실패 원인은 로컬 DB에 `scanner_runs.active_gpt_analysis_id` 컬럼이 없는 것(로컬 마이그레이션 미적용)이다. RVOL 변경과 무관하다.

## 10. Rollback

- 코드: `deploy_r1.sh rollback`이 STAMPS의 마지막 백업 `20261017_125908/RV/`에서 RV 3파일을 복원한다.
- store: 수리는 감사 로그와 함께 유지하는 것이 원칙이다. 수리 전 상태가 필요하면 `kiwoom_premarket.raw_copy.sqlite3`(sha256 `b6ebfb4d…`)로 되돌린다.

## 11. 새 코드로 돈 첫 정규 수집 (2026-10-17 13:35 KST, 00:35 ET)

- 13:35:05 ~ 14:29:07 KST, exit 0, memory peak 50MB.
- `append done: 2552 symbols, 14868 pages, 1670 staged` 다음에 `bootstrap complete`로 끝났다. KHC 반복 루프(1종목 101~105페이지 반복)는 다시 나타나지 않았다.
- 실행 후 store 상태:

| 항목 | 값 |
|---|---|
| integrity | ok |
| sessions | 116,555 (+2,026, 전부 session 2026-10-16) |
| 10/16 행 / universe 누락 | 2,551 / 1 (2,027에서 해소) |
| 2026-10-17 이후 행 | 0 (토요일, 조기 기록 거부됨) |
| 조기 기록 행 | 28 (변화 없음) |
| repair_log / new checksum 유지 | 6,968 / 6,968 |
| KHC staged | 25 |

### 발견: append pass가 예외 표시를 지운다 (runtime/shadow 전에 해결 필요)

- 실행 후 bootstrap pending이 **5종목**(BH.A, DPC, DSGR, ETRA, OBX)이 됐다. 수리 직후에는 0이었다.
- 원인(`run_e_rt3_rvol._pass`):
  - append pass는 `target=1`로 돈다. 그래서 `history_exhausted = exhausted and staged < 1`, `HISTORY_PAGE_LIMIT if page_limited and staged < 1`이 모두 거짓이 된다.
  - 이 값을 `note_pass`가 덮어쓰면서, bootstrap pass(`target=20`)가 남긴 unreachable 표시가 지워진다. 앞서 본 OIG 1 → 0과 같은 현상이다.
- `once`는 bootstrap 대상(`work`)을 append **전에** 계산한다. 그래서 이번 실행은 `bootstrap complete`로 끝났고, 지워진 5종목은 다음 실행(10:00 ET)이 다시 걸어 표시할 때까지 pending으로 남는다.
- 영향: E 결정 시각(09:25 ET)은 00:35 ET 실행 후, 10:00 ET 실행 전이다. 지금 동작대로라면 매 거래일 결정 시점에 `bootstrap_complete=false`가 되어 `PROVISIONAL_RVOL_BOOTSTRAP`이 된다.
- 기존 코드도 append 뒤에 같은 종목들을 매일 다시 걸었다(10/15 로그 `pass done: 6/6 symbols, 1359 pages`). 다만 그때는 같은 실행 안에서 다시 표시됐다.
- 수리 결과(행 값, repair log)와는 무관하다. 전략 규칙과 threshold도 무관하다. 수집기 상태 표시의 문제다.
- 제안(미적용): append pass(`target < RVOL_WINDOW`)에서는 기존 `history_exhausted`/`last_error` 예외 표시를 보존한다. 또는 `once`가 append 후 queue를 다시 계산한다. 회귀 테스트를 추가한 뒤 runtime phase 전에 배포한다.

## 다음 단계 (미시작)

runtime phase (A/E 프로세스 분리, 09:34 종가 수정) → shadow 검증 → official V2. 각 단계는 별도 승인 후 진행한다.

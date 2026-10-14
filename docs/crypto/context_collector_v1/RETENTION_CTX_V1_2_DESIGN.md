# Context Collector Retention V1.2 설계 (HOT / COLD ARCHIVE)

상태: 설계와 사전검증만 끝남. 코드 변경 0, 배포 0, 삭제 0 (2026-10-14).

> 2026-10-14 갱신: telemetry와 storage_stats는 이 설계의 "COLD 후 48시간 삭제"가 아니라 V1.1 7일 규칙을 그대로 따른다. 정본은 `RETENTION_CTX_V1_2.md` 2-1절이다.
근거 코드: 로컬 main b9e00aa. 서버 설치본 3곳(crypto_context, q123_forward, btc_context_ui)과 sha256이 모두 같음.

## 1. 실측 전제

- journal 전체가 세션 하나다. 세션 `31cc8177`은 2026-10-10 21:47 KST에 시작했고 86 segment가 쌓였다. 종료된 세션은 0개다.
- 그래서 "종료 세션만 COLD로 보낸다"는 정책은 collector가 재시작되기 전까지 절약량이 0이다. V1.2는 **활성 세션 안에서 segment 단위로** HOT과 COLD를 나눠야 한다.
- 10/13 하루 증가량: context 28.7MB, wall_r0 27.4MB, wall_v2 10.8MB, telemetry 30.2MB(plain), storage_stats 9.2MB. 합계 106.3MB.
- telemetry 증가는 `wall_continuity` 이벤트(건당 약 12KB)가 resync와 함께 늘었기 때문이다. 시간당 10건이던 것이 69건이 됐다.

## 2. writer / reader 지도

| artifact | writer | 운영 reader | 연구 reader | 운영상 필요 기간 | 재생성 |
|---|---|---|---|---|---|
| context `.jsonl.gz` | usb-crypto-context | Q123 recorder (stream.py) | reader.seconds, R0/R1 replay | Q123 watermark 이후 | 불가 (실시간 수집) |
| wall_r0 `.jsonl.gz` | 같음 | Q123 recorder | 같음 | Q123 watermark 이후 | 불가 |
| wall_v2 `.jsonl.gz` | 같음 | 없음 (API 8013 미설치) | reader.walls_secondary_current | 0 | 불가 |
| telemetry `.jsonl` | 같음 | collector의 viewer 경로 (현재 세션 최근 12건) | 품질 연구 | 최근 1 segment | 불가 |
| storage_stats `.jsonl` | 같음 | 없음 | 용량 연구 | 0 | 불가 |
| session `.jsonl` | 같음 | LM viewer `latest_session` | 전부 | 영구 (거의 0바이트) | 불가 |
| `state/` (tmpfs) | 같음 | btc_context_ui, retention | 없음 | 현재 | 매초 재작성 |
| `.writer.lock` | 같음 | retention, Q123 판정 | 없음 | 현재 | 기동 시 |

collector는 재기동할 때 옛 segment를 읽지 않는다. orphan `.open`을 봉인하고 압축하는 것뿐이다.

## 3. Q123 의존 범위 (코드 기준)

- 읽는 종류는 `context`와 `wall_r0` 두 가지다 (stream.py `KINDS`). wall_v2, telemetry, storage_stats는 읽지 않는다.
- 처음부터 읽어야 하는 이유: wall_r0의 resting set은 세션 시작부터 누적된다. 그래서 cursor는 단순 offset이 아니라 checkpoint(`SessionCursor`: kind별 [segment, offset], resting, pending)다.
- recorder는 10분마다 `runtime/checkpoints/ckpt-<sample_ms>.json`을 쓴다. sha256과 schema(`q123-forward-recorder.v1`)를 함께 기록한다. 보존 범위는 `need − 6h` 이후 전부에, 그보다 오래된 것 중 최신 1개를 더한 것이다.
- 재기동(`_restore`)할 때는 `sample_ms ≤ need`인 checkpoint 중 가장 최신 것을 고른다. `need`는 결과가 덜 채워진 event의 decision_ts와 (최신 anchor − 2h) 중 작은 값에서 120초를 뺀 값이다. 결과 horizon의 최대는 60분이다.
- checkpoint가 있으면 cursor 위치 이전 segment가 없어도 된다. `_start_pos`는 pos가 이미 있으면 첫 segment를 검사하지 않는다.
- checkpoint를 못 고르면 `SESSION_START`로 간다. 그때 segment 1이 없으면 `UnrecoverableStart`가 나고 **활성 세션 전체가 unrecoverable로 표시돼 Q123 수집이 멈춘다.**
- 완료 세션은 cursor `done=True`가 checkpoint에 남고 이후 다시 읽지 않는다. 하지만 그 checkpoint를 선택하지 못하면 처음부터 다시 읽는다.
- `analyze` 명령은 Q123 store만 읽는다. journal을 처음부터 읽는 것은 오프라인 `run.py`(연구용)뿐이다.
- 2026-10-14 11:00 실측: 유효 checkpoint 50개 중 가장 오래된 것이 가리키는 위치는 segment 77(10/14 02:29 KST)이다. Q123이 운영에 필요로 하는 raw는 최근 약 9시간이다.

**raw를 서버에서 지워도 Q123 운영과 복구가 가능한 조건**

- context 또는 wall_r0의 segment n을 지우려면, sha256과 schema가 모두 맞는 Q123 checkpoint가 1개 이상 있어야 한다.
- 그 checkpoint들이 가리키는 해당 세션·kind의 최소 segment를 W라 할 때, n < W − margin이어야 한다.
- Q123 status의 `unrecoverable_sessions`가 비어 있어야 한다.
- 완료 세션이라면, 유효 checkpoint 중 하나에 그 세션의 `done=True`가 있으면 된다.

다음 경우에는 위 조건이 깨진다. 이때는 COLD에서 raw를 되돌려 놓고 기동해야 한다.

- `RECORDER_VERSION`을 바꾸는 배포
- checkpoint 디렉터리 초기화
- Q123 store 초기화로 `need`가 None이 되는 경우

## 4. 세션 lifecycle 정본

- **ACTIVE**: `.writer.lock`이 그 session_id를 가리키고, `state/` 파일이 있다면 같은 세션을 가리킨다. 이 판정은 v1.1의 `active_session8`과 같다.
- **FINALIZING**: lock은 아직 그 세션을 가리키지만 writer 프로세스가 없다. collector가 정지했지만 아직 다음 세션이 시작되지 않은 상태다. 이 세션에는 `.open` 파일이 남아 있을 수 있다. CLOSED로 보지 않는다.
- **CLOSED_CLEAN**: lock이 다른 세션을 가리키고, 그 세션의 `.open` 파일이 0개이며, session 파일에 `END` 기록이 있다. `END`는 V0 collector의 `finish()`가 쓴다.
- **CLOSED_CRASHED**: 위와 같지만 `END` 기록이 없다. 다음 기동의 `recover_orphans`가 이미 봉인한 상태다. Q123도 이 세션을 over로 취급하므로 CLOSED로 본다.
- **UNKNOWN**: lock을 읽을 수 없거나, lock과 state가 서로 다른 세션을 가리키거나, 세션 이름이 hex8이 아니다. 아무것도 지우지 않는다.

시간이 지났다는 것만으로는 CLOSED가 되지 않는다.

## 5. HOT / COLD 정책

- **HOT**(서버 유지): 활성 세션의 최근 48시간이다. Q123 watermark보다 이후인 context와 wall_r0는 기간과 무관하게 HOT이다. 세션 1번 segment는 별도로 고정하지 않는다. 대신 Q123 checkpoint 조건이 지켜준다. session 파일, `state/`, lock은 항상 HOT이다.
- **COLD**(외부): 48시간이 지난 context, wall_r0, wall_v2와 telemetry, storage_stats다. 종료 세션의 raw 전부도 COLD다.
- telemetry와 storage_stats는 지금 7일이 지나면 **아카이브 없이 삭제된다.** V1.2에서는 COLD로 보낸 뒤 48시간이 지나면 지운다.

## 6. COLD archive 형식

- 단위는 (session8, UTC 날짜)다. 봉인된 파일은 이미 불변이고, 자체 kind는 이미 gzip이다. 그래서 서버에서 다시 압축하거나 복사본을 만들지 않는다.
- 서버는 `<root>/../archive/manifests/<session8>-<YYYYMMDD>.json`만 쓴다.
- manifest 필드:

```json
{"version": "ctx-archive.v1", "session_id": "...", "session8": "...", "utc_date": "YYYYMMDD",
 "journal_schema": "ctx-v1.1 / collector_version", "retention_version": "ctx-retention.v1.2",
 "created_at": "...", "session_state": "ACTIVE|CLOSED_CLEAN|CLOSED_CRASHED",
 "q123": {"watermark": {"context": 77, "wall_r0": 77}, "session_done": false,
          "checkpoint_files": 50},
 "files": [{"path": "context/context-...-00001.jsonl.gz", "bytes": 0, "sha256": "...",
            "sealed_utc": "..."}],
 "total_bytes": 0, "manifest_sha256": "..."}
```

- 도구는 서버에 이미 있는 것만 쓴다. python3, sha256sum, rsync, gzip, zstd, xz가 있다. 새 의존성은 0이다.
- 외부 사본 위치: 로컬 PC `~/usb_archive/crypto_context/<session8>/<YYYYMMDD>/<kind>/`. Google Drive는 지금 WSL에 마운트돼 있지 않고(`/mnt/g` 비어 있음), 서버에는 rclone이 없다. 그래서 1차 COLD는 로컬 PC다. Drive에는 그다음에 복사한다.

## 7. 전송과 검증 workflow (pull 방식)

1. 서버 retention이 `plan-archive`를 실행한다. 자격이 있는 파일의 manifest를 쓰기만 하고, 데이터는 복사하지 않는다.
2. 로컬 스크립트(`deploy/crypto/ctx-archive-pull.sh`, 신규)가 manifest를 받는다. 이어서 `rsync --files-from`으로 journal 경로에서 직접 pull하고, 로컬에서 파일별 sha256을 대조한다.
3. 전부 일치하면 로컬이 receipt를 만들어 서버 `archive/receipts/<session8>-<YYYYMMDD>.json`에 올린다. receipt에는 manifest_sha256, 파일별 sha256, 로컬 경로, 검증 시각이 들어간다.
4. 서버 retention `--apply`는 receipt가 있고, receipt와 manifest가 같고, 지금 서버 파일의 sha256까지 같은 파일만 지운다.

서버는 외부 저장소의 자격증명을 갖지 않는다. 전송은 로컬에서 시작하는 ssh pull 하나뿐이다.

## 8. prune 조건 (전부 만족해야 한다)

1. 세션 식별 성공 (UNKNOWN 아님)
2. 봉인된 정규 파일이고 이름이 패턴에 맞음. `.open`, `.tmp`, symlink는 대상이 아니다.
3. mtime과 이름 날짜가 둘 다 HOT 기간보다 오래됨 (v1.1과 같은 두 시계 규칙)
4. ACTIVE 세션의 context와 wall_r0는 n < W − 12 segment. W를 구할 수 없으면 보존한다.
5. CLOSED 세션의 context와 wall_r0는 유효 checkpoint에 `done=True`가 있어야 한다.
6. manifest가 있고 그 sha가 맞음
7. receipt가 있고 receipt와 manifest, 서버 파일의 sha256이 모두 일치함
8. Q123 checkpoint schema가 기대값과 다르면 context와 wall_r0는 하나도 지우지 않음 (fail closed)

디스크가 부족하다는 이유로는 어떤 조건도 완화하지 않는다.

## 9. 디스크 health guard

- 선례가 이미 있다. liqfwd collector는 여유 공간이 300MB 미만이면 쓰기를 멈춘다(`LIQFWD_DISK_MIN_FREE`).
- 상태 기준: OK는 여유 1.5GB 이상이면서 7일치 증가량 이상이다. WARNING은 1.0GB 미만이거나 7일치 미만이다. CRITICAL은 500MB 미만으로, liqfwd 정지선 300MB보다 먼저 울리게 한다.
- 표시 위치는 기존 구조를 쓴다. retention의 일일 PRUNE JSON 로그에 `disk` 블록을 넣는다. 화면에서 읽을 수 있도록 btc_context_ui `/health`에 `disk` 필드를 추가한다. crypto terminal의 `storage` 상태 블록에도 같은 값을 붙인다.
- WARNING과 CRITICAL은 표시만 하고 삭제를 일으키지 않는다.

## 10. 예상 효과

- 서버 journal의 정상 상태: HOT 48시간에 해당하는 약 210MB와, 아직 pull하지 않은 분량(하루 106MB × 마지막 검증 pull 이후 일수)을 더한 값이다.
- pull 주기는 3일 이하를 권장한다. 7일이면 최대 약 950MB까지 늘어난다.
- 외부 증가량은 하루 약 106MB다. telemetry를 로컬에서 gzip하면 하루 약 75MB로 줄어든다. 한 달이면 2.2~3.2GB다.
- 10/14 11:00 기준으로 당장 COLD로 넘길 수 있는 양은 HOT 48h에서 104MB, HOT 24h에서 210MB다.

## 11. 필요한 코드 변경 (승인 후)

- `backend/app/crypto/context_collector_v1/retention.py`를 v1.2로 올린다. 세션 상태 판정, Q123 watermark 읽기, HOT 시간 단위, manifest와 receipt gate, `disk` 블록이 들어간다. crypto_context 설치본에는 Q123 코드가 없다. 그래서 checkpoint는 JSON과 canonical sha만으로 검증하는 사본 함수로 읽고, parity 테스트로 보장한다.
- `backend/app/crypto/context_collector_v1/archive.py`(신규)는 manifest와 receipt의 생성·검증만 맡는다.
- `deploy/crypto/ctx-archive-pull.sh`(신규)는 로컬 pull, 검증, receipt 업로드를 맡는다.
- `docs/crypto/context_collector_v1/RETENTION_CTX_V1_2.md`와 sha256을 새로 만든다.
- 서버 `usb-crypto-context-retention.service`와 `context.env`에 `CTX_V1_HOT_HOURS`, `CTX_V1_Q123_RUNTIME`, `CTX_V1_ARCHIVE_DIR`을 넣는다.
- 테스트: watermark parity, schema 불일치 fail closed, receipt 불일치, W 경계, CLOSED done, UNKNOWN identity를 다룬다.
- collector, Q123 recorder, stream.py는 바꾸지 않는다.

## 12. 운영 영향

- collector와 Q123은 재시작하지 않는다. retention oneshot의 코드와 unit 인자만 바뀌므로, 10/11 v1.1 반영 때처럼 release 안의 파일을 교체하고 daemon-reload만 하면 된다.
- 이후 Q123 `RECORDER_VERSION`을 바꾸는 배포를 하려면, 먼저 COLD에서 활성 세션의 segment 1부터 W까지를 복원해야 한다. 아니면 그 배포 직전에 collector 세션을 새로 시작해야 한다. 이 내용을 Q123 런북에 적어야 한다.

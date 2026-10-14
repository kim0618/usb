# RETENTION_CTX_V1_2: HOT / COLD ARCHIVE / PRUNE 계약

버전: `ctx-retention.v1.2`. 구현: `backend/app/crypto/context_collector_v1/retention.py`의 `retain`(V1.2 절반)과 `archive.py`.
대체 대상: `RETENTION_CTX_V1_1.md`와 `RETENTION_ACTIVE_SESSION_FIX_V1.md`의 삭제 규칙. V1.1의 `prune` 명령은 코드에 남아 있지만, 이 계약 이후 어떤 unit도 실행하지 않는다.
설계 근거: `RETENTION_CTX_V1_2_DESIGN.md`.

## 1. 분류

봉인된 파일은 아래 순서로 정확히 한 분류에 들어간다.

| 분류 | 조건 |
|---|---|
| HOT | `.jsonl.open`, `.tmp`, 또는 두 시계 중 하나라도 HOT 기간 안 |
| PINNED | `session` 파일. 아카이브하지도 지우지도 않는다 |
| KEEP | 정규 파일이 아니거나 이름이 패턴과 다르다 |
| ARCHIVE_CANDIDATE | raw kind(`context`, `wall_r0`, `wall_v2`)가 봉인됐고, mtime과 이름 날짜가 둘 다 HOT cutoff보다 앞선다 |
| PRUNE_ELIGIBLE | ARCHIVE_CANDIDATE 중 2절의 gate를 모두 통과한 파일 |
| EXPIRED | `telemetry`, `storage_stats`가 두 시계 모두 7일보다 오래됐다. 2-1절 |

- HOT 기간은 `context`, `wall_r0`, `wall_v2`가 48시간(`CTX_V1_HOT_HOURS`)이다. `telemetry`와 `storage_stats`의 보존 기간은 V1.1의 7일이다(`CTX_V1_TELEMETRY_HOT_HOURS`=168). 어느 쪽도 24시간 미만은 거부한다.
- 두 시계 규칙은 V1.1과 같다. mtime이 cutoff보다 앞서고, 이름의 UTC 날짜가 cutoff 날짜보다 앞서야 한다.
- `state/`, `state.disk-*`, `.writer.lock`은 kind 디렉터리 밖에 있어 아예 나열되지 않는다.

## 2. PRUNE gate: raw kind 전용 (하나라도 실패하면 KEEP, 실패 사유를 기록)

1. **세션 식별**: writer lock이 hex8 세션을 가리키고, 상태 파일이 있다면 같은 세션을 가리킨다. 실패하면 `IDENTITY_UNKNOWN`.
2. **세션 상태**: 3절. UNKNOWN이면 `SESSION_STATE_UNKNOWN`.
3. **Q123 guard**(`context`, `wall_r0`만 해당): 4절. 실패하면 `Q123_<state>`, `Q123_UNRECOVERABLE`, `Q123_SESSION_NOT_IN_EVERY_CHECKPOINT`, `Q123_MARGIN`, `Q123_NOT_DONE` 중 하나.
4. **manifest**: 유효한 manifest가 이 파일을 나열한다. 아니면 `NO_MANIFEST`.
5. **receipt**: 유효한 receipt가 이 파일을 manifest와 같은 sha256으로 나열한다. 아니면 `NO_RECEIPT`, `RECEIPT_SHA_MISMATCH`.
6. **현재 파일**: 지금 디스크의 sha256과 크기가 manifest와 같다. 아니면 `CURRENT_SHA_MISMATCH`.

raw kind의 실제 삭제는 실행에 `--apply`가 있고 **동시에** `CTX_V1_PRUNE_ENABLED=true`일 때만 일어난다. 둘 중 하나라도 없으면 dry-run이다. 디스크 상태(9절)는 어떤 gate도 바꾸지 않는다.

## 2-1. telemetry와 storage_stats: V1.1 계약 그대로

이 두 kind는 raw 정책과 섞지 않는다. manifest도 receipt도 Q123 guard도 없고, `CTX_V1_PRUNE_ENABLED`의 영향도 받지 않는다. V1.1과 같은 조건으로 판정한다.

- 세션 식별이 성공한다.
- 봉인된 파일이고 이름이 패턴에 맞는다.
- mtime이 지금으로부터 7일보다 앞서고, 이름의 UTC 날짜도 cutoff 날짜보다 앞선다.
- 활성 세션이라도 보호하지 않는다. V1.1의 활성 세션 보호는 session과 재구성 원본에만 적용됐다.

이 조건을 만족하는 파일은 `--apply`가 붙은 모든 실행에서 지운다. unit은 항상 `--apply`를 붙이므로 매일 지워진다. 세션을 식별하지 못하면 `IDENTITY_UNKNOWN`으로 보존한다. 이 판정이 V1.1 `plan`과 같다는 것은 parity 테스트로 보장한다. 외부 보관이 필요해지면 별도 계약으로 다룬다.

## 3. 세션 상태

| 상태 | 판정 |
|---|---|
| ACTIVE | writer lock이 그 세션을 가리킨다. writer 프로세스가 죽었어도(FINALIZING) ACTIVE로 다룬다 |
| CLOSED_CLEAN | lock이 다른 세션을 가리키고, 그 세션의 `.open`·`.tmp` 파일이 0개이며, session 파일에 `END` 기록이 있다 |
| CLOSED_CRASHED | 위와 같지만 `END`가 없다. 다음 기동의 orphan 복구가 이미 봉인했다 |
| UNKNOWN | 세션 식별이 실패했거나, lock이 가리키지 않는 세션에 쓰는 중인 파일이 있다 |

시간이 지났다는 것만으로는 CLOSED가 되지 않는다.

## 4. Q123 checkpoint guard

입력은 `CTX_V1_Q123_RUNTIME/checkpoints/ckpt-*.json`과 `CTX_V1_Q123_STORE`이고, 둘 다 읽기 전용이다. crypto_context release에는 Q123 코드가 없으므로 아래 규칙을 restate했고, parity 테스트로 recorder와 같음을 보장한다.

- **checksum**: `sha256`을 뺀 본문을 canonical JSON(정렬 키, `,`와 `:` 구분자, `ensure_ascii=False`)으로 직렬화한 sha256이 일치해야 한다. 불일치하는 파일은 recorder처럼 무시한다.
- **schema와 구조**: checksum이 맞는데 schema가 `q123-forward-recorder.v1`가 아니거나, cursor 구조가 다르면 guard 전체를 닫는다. 구조 조건은 `pos.context`와 `pos.wall_r0`가 각각 `[segment ≥ 1, offset ≥ 0]`이고, `resting`이 dict, `pending`이 list, `done`이 bool인 것이다. 이때 상태는 `SCHEMA_MISMATCH` 또는 `CHECKPOINT_STRUCTURE`이며 context와 wall_r0는 0건 삭제된다.
- **유효 checkpoint 없음**: `NO_CHECKPOINT`.
- **need**: recorder의 `_needed_from` 규칙으로 계산한다. need가 None이면 recorder가 세션 시작부터 다시 만들므로 `NEED_NONE`이다. store를 읽지 못하면 `STORE_MISSING` 또는 `STORE_UNREADABLE`이다.
- **need 이전 checkpoint**: `sample_ms ≤ need`인 유효 checkpoint가 없으면 recorder가 재기동 때 세션 시작부터 읽으므로 `NO_CHECKPOINT_AT_OR_BEFORE_NEED`이다.
- **세션별 판정**: 세션이 모든 유효 checkpoint에 들어 있어야 한다. watermark W는 kind마다 모든 유효 checkpoint 중 가장 작은 segment다.
  - ACTIVE: segment n ≤ W − 12일 때만 통과한다.
  - CLOSED: 모든 유효 checkpoint에서 `done=True`여야 한다.
  - checkpoint의 `unrecoverable`이나 `status.json`의 `unrecoverable_sessions`에 있는 세션은 보존한다.
- `wall_v2`, `telemetry`, `storage_stats`에는 운영 reader 의존성이 없어 Q123 guard를 적용하지 않는다.

## 5. Manifest (`ctx-archive-manifest.v1`)

경로는 `CTX_V1_ARCHIVE_DIR/manifests/<session8>-<YYYYMMDD>-<part 3자리>.json`이다. 한 번만 쓰며 덮어쓰지 않는다. 나중에 HOT을 벗어난 파일은 다음 part에 들어간다.
필드: `manifest_id`, `session8`, `session_id`, `session_state`, `journal_schema`(session START의 collector_version과 contract), `utc_date`, `part`, `root`, `retention_version`, `created_at`, `q123`(state, 유효 checkpoint 수, need_ms, watermark, done), `files[]`(path, kind, segment, bytes, sha256, sealed_utc), `file_count`, `total_bytes`, `aggregate_digest`, `manifest_sha256`.
서버는 데이터를 복사하지도 다시 압축하지도 않는다. 봉인된 파일은 불변이고 자체 kind는 이미 gzip이다.

## 6. Receipt (`ctx-archive-receipt.v1`)

경로는 `CTX_V1_ARCHIVE_DIR/receipts/<manifest_id>.json`이고, 운영자 PC의 `archive pull`이 만들어 올린다.
필드: `manifest_id`, `manifest_sha256`, `pulled_at`, `verified_at`, `destination`, `result`(OK, PARTIAL, FAILED), `file_count`, `byte_count`, `verified[]`(path, sha256, bytes), `failed[]`(path, reason), `aggregate_digest`, `receipt_sha256`.

서버가 receipt를 받아들이는 조건은 다음과 같다.

- seal(receipt_sha256)과 version이 맞는다.
- manifest_id와 manifest_sha256이 현재 manifest와 같다.
- result가 OK 또는 PARTIAL이다.
- aggregate_digest, file_count, byte_count를 다시 계산하면 같다.
- verified의 모든 항목이 manifest의 sha256과 bytes와 같다.

PARTIAL receipt는 검증된 파일에 대해서만 효력이 있다.

## 7. 외부 사본

- 1차 COLD 저장소는 운영자 PC의 `~/usb_archive/crypto_context/`(`journal/`, `manifests/`, `receipts/`)다.
- 서버와 같은 디스크에 있는 경로는 COLD가 아니다.
- 서버는 외부 저장소의 자격증명을 갖지 않는다. 전송은 PC가 시작하는 ssh와 rsync pull 하나뿐이다.
- Google Drive 사본은 PC에서 2차로 만든다. receipt의 근거는 PC 사본이다.

## 8. 로그

매 실행은 `retention.log`에 `{"event":"RETAIN", ...}` 한 줄을 쓴다. 들어가는 항목은 다음과 같다.

- 세션 상태와 Q123 상태(checkpoint 수, need, watermark, done, resting 수)
- `hot`, `pinned`, `archive_candidate`, `receipt_verified`, `prune_eligible`, `deleted`(raw), `expired`, `deleted_expired`(telemetry, storage_stats)의 파일 수와 바이트
- 사유별 `keep_reasons`
- manifest와 receipt 현황
- `disk_before`, `disk_after`, `health`

파일 단위 목록은 로그에 남기지 않는다.

## 9. 디스크 health (표시 전용)

| 상태 | 여유 공간 |
|---|---|
| OK | ≥ 1,000,000,000 B |
| WARNING | < 1,000,000,000 B |
| CRITICAL | < 500,000,000 B |
| UNKNOWN | 측정 실패 |

이 상태는 retention 로그와 BTC context UI의 `/health` `disk` 필드에 나온다. `/health`의 `ok` 값은 바꾸지 않는다. WARNING과 CRITICAL은 경고만 하며, 어떤 gate도 완화하지 않고 receipt 없는 파일을 지우지 않는다.

## 10. 운영 제약

- collector, Q123 recorder, crypto paper는 이 계약 때문에 재시작하지 않는다. 반영 대상은 retention의 파일, unit, env뿐이다.
- **Q123 `RECORDER_VERSION`이나 checkpoint schema를 바꾸는 배포 전에는, 지워진 활성 세션의 context와 wall_r0를 COLD에서 복원해야 한다.** 바뀐 recorder는 옛 checkpoint를 버리고 세션 시작부터 읽기 때문이다. checkpoint 디렉터리나 Q123 store를 초기화할 때도 같다. 절차는 `RETENTION_CTX_V1_2_RUNBOOK.md`에 있다.
- 첫 배포는 `CTX_V1_PRUNE_ENABLED=false`로 한다. `true`로 바꾸는 것은 dry-run 결과 보고 후 별도 승인 사항이다.

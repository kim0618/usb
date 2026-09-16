# Backtest Cross-PC Workflow

집 PC와 회사 PC에서 번갈아 Historical Backtest 작업을 하기 위한 절차다.
Production deploy, Kiwoom live, Production DB와는 무관한 로컬 작업 규약이다.

---

## 1. 역할 분리

| 저장소 | 담당 | 예 |
| --- | --- | --- |
| Git / GitHub `main` | source code, tests, docs, schema/contract | `backend/app/backtest/workspace/`, `backend/tests/`, 이 문서 |
| Google Drive `1_US-B` | massive raw data, normalized market data, backtest runs/results/reports, DB snapshot, shared state | `market_data/`, `backtest/`, `snapshots/`, `state/` |
| 각 PC의 로컬 `.env` | API key, Kiwoom credential | `MASSIVE_API_KEY` 등 |

규칙:

- Secret은 Git에도 Google Drive에도 저장하지 않는다. `.env`는 각 PC가 직접 관리하며 동기화 대상이 아니다.
- Google Drive 데이터는 repo 밖에 있으므로 Git 추적 대상이 아니다.
- `CURRENT_STATE.json`에는 데이터가 아니라 "어디까지 끝났는가"만 기록한다.

---

## 2. Workspace 경로

공용 root 이름은 정확히 `1_US-B`다.

- Windows: `G:\내 드라이브\1_US-B` (PC마다 drive letter가 다를 수 있음)
- WSL: `/mnt/<letter>/내 드라이브/1_US-B`

코드는 drive letter를 하드코딩하지 않는다. `/mnt` 아래 각 마운트에서 `내 드라이브/1_US-B`,
`My Drive/1_US-B`, `1_US-B`만 정해진 깊이로 확인한다.

- 정확히 1개 발견: 그 경로를 사용
- 0개: `BACKTEST_WORKSPACE_NOT_FOUND`
- 2개 이상: `BACKTEST_WORKSPACE_AMBIGUOUS` (임의로 첫 번째를 고르지 않는다)

탐색이 실패하면 `--workspace-root`로 직접 지정한다. repo 내부 경로는 어떤 경우에도
workspace root로 채택되지 않으며, 탐색이 workspace를 자동 생성하지도 않는다.

```bash
PYTHONPATH=backend .venv/bin/python -m app.dev.backtest_workspace status
PYTHONPATH=backend .venv/bin/python -m app.dev.backtest_workspace \
  --workspace-root "/mnt/h/내 드라이브/1_US-B" status
```

---

## 3. 명령

```bash
# 최초 1회(또는 새 PC에서 폴더 구조 확인)
PYTHONPATH=backend .venv/bin/python -m app.dev.backtest_workspace init

# 작업 시작 시 상태 확인
PYTHONPATH=backend .venv/bin/python -m app.dev.backtest_workspace status

# 이 PC에서 workspace가 정상인지 점검
PYTHONPATH=backend .venv/bin/python -m app.dev.backtest_workspace doctor
```

- `init`은 idempotent하다. 기존 폴더/파일을 삭제하거나 덮어쓰지 않는다.
  `1_US-B` 폴더 자체가 아직 없으면 `--create-root`를 붙인다.
- `doctor`는 root 접근, 필수 디렉터리, 쓰기 권한(partial 후 atomic replace), `workspace.json`,
  `CURRENT_STATE.json`, manifest `quick_check`, writer lock, git HEAD와 `source_commit` 차이를 보고한다.
  실패 항목이 있으면 exit code 1.
- 어떤 명령도 secret을 읽거나 출력하지 않는다.

---

## 4. 작업 시작 절차

1. Google Drive 동기화가 끝났는지 확인한다(트레이 아이콘이 "최신 상태").
2. `git status`로 로컬 미커밋 변경을 확인한다.
3. `git pull --ff-only origin main`
4. `... backtest_workspace doctor`
5. `... backtest_workspace status`로 `current_stage` / `next_stage` / `source_commit`을 읽는다.
6. `source_commit`이 현재 HEAD와 다르면, 그 차이가 무엇인지 확인한 뒤 작업을 시작한다.

`status`의 `source_commit=UNCOMMITTED`는 "그 단계 코드가 아직 push되지 않았다"는 뜻이다.
이 경우 다른 PC에는 해당 코드가 없으므로 먼저 코드 동기화 상태부터 확인한다.

---

## 5. 작업 종료 절차

순서가 중요하다. `CURRENT_STATE`가 push되지 않은 코드를 완료 단계로 가리키면 다른 PC가
재현할 수 없는 상태를 정본으로 믿게 된다.

1. 테스트 실행 (`.venv/bin/python -m pytest backend/tests -q`)
2. 변경 리뷰 (`git diff`, `git diff --check`)
3. `git commit`
4. `git push origin main`
5. push된 commit hash를 얻는다: `git rev-parse HEAD`
6. 그 hash로 `CURRENT_STATE`를 atomic update한다.
7. Google Drive 동기화 완료를 확인한다.
8. 다른 PC는 동기화가 끝난 뒤에 시작한다.

`CURRENT_STATE` 갱신은 5번 이후에만 한다. commit 전에는 `source_commit`을 `UNCOMMITTED`로
두고, 거짓 hash를 적지 않는다.

```python
# PYTHONPATH=backend .venv/bin/python
from pathlib import Path
from app.backtest.workspace import Workspace, update_current_state

workspace = Workspace(Path("/mnt/g/내 드라이브/1_US-B"))
update_current_state(
    workspace,
    stage="MASSIVE_AAPL_1_YEAR_FEASIBILITY",
    status="PASS",
    source_commit="<git rev-parse HEAD 결과>",
    next_stage="HISTORICAL_COLLECTOR_DESIGN",
    notes="1년 분량 수집 가능성 확인. 호출 수/소요 시간 기록.",
)
```

CLI와 라이브러리는 git을 읽기만 한다. `add`, `commit`, `push`, `pull`, `reset`, `stash`,
`rebase`를 자동 실행하지 않는다. Git 작업은 항상 사람이 명시적으로 한다.

---

## 6. 동시 작업 방지 (writer lock)

두 PC에서 동시에 collector/backtest writer가 돌면 Drive 동기화 충돌로 데이터가 깨진다.
`state/writer.lock.json`이 workspace 단위 writer lock이다.

- lock에는 `owner_id`, `pid`, `acquired_at`, `heartbeat_at`, `lease_seconds`, `purpose`가 들어간다.
- 획득은 `O_EXCL`로 하며, 이미 있으면 `BACKTEST_WRITER_LOCK_HELD`로 거절한다.
- "파일이 있으면 영원히 잠김"이 아니다. lease(기본 1800초)가 지나면 `BACKTEST_WRITER_LOCK_STALE`로 보고한다.
- stale이어도 자동으로 지우지 않는다. 사람이 다른 PC 상황을 확인한 뒤 명시적으로 해제한다.

```bash
PYTHONPATH=backend .venv/bin/python -m app.dev.backtest_workspace unlock --force
# lease가 아직 살아 있는 lock까지 깨야 할 때만(다른 PC가 확실히 멈춘 경우)
PYTHONPATH=backend .venv/bin/python -m app.dev.backtest_workspace unlock --force --even-if-live
```

장시간 작업 중에는 `lock.heartbeat()`로 lease를 갱신한다.

---

## 7. 파일 쓰기 계약

Google Drive로 동기화되는 파일은 최종 경로에 직접 스트리밍하지 않는다. 절반만 동기화된
파일이 다른 PC에서는 완성 파일처럼 보이기 때문이다.

1. 같은 디렉터리의 `<name>.partial`에 쓴다.
2. flush + fsync 후 크기를 검증한다.
3. sha256 checksum을 계산한다.
4. `os.replace`로 최종 경로에 atomic하게 교체한다.
5. 최종 파일이 존재하는 것을 확인한 뒤에만 manifest를 `COMPLETE`로 기록한다.

실패하면 `.partial`을 지우고 최종 파일은 건드리지 않는다. `*.partial`은 `.gitignore` 대상이다.

```python
from app.backtest.workspace import safe_write

with safe_write(workspace.root / "market_data/raw/massive/AAPL_2026-09-08.json") as handle:
    handle.partial_path.write_bytes(payload)
# handle.checksum, handle.size는 교체 성공 후에 채워진다
```

---

## 8. Manifest

`state/collector_manifest.sqlite3`는 수집 이력의 정본이다. 이번 단계에서는 schema와 helper만
있고 Historical Collector는 아직 없다.

- 식별: `provider`, `collector_version`, `symbol`, `data_kind`, `timeframe`, `start_date`, `end_date`
- 상태: `PLANNED` → `COLLECTING` → `COMPLETE` / `FAILED`
- `COMPLETE`는 `relative_path`, `checksum`, `row_count`가 모두 있어야 하며 SQLite CHECK로 강제된다.
- `.partial` 경로는 저장할 수 없다.
- WAL을 쓰지 않는다. Drive가 sidecar 파일을 따로 동기화하면 DB가 깨지기 때문이다.

---

## 9. 디렉터리 구조

```text
1_US-B/
  market_data/
    raw/massive/
    normalized/minute/
    normalized/daily/
    metadata/
  backtest/
    runs/
    results/
    reports/
  snapshots/
    paper_trading/
  state/
    workspace.json
    CURRENT_STATE.json
    collector_manifest.sqlite3
    writer.lock.json        # writer 작업 중에만 존재
  logs/
```

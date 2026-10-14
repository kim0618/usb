# Retention V1.2 운영 런북

계약: `RETENTION_CTX_V1_2.md`. 서버 경로는 운영 기준이다. 서버 명령은 항상 `ssh traderj 'bash -s'`로 보내고 hostname 가드를 붙인다.

## 경로와 설정

| 항목 | 값 |
|---|---|
| journal | `/root/usb_runtime/crypto_context/journal` (`CTX_V1_ROOT`) |
| archive | `/root/usb_runtime/crypto_context/archive/{manifests,receipts}` (`CTX_V1_ARCHIVE_DIR`) |
| Q123 runtime / store | `/root/usb_runtime/q123_forward/runtime`, `/root/usb_runtime/q123_forward/store` |
| HOT | `CTX_V1_HOT_HOURS=48`, `CTX_V1_TELEMETRY_HOT_HOURS=168` |
| 삭제 스위치 | `CTX_V1_PRUNE_ENABLED=false` (기본) |
| 실행 | `usb-crypto-context-retention.timer`, 매일 04:40 KST, 결과는 `logs/retention.log` |
| 로컬 COLD | `~/usb_archive/crypto_context/` |

## 1. 오늘 상태 보기

```bash
ssh traderj 'tail -1 /root/usb_runtime/crypto_context/logs/retention.log' | python3 -m json.tool | head -80
```

확인할 항목은 다음과 같다.

- `q123.state`가 `OK`인가
- `prune_eligible`의 크기
- `keep_reasons`의 사유별 분포
- `health`
- `deleted`: raw kind 삭제 수. 스위치가 false인 동안은 항상 0이어야 한다
- `deleted_expired`: telemetry와 storage_stats 중 7일이 지난 파일의 삭제 수. 스위치와 무관하게 매일 동작한다(V1.1 계약)

## 2. 외부 사본 만들기 (운영자 PC, 2~3일마다 권장)

```bash
cd ~/usb && deploy/crypto/ctx-archive-pull.sh
```

이 스크립트는 manifest를 받고, 나열된 파일을 rsync로 pull하고, sha256을 대조한 뒤 receipt를 서버에 올린다. 다시 실행해도 안전하다. 종료 코드가 0이 아니면 출력의 `result`와 `failed`를 본다. 다음 retention 실행부터 receipt가 반영된다. 바로 확인하려면 3절의 수동 실행을 한다.

## 3. 수동 dry-run (삭제 없음)

```bash
ssh traderj 'bash -s' <<'EOF'
[ "$(hostname)" = "trader-j" ] || exit 1
systemctl start usb-crypto-context-retention.service
tail -1 /root/usb_runtime/crypto_context/logs/retention.log
EOF
```

`CTX_V1_PRUNE_ENABLED=false`인 동안 이 실행은 unit에 `--apply`가 있어도 아무것도 지우지 않는다.

## 4. 삭제 켜기 (별도 승인 후에만)

1. 최근 dry-run에서 `q123.state=OK`이고, `prune_eligible`이 예상 범위이며, `keep_reasons`에 처음 보는 사유가 없는지 확인한다.
2. `/root/usb_runtime/crypto_context/config/context.env`에서 `CTX_V1_PRUNE_ENABLED=true`로 바꾼다. daemon-reload는 필요 없다. EnvironmentFile은 실행할 때마다 읽힌다.
3. 3절의 수동 실행으로 `deleted`가 `prune_eligible`과 같은지 확인한다.
4. 되돌리려면 `false`로 바꾼다. 이미 지운 파일은 5절의 복원 절차로만 되살린다.

## 5. COLD에서 복원하기

다음 상황 전에는 복원이 필요하다.

- **Q123 `RECORDER_VERSION`이나 checkpoint schema를 바꾸는 배포.** 새 recorder는 옛 checkpoint를 버리고 세션 시작부터 읽는다. 세션 처음의 segment가 없으면 활성 세션이 UNRECOVERABLE이 되고 Q123 수집이 멈춘다.
- Q123의 `runtime/checkpoints` 또는 `store`를 지우거나 초기화할 때.
- 활성 세션 전체를 다시 읽는 연구 replay(reader.seconds, R0·R1 replay)를 서버에서 돌릴 때. 연구는 원칙적으로 로컬 COLD 사본에서 돌린다.

복원 절차는 다음과 같다. 파일은 불변이고 이름에 세션과 순번이 있어 원래 자리로 그대로 돌아간다.

```bash
# 로컬: 필요한 세션의 context와 wall_r0를 되돌린다 (예: 세션 31cc8177)
cd ~/usb_archive/crypto_context/journal
rsync -a --ignore-existing context/*-31cc8177-* traderj:/root/usb_runtime/crypto_context/journal/context/
rsync -a --ignore-existing wall_r0/*-31cc8177-* traderj:/root/usb_runtime/crypto_context/journal/wall_r0/
```

- 복원한 뒤 서버에서 각 파일의 sha256이 manifest와 같은지 확인한다. `--ignore-existing`이므로 서버에 이미 있는 파일은 건드리지 않는다.
- 복원한 파일은 receipt가 그대로 유효하다. 그래서 삭제 스위치가 켜져 있으면 다음 실행에서 다시 지워진다. Q123 배포가 끝나 checkpoint가 다시 쌓일 때까지는 `CTX_V1_PRUNE_ENABLED=false`로 둔다.

## 6. 디스크 경고

- `health`가 WARNING이면 여유 공간이 1.0GB 미만이고, CRITICAL이면 500MB 미만이다. 이 상태는 경고일 뿐 자동 삭제를 일으키지 않는다.
- 대응 순서는 다음과 같다.
  1. 2절의 pull을 실행한다.
  2. 삭제가 승인돼 있다면 3절을 실행한다.
  3. 그래도 부족하면 다른 대용량 항목을 정리한다. 서버 /root 정리 감사의 후보 목록을 본다.
- liqfwd collector는 여유 공간 300MB 미만에서 스스로 쓰기를 멈춘다.

## 7. 롤백

V1.2 반영 때 만든 백업은 `releases/before-retention-v1-2-20261014/`에 있다. 백업에는 retention.py, `__main__.py`, unit, context.env가 들어 있다. 롤백 순서는 다음과 같다.

1. 이 백업을 원래 위치로 되돌린다.
2. `systemctl daemon-reload`를 실행한다.
3. 새로 생긴 `archive.py`는 두어도 무해하다.

V1.1로 되돌려도 telemetry와 storage_stats의 7일 삭제 규칙은 같다. 달라지는 것은 raw kind뿐이다. V1.1에서는 활성 세션의 raw를 기간과 무관하게 보존하고, 종료 세션의 raw는 7일 뒤 아카이브 없이 지운다.

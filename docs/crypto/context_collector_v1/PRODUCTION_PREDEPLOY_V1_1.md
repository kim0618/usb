# Production Context Collector V1.1 - 운영 배포 직전 준비 (Pre-Deploy)

    identity: ctx-collector.v1.1 + ctx-retention.v1
    branch:   deploy/crypto-context-collector-v1-1 (base origin/main 5d7863b)
    status:   READY_FOR_PRODUCTION_CONTEXT_COLLECTOR_DEPLOY, 배포 0 (사용자 승인 대기)
    scope:    BTCUSDT 전용, 공개 시세만, 키 / 주문 / 계정 / 레버리지 / AUTO 경로 0

이 문서는 운영에 무엇을, 어디에, 어떤 명령으로 올리고 어떻게 되돌리는지를 고정한다. 여기 적힌 서버
명령은 아직 한 번도 실행하지 않았다. 실행은 사용자 승인 뒤에만 한다.

---

## A. V1.5 정본 커밋

`5d7863b feat(crypto): liquidity map V1.5 request ownership and coverage floor` (origin/main).
16파일, 계약 `lm-continuity.v5` (WALL_CONTINUITY_V1_2.md sha256 `c9d9659c...`), 상태 스키마
`ms-v0-state.v1-3`. 16개 전부 V1.5 동결 해시(`SOURCE_SHA256_MTIME.txt`)와 바이트 동일.

## B. Collector 재기준화

`5d7863b` 위에서 V1.1의 Collector 커밋 4개만 cherry-pick했다. V1.5 복사 커밋(`c2c1090`)은 버렸다.
`c2c1090`의 14파일은 `5d7863b`과 차이 0이었다.

    fec8247 research(crypto): prototype lightweight context collector
    335fde8 docs(crypto): freeze context collector V1.1 contract before measurement
    a8a6a1e research(crypto): harden lightweight context collector
    d1c2aed docs(crypto): context collector V1.1 hardening report
    fa59798 ops(crypto): add context collector retention and production unit files
    (+ 이 문서 커밋)

* 중복 V1.5 파일 0. main 밖 경로 변경 0(Collector 패키지, ctx 테스트, ctx 문서, `deploy/crypto_context/`만).
* Collector 경로는 원래 V1.1 커밋 `d20ea19`와 바이트 동일. main history 재작성 0.

## C. Parity (재기준화 후 재실행)

46분 V1.5 녹화(trial3, 2,759표본)를 재기준화한 코드로 다시 재생했다. 원래 V1.1 결과와 키 94개를
비교(시간·경로 키 제외)해 **차이 0**.

| 항목 | 결과 |
|---|---|
| V1.5 파일 hash | 16 / 16 일치 (origin/main) |
| 재생기 충실도 derived / seq / wall | 2,759 / 2,759 / 73,981 동일 |
| request ownership | 기록된 `snapshot_request_id` 전달, 2건 APPLIED (녹화와 같음) |
| late snapshot discard | 녹화에는 자연 발생 0. 단위 테스트(소유권 28건)로 확인, `late_response_can_install=false` |
| coverage refresh floor | 상태파일 `coverage_min_interval_s=10.0`, `safety_refresh_min_interval_s=300.0` |
| telemetry / state schema | `ms-v0-state.v1-3`, `coverage_cooldown_s` 부재, telemetry에 snapshot_request / refresh_applied |
| wall carry | continuity 게이트 5개 동일, 재생 carry 값 동일 |
| wall 값 / flow 값 | 2,758 / 2,759 동일, 어긋난 1건은 세션 flush 전 첫 표본(V1과 같은 읽기 시점 차이) |
| false LIVE/NONE | 0 (게이트가 막은 것 9) |
| RANK_EVICTED 분리 | 584 (TRUE_ENDED 376, OUT_OF_COVERAGE 10, UNKNOWN 5), 분할 완전성 2,759 / 2,759 |
| Q1~Q3 (R0 T2 / G1~G6) | primary 벽 5,516 / 5,516, 에피소드 240 = 240, G1~G6 6 / 6 동일 |

## D. Retention = 7일

`RETENTION_CTX_V1_1.md` (`ctx-retention.v1`, sha256 `bbca1f65...`). 봉인된 영구 파일만, 두 시계
(봉인 mtime, 이름의 UTC 날짜)가 모두 7일을 넘어야 삭제. `.open`, `state/`, `state.disk-*`,
`.writer.lock`, 실행 중 세션의 `session` 헤더, 링크, 이름 불일치 파일은 삭제 대상에서 아예 빠진다.
`.writer.lock`과 `context/`가 없는 경로는 목록을 만들기 전에 거부(exit 2). 기본은 dry run.
별도 oneshot 유닛 + 매일 04:40 KST 타이머, Nice 19, IO idle. 이번 단계에서 운영 삭제 실행 0.

## E / F. 저장량

| | 값 |
|---|---|
| 디스크 점유 / day (V1.1 실측, tmpfs 캐시) | 55.3 MB |
| 7일 | 약 387 MB |
| 최대 보존(두 시계 조건으로 최대 약 8일) | 약 443 MB + 쓰는 중인 1시간 최대 약 24 MB |
| 영구 디스크 쓰기 / day | 760.5 MB (상태파일 갱신 약 18.4 GB/day는 tmpfs) |

## G. Runtime root

`/root/usb_runtime/crypto_context/` (트레이딩 경로와 공유 0)

| 경로 | 용도 |
|---|---|
| `releases/crypto-context-<short>/` | 릴리스 스냅샷(코드 + 계약 문서 + 유닛 원본 + MANIFEST.sha256) |
| `src` -> `releases/crypto-context-<short>` | 현재 릴리스 링크. `PYTHONPATH=src/backend`, 계약 문서는 `src/docs` |
| `journal/` | 영구 저널 = `CTX_V1_ROOT`. writer lock은 `journal/.writer.lock` |
| `journal/state` -> `/run/usb-crypto-context/ctx-state-<hash>` | 휘발 캐시 링크 |
| `logs/collector.log`, `logs/api.log`, `logs/retention.log` | 유닛 stdout / stderr (START / END JSON 수준, 세션당 약 650 B) |
| `config/context.env` | 4개 값: root, cache, port, retention days |

## H. tmpfs state path

`/run/usb-crypto-context` (systemd `RuntimeDirectory=usb-crypto-context`, mode 0700). 시작 시 생성,
중지 시 삭제. 중지 상태에서 API는 `UNKNOWN / NO_CONTEXT_FILE`을 답한다(정답).

## I / J. Port, service name

* port 후보 **8013**, `127.0.0.1` 바인드. 알려진 사용 포트: 8000(backend), 8100(crypto-paper),
  3000(frontend), 8011(LM viewer 후보), 8012(Market Context 패널 API 후보). 실제 충돌 확인은 배포 직전
  서버 `ss -ltnp`로 한다.
* 유닛 4개: `usb-crypto-context.service`(수집기), `usb-crypto-context-api.service`(API),
  `usb-crypto-context-retention.service` + `.timer`.

## K. API

GET만: `/health`, `/snapshot`, `/status`. POST / PUT / PATCH / DELETE = 405. order / account / leverage /
AUTO / arm 라우트 0 (운영 레이아웃 스모크에서 openapi 경로 3개, 해당 경로 404).
nginx `/market-context-api` 추가 0, `NEXT_PUBLIC_MARKET_CONTEXT_BASE_URL` 설정 0, frontend 재빌드 0.
프론트 패널은 8012의 `/api/market-context/*`를 부르며 이 API(8013)와 경로가 다르다. 패널은 OFF로 남는다.

## L. Tests (재기준화 후)

| 범위 | 결과 |
|---|---|
| Collector (`ctx_v1`, retention 18건 포함) | 149 passed, 1 skipped(원본 MC V1 패키지 부재, 설계상) |
| Collector + MS V0 + LM (V1.5 포함) | 657 passed, 3 skipped |
| `tests/crypto` 전체 | 2,148 passed / 21 failed, 실패 21건은 V1.1 기준선과 **ID 동일**(btc_p1 5, btc_p2 4, btc_vol_a 4, multisymbol 5, leverage 1, expert_e1 1, paper_feed_seed 1). Collector / MS V0 / LM 실패 0 |

요구 항목 대응: V1.5 parity, warmup(8), rank eviction, tmpfs state loss(5), reconnect / gap(20),
writer collision(32), disk full(4), BTC-only(24), GET-only(12), Q1~Q3 replay(24), retention(18).

운영 레이아웃 스모크(로컬, 릴리스 목록만 복사, `PYTHONPATH=src/backend`, 실 Binance 공개 스트림 85초):
모듈은 스냅샷에서만 적재, `/health` LIVE, RSS 43 MB, CPU 5%, 두 번째 writer exit 3, ETHUSDT exit 2
(루트 생성 0), API ETH `UNAVAILABLE / COLLECTOR_IS_BTC_ONLY`, SIGTERM으로 `stop_reason=signal` 종료와
봉인 / gzip, prune dry run 삭제 0, DATA_CONTRACT_V0 `sha256_agrees=true`.

## N. BTC-only

CLI `--symbol`이 BTCUSDT가 아니면 루트 생성 전 exit 2. `build()`도 거부. 런타임 상태파일 심볼 검사.
API는 ETH / SOL에 `UNAVAILABLE`. V0 스트림 URL은 `btcusdt` 고정. 유닛은 `--symbol BTCUSDT` 고정.

## O. Cross-service overlap

| 대상 | 겹침 |
|---|---|
| Strategy A / H / E / RVOL / liqfwd | 파일 0, 유닛 0, 경로 0, 포트 0. import 그래프에 strategy / services / api 없음 |
| crypto-paper (8100, `/root/usb_runtime/crypto_paper`) | 파일 0. 별도 소스 스냅샷, 별도 런타임 루트. 터미널 패키지 import 0 |
| frontend / Market Context frontend | 0. 빌드 / env / 소스 미변경 |
| nginx | 0. 127.0.0.1 전용 |
| **공유(읽기만)** | `/root/usb/.venv` 인터프리터와 websockets / httpx / fastapi / uvicorn. 설치 / 변경 0 |
| **공유(호스트)** | 아웃바운드 IP: Binance 공개 엔드포인트(fstream WS 2개, fapi depth REST, limit 1000 = weight 20, 자연 시 시간당 약 10회). Binance 수동 실매매와 같은 IP의 weight 한도를 함께 쓴다 |
| **공유(호스트)** | `systemctl daemon-reload` 1회(설치 / 롤백 때). 다른 유닛 재시작 없음 |

`app.crypto.market_structure_v0`, `app.crypto.liquidity_map`는 저장소에서는 공유 모듈이지만 운영에는
이 릴리스 안의 복사본으로만 올라가 다른 서비스가 읽는 파일과 겹치지 않는다.

## P. Deploy manifest

`stage_release.sh <commit> <out>`이 `RELEASE_FILES.txt`의 경로만 `git show <commit>:<path>`로 꺼내고
`MANIFEST.sha256`과 `SOURCE_COMMIT`을 만든다. 테스트, 연구 산출물, 녹화, 스크린샷, 보고서는 목록에 없다.
아래 sha256은 `fa59798` 기준(이 문서 자신은 릴리스 커밋에서 추가되므로 제외). 마지막 칸은 그 파일을
마지막으로 바꾼 커밋.

| sha256 | source commit | path |
|---|---|---|
| `5286e5edb94b5ce95f05bb983c5dab09a6e7fff4b8b7d86fc7146006094a2238` | 673c105 | backend/app/__init__.py |
| `fbb89ff1f4687ae9477395c91d0aea1cb7133d37a13a19ef73e2632eb699f127` | 3e8d0b6 | backend/app/crypto/__init__.py |
| `9365d7850d616073d97df69db37345490ba050c412ca0d100107dc7a58b8000b` | fa59798 | backend/app/crypto/context_collector_v1/__init__.py |
| `cfcd6fb2de0af09b180e69ee5069a464f96ed2166c91d3c689f481f63fd348ce` | fa59798 | backend/app/crypto/context_collector_v1/__main__.py |
| `2260d6763d670c2550359d3f3e6f4ea621dc162c60982bed47ed6f9262acaef2` | a8a6a1e | backend/app/crypto/context_collector_v1/api.py |
| `f060af6d4e36c12dce07939512c068cc33fd7c25a85080119a8e06216b6a9108` | a8a6a1e | backend/app/crypto/context_collector_v1/collector.py |
| `c6b315bc9f5931389894205b2888baffbfa095666e218b114bd8339b3db29e60` | a8a6a1e | backend/app/crypto/context_collector_v1/context.py |
| `dab9e4264ddd9af0315e06bd84fd965d75d21813ea9c837abb975de9d2873952` | a8a6a1e | backend/app/crypto/context_collector_v1/contract.py |
| `16339885bbf5e234dd67f77ea501dac9271d5c671bd126e83413c35fdee7f1aa` | fec8247 | backend/app/crypto/context_collector_v1/mcv1_vendored/__init__.py |
| `5f684f0a88e078dbfab3832d3d2dab9171cf5735874f0362777078a30f9ebd51` | fec8247 | backend/app/crypto/context_collector_v1/mcv1_vendored/contract.py |
| `106979e09f21690fd36ad33ebdf195003b369130bf14223dbd18632ffb03d08e` | fec8247 | backend/app/crypto/context_collector_v1/mcv1_vendored/flow.py |
| `0ccbfd10c74036fe20d4c76cd678ee20976c061d3c064f990a29993bf7c17ce6` | fec8247 | backend/app/crypto/context_collector_v1/mcv1_vendored/liquidity.py |
| `7309699df43814878dd9c4df4b86dadb83eb69fb466ff3a07aa7d75c00251d78` | fa59798 | backend/app/crypto/context_collector_v1/retention.py |
| `843c7a1f5205b91d13367fefe7c5d0c6158daca563c8ec6622e26fd67846af7c` | a8a6a1e | backend/app/crypto/context_collector_v1/store.py |
| `2012ade47906862aa43a4023002284d3987b7313984c5e2c334741a4df0ef15c` | fec8247 | backend/app/crypto/context_collector_v1/wallr0.py |
| `a8fda4e151a3fa4b7d097edf8be8cf3bdc9f98e259bd65549eeda2ec075c0b1c` | 4bd3d2a | backend/app/crypto/liquidity_map/__init__.py |
| `7795f10bc4c9e4cc485606f80f567c091e3d152fec592c1eccdeabd655cb3e90` | 5d7863b | backend/app/crypto/liquidity_map/checkpoint.py |
| `c029b0a8c686e49efc010794e8f1f5461993e7320182111edd9addddb1a5e12d` | 5d7863b | backend/app/crypto/liquidity_map/continuity.py |
| `b926279f1c464acf54ff94ef0ac5f4f8b1f6e373ef381fd3328a5743c44fd055` | 4bd3d2a | backend/app/crypto/liquidity_map/journal.py |
| `a35e40129968f097693862157fbd5a4a172814e9a647682f70b7b4bf0ae849cd` | 5d7863b | backend/app/crypto/liquidity_map/view.py |
| `4b90c58b7a59829ff539b576a67843567e9bc99128b1eb545b00476d7a445130` | 4bd3d2a | backend/app/crypto/liquidity_map/wallrule.py |
| `af1edc2be7e3434e9af85f952cc18226e9ef946d828656db77e2f6a57969ae5b` | 4bd3d2a | backend/app/crypto/liquidity_map/wallstate.py |
| `4d18d90af1c8402afed41c5dbc529c1904bbf492ec5426b80f60259330d256e1` | 4bd3d2a | backend/app/crypto/market_structure_v0/__init__.py |
| `b5f5354a718bb93b28bdd2c108232811d1eca4febfacf154f8ff7bbf23506090` | 4bd3d2a | backend/app/crypto/market_structure_v0/bands.py |
| `ce4218745a375ec45e19bf5c930082f4cda3053e9b8e9c979b1db9f3da9526ca` | 4bd3d2a | backend/app/crypto/market_structure_v0/book.py |
| `f7283837ef81608c5d5211412e253f7d3efb47b1fa1d4e5b63afb358a24b81f4` | 5d7863b | backend/app/crypto/market_structure_v0/collector.py |
| `9c70fe555ccf0ef2656a45b5600f52aa9db1ab5b22e64da7df7f573ca4a7b55c` | 4bd3d2a | backend/app/crypto/market_structure_v0/contract.py |
| `937b91a9400951ff8f3296ae5de0c682a9d1bc68853cd1ea197278808a56f1d1` | 4bd3d2a | backend/app/crypto/market_structure_v0/envelope.py |
| `41bdaceb1f65c8a7b4e4d6c6288e1ff968f951eb1162e964bb671bf9541cee31` | 4bd3d2a | backend/app/crypto/market_structure_v0/flow.py |
| `c9482565f00480de64b1ad9ec4f92ab4fb10d2ed20b5836f926808c0b80a5261` | 4bd3d2a | backend/app/crypto/market_structure_v0/safety.py |
| `df10961aec8e3dcd206d55932a8c61e406a7e2e22668f2cebfc0b2c51233f40f` | 4bd3d2a | backend/app/crypto/market_structure_v0/store.py |
| `baf1b4007caf894a57cf5e2568f912e1bc36cc67c41443634c261d655cf7cc15` | 4bd3d2a | backend/app/crypto/market_structure_v0/trades.py |
| `ad6401caa8c937bbe8383028763a9019813b31fa06c24ca4bc0fffb7709aa6b4` | 4bd3d2a | backend/app/crypto/market_structure_v0/walls.py |
| `9eed3862860f2a8004e0f6ffc4593841b55d8c303e261ebd6e9e8fdde9b6d52f` | 4bd3d2a | docs/crypto/market_structure_v0/DATA_CONTRACT_V0.md |
| `9d36517c436d3e1824b3430bf7aeb260dbba2c5ddfdcfffb0b1fc878344dd811` | 4bd3d2a | docs/crypto/market_structure_v0/DATA_CONTRACT_V0.sha256 |
| `deaa9db8f0e148b0deb6297f4423a7b5e999de723cefde5bcbb49634aa6a9d87` | 4bd3d2a | docs/crypto/liquidity_map_v1/WALL_RULE_V2.md |
| `935c45429e09db605a673221faf31b382c02ac8fab43cc38e87e63b5c27499bc` | 4bd3d2a | docs/crypto/liquidity_map_v1/WALL_RULE_V2.sha256 |
| `c9d9659c8957501c97e570aaf55d8cf616e3c6733c6a238174c86365031fed44` | 5d7863b | docs/crypto/liquidity_map_v1/WALL_CONTINUITY_V1_2.md |
| `b6d3c2b2cd0ad1985fa4f8abca855a41d66344a5d4ef107356ceb18f61ae391c` | 5d7863b | docs/crypto/liquidity_map_v1/WALL_CONTINUITY_V1_2.sha256 |
| `a9f8becfd18aaf920bc2cc2ad96b4d465c5a1faa3a9e8652d261fdded03297fc` | 335fde8 | docs/crypto/context_collector_v1/CONTRACT_CTX_V1_1.md |
| `46d2fa9fbd1182a9b5cecec77e5ed1d5a139f2decd4b87e95897f9551df0b4a4` | 335fde8 | docs/crypto/context_collector_v1/CONTRACT_CTX_V1_1.sha256 |
| `bbca1f65a6b9d89afd79299a6e01156cd7e8b02ad3749d02811af3579c5ca58c` | fa59798 | docs/crypto/context_collector_v1/RETENTION_CTX_V1_1.md |
| `a22940b36f1893fa87a51a8bf9a78b50403a08d458996829a496b76e4a7dc3d5` | fa59798 | docs/crypto/context_collector_v1/RETENTION_CTX_V1_1.sha256 |
| `ab7d225c2b8f3abcf8c1af5b6142fa08e4d918a90e10b7788839695150f8557a` | fa59798 | deploy/crypto_context/context.env |
| `a16d2184c496d2b037db79a215b5f5c4bfd1896dde3024fa8acc1fd1a15b93b4` | fa59798 | deploy/crypto_context/usb-crypto-context.service |
| `5e138b3d705382237db4dc23eec3c2e7917be422bb2a0ba838deded8d3f62e02` | fa59798 | deploy/crypto_context/usb-crypto-context-api.service |
| `a269343873ad44e8e88aa5c68bd6580b27ad90463a5f7a6c800d89e922581f3e` | fa59798 | deploy/crypto_context/usb-crypto-context-retention.service |
| `67fa1f024ec5ccbf6b8aa9c2614a18792c201ca2ad83c2ccc9910675a8de12e6` | fa59798 | deploy/crypto_context/usb-crypto-context-retention.timer |

운영 설치 위치:

| 릴리스 안 경로 | 설치 위치 |
|---|---|
| `backend/**`, `docs/**` | `/root/usb_runtime/crypto_context/releases/crypto-context-<short>/` (그대로, `src` 링크로 사용) |
| `deploy/crypto_context/context.env` | `/root/usb_runtime/crypto_context/config/context.env` |
| `deploy/crypto_context/usb-crypto-context*.service`, `.timer` | `/etc/systemd/system/` |
| 런타임 디렉터리 | `journal/`(0700), `logs/`, `/run/usb-crypto-context`(systemd가 생성) |

## Q. Rollback

신규 서비스라 되돌리기는 끄고 지우는 것뿐이다. 데이터는 남긴다. crypto-paper / frontend / nginx
재시작 0, 트레이딩 유닛 무접촉.

    systemctl disable --now usb-crypto-context-retention.timer
    systemctl disable --now usb-crypto-context-api.service usb-crypto-context.service
    rm -f /etc/systemd/system/usb-crypto-context.service \
          /etc/systemd/system/usb-crypto-context-api.service \
          /etc/systemd/system/usb-crypto-context-retention.service \
          /etc/systemd/system/usb-crypto-context-retention.timer
    systemctl daemon-reload
    systemctl reset-failed 'usb-crypto-context*' || true
    # /root/usb_runtime/crypto_context/ 는 그대로 둔다 (journal 보존)

릴리스만 이전본으로 되돌릴 때: `ln -sfn releases/<이전> /root/usb_runtime/crypto_context/src` 후
`systemctl restart usb-crypto-context.service usb-crypto-context-api.service` (이 두 유닛만).

확인: 롤백 전후 `usb-crypto-paper` 등 기존 유닛의 `MainPID`와 `ActiveEnterTimestamp`가 같아야 한다.

## R. 실행 예정 명령 (실행하지 않음, 승인 후)

로컬:

    cd <repo> && deploy/crypto_context/stage_release.sh <release_commit> <out_dir>
    # crypto-context-<short>.tgz 와 그 sha256을 기록

서버(`ssh traderj 'bash -s'`, 모든 블록 첫 줄에 hostname 가드):

    # 0. 읽기 전용 사전 확인. 하나라도 어긋나면 중단
    ss -ltnp | grep -E '[:.]8013\b' && exit 1          # 포트 비어 있어야 함
    systemctl list-unit-files 'usb-crypto-context*' --no-legend | grep . && exit 1
    test ! -e /root/usb_runtime/crypto_context || exit 1
    df -BM --output=avail /root | tail -1                # 1,000 MB 이상이어야 진행
    free -m
    findmnt -no FSTYPE /run                              # tmpfs
    /root/usb/.venv/bin/python -c "import websockets, httpx, fastapi, uvicorn"
    for u in usb-crypto-paper usb-crypto-liqfwd usb-backend usb-frontend usb-e-paper usb-e-rvol nginx; do
      systemctl show -p Id -p ActiveState -p MainPID -p ActiveEnterTimestamp "$u"; done > /tmp/ctx_pre.txt

    # 1. 설치 (scp crypto-context-<short>.tgz 를 먼저 releases/ 로)
    install -d -m 0755 /root/usb_runtime/crypto_context/{releases,logs,config}
    install -d -m 0700 /root/usb_runtime/crypto_context/journal
    tar -xzf /root/usb_runtime/crypto_context/releases/crypto-context-<short>.tgz \
        -C /root/usb_runtime/crypto_context/releases
    (cd /root/usb_runtime/crypto_context/releases/crypto-context-<short> && sha256sum -c MANIFEST.sha256)
    ln -sfn releases/crypto-context-<short> /root/usb_runtime/crypto_context/src
    install -m 0644 /root/usb_runtime/crypto_context/src/deploy/crypto_context/context.env \
        /root/usb_runtime/crypto_context/config/context.env
    install -m 0644 /root/usb_runtime/crypto_context/src/deploy/crypto_context/usb-crypto-context*.service \
        /root/usb_runtime/crypto_context/src/deploy/crypto_context/usb-crypto-context-retention.timer \
        /etc/systemd/system/
    systemctl daemon-reload
    systemd-analyze verify /etc/systemd/system/usb-crypto-context*.service \
        /etc/systemd/system/usb-crypto-context-retention.timer

    # 2. 시작 (수집기, API만. 기존 유닛 무접촉)
    systemctl enable --now usb-crypto-context.service
    systemctl enable --now usb-crypto-context-api.service

    # 3. 확인 (70초 뒤)
    curl -s 127.0.0.1:8013/health      # state LIVE
    curl -s 127.0.0.1:8013/status      # state_cache.volatile true, dropped 0
    curl -s -o /dev/null -w '%{http_code}\n' -X POST 127.0.0.1:8013/snapshot   # 405
    ss -ltnp | grep 8013               # 127.0.0.1:8013 하나뿐
    PYTHONPATH=/root/usb_runtime/crypto_context/src/backend /root/usb/.venv/bin/python \
      -m app.crypto.context_collector_v1 prune --root /root/usb_runtime/crypto_context/journal   # dry run, 삭제 0
    du -sh /root/usb_runtime/crypto_context/journal   # 1시간 뒤 약 2.3 MB 증가
    for u in usb-crypto-paper usb-crypto-liqfwd usb-backend usb-frontend usb-e-paper usb-e-rvol nginx; do
      systemctl show -p Id -p ActiveState -p MainPID -p ActiveEnterTimestamp "$u"; done | diff /tmp/ctx_pre.txt -

    # 4. 보존 타이머 (첫 실삭제는 첫 봉인 파일로부터 7일 뒤)
    systemctl enable --now usb-crypto-context-retention.timer

## S. 남은 위험

1. **서버 사실은 미확인**: 디스크 여유 1.1 GB는 V1 시점 기록이고 이번에 다시 재지 않았다(서버 접근 금지).
   포트 8013 비어 있음, venv 패키지, `/run` tmpfs, 메모리 여유도 R절 0단계에서 처음 확인한다.
2. **24시간 연속 실행 기록 없음**. 최장 실측 46분 + 강제 시나리오. 자연 HARD / gap / reconnect는 0회라
   강제로만 확인. V1.5 자체의 24H trial도 미실행.
3. **Binance IP weight 공유**: 수동 실매매와 같은 아웃바운드 IP. 수집기 사용량은 작지만(시간당 약
   weight 200) 강제 재동기화가 반복되면 늘어난다. 재동기화는 백오프가 있다.
4. **공유 venv**: 다른 작업이 `/root/usb/.venv`의 fastapi / websockets / httpx를 바꾸면 이 서비스도 영향.
5. **ABSORPTION_CANDIDATE 52.7%** 발화(계약상 수정 금지, 한계로만 기록). UI가 OFF라 노출 0.
6. **retention**: 7일보다 오래 열려 있는 벽은 OPEN 행을 잃는다(실측상 없음). 삭제는 되돌릴 수 없다.
7. **브랜치 미push**: `deploy/crypto-context-collector-v1-1`은 로컬 커밋. 릴리스 tarball은 커밋에서
   만들지만, 정본으로 남기려면 배포 전에 push(또는 main 병합)가 필요하다. 이 환경에는 GitHub 자격증명이 없다.
8. **logs 무회전**: 세션당 수백 바이트라 실질 위험은 낮지만 logrotate 대상은 아니다.

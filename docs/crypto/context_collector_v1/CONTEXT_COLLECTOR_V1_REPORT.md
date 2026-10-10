# Production Context Collector V1 보고서

    identity: ctx-collector.v1   (code: ctx-collector.code.0.1)
    branch:   research/crypto-context-collector-v1   base: origin/main 5cead43
    scope:    BTCUSDT 전용, 로컬 격리 실측, 배포 0 / push 0

Manual Market Context V1(`mc-display.v1`)의 LIQUIDITY / FLOW 레이어를 운영에서 공급할 수 있는
경량 수집기를 설계하고 구현하고 실측한 기록이다. 결론부터: **V0 상태기계를 한 줄도 바꾸지 않고
저장 정책만 바꿔서 같은 값을 하루 수십 MB로 공급할 수 있다.** 다만 운영 배포 판정은
`VIABLE_FOR_LONGER_LOCAL_TRIAL`이며, 이유는 Q절에 있다.

---

## A. 격리 증명

| 항목 | 값 |
|---|---|
| 원 repo | `/home/tjd618/usb`, branch `main`, HEAD `1c2f288` (origin/main보다 2커밋 앞, 미push 문서 커밋) |
| 원 repo dirty | 작업 시작 시 317줄(`git status --porcelain`), staged 0 |
| 작업 위치 | scratchpad의 **별도 clone**(`git clone --no-hardlinks`), `git worktree add`는 원 repo `.git`에 메타데이터를 쓰므로 쓰지 않음 |
| 기준 commit | `5cead43` = 작업 시점 원 repo의 `origin/main` ref. 다른 세션의 미push 커밋 2개(`53d7078`, `1c2f288`)는 미포함 |
| push 차단 | clone의 origin push URL을 `DISABLED`로 설정 |
| 원 repo 쓰기 | 0. 원 repo의 미추적 파일(MC V1, R0)은 **읽기만** 했고 scratchpad로 복사해 참조 |
| 원 repo 파일과의 교집합 | 이 커밋의 변경은 전부 신규 파일(status A 100%). 원 repo dirty 317개 경로와 교집합 0 (O절) |
| 운영 접근 | ssh / scp / systemctl / nginx / `/root/usb` 접근 0. Binance 공개 시세만 사용, 키 0 |

pytest는 원 repo의 `.venv`가 아니라 `~/.venvs/usb`로, `PYTHONDONTWRITEBYTECODE=1`로 돌렸다.

---

## B. 기존 Research Collector 비용 분해

이번 세션의 실측 3회: `rec1` 1,508 s 4.30 GB/day, `rec0` 1,282 s 4.06 GB/day, K절 trial 3,909 s
4.34 GB/day(모두 gap 0, reconnect 0, drop 0). 표는 `rec1`의 `storage_stats` 값이며, 기존 V0 보고서
(608 s, 4.51 GB/day)와 같은 분포다.

| kind | 비중 | MB/h | MB/day | rows/day | Market Context 화면에 필요 | 연구 재현용 | 판정 |
|---|---|---|---|---|---|---|---|
| `wall` (V0 후보 OPEN/END 전이) | 49.0% | 87.8 | 2,107.6 | 2,624,448 | 아니오. 현재 후보 집합은 상태파일에 있음 | R0 재현에 13%만 필요(N절) | 버림, 13%만 `wall_r0`로 압축 보존 |
| `raw_depth` | 35.2% | 63.1 | 1,514.1 | 897,710 | 아니오. 호가창은 메모리에서 유지 | 북 재생(replay authority) | 버림 |
| `derived` (1초 표본) | 8.4% | 15.0 | 360.1 | 85,870 | 매초 상태파일로 덮어씀 | 일부 필드만 필요 | 버림, 필요 필드만 `context`로 |
| `trade` | 3.8% | 6.8 | 163.2 | 278,060 | 아니오. flow 창은 메모리 | 거래 재생 | 버림 |
| `raw_trade` | 3.0% | 5.4 | 128.6 | 278,060 | 아니오 | 거래 재생 | 버림 |
| `snapshot` | 0.2% | 0.3 | 7.3 | 171 | 아니오 | 북 재생 | 버림 |
| `checkpoint` | 0.2% | 0.3 | 6.9 | 171 | 아니오 | 감사 가속 | 버림 |
| `storage_stats` | 0.2% | 0.3 | 7.1 | 1,374 | 운영 진단 | 아니오 | 유지 |
| `telemetry` | 0.1% | 0.2 | 5.2 | 973 | gap / resync / reconnect 사유 | 품질 이력 | 유지 |
| `session` | 0.0% | 0.0 | 0.1 | 57 | 뷰어가 세션 판정에 읽음 | 세션 경계 | 유지 |
| **합계** | | 179.2 | **4,300** | 4,166,894 | | | |

4.3~4.5 GB/day의 84%는 `wall` 전이와 `raw_depth`다. 둘 다 "나중에 무엇이든 다시 계산할 수 있게"
남기는 연구용 원자료이고, Market Context 화면은 둘 다 읽지 않는다. 뷰어(`liquidity_map.api`)는
정상 경로에서 `session` 기록과 압축 상태파일(`state/collector_state.json`)만 읽는다.

상태파일은 저장 용량이 아니라 **쓰기 대역폭**이다. 매초 덮어쓰고(실측 161~193 KB), 하루 약
14~17 GB를 쓰지만 디스크 점유는 파일 1개다. 운영에서는 tmpfs 배치를 검토할 수 있다(R절).

---

## C. 최소 필수 필드

`mc-display.v1` 6·7·10절과 Market Context R0 7절(Layer B / C)에서 추출했다.

**LIQUIDITY**

| 요구 | 공급 위치 |
|---|---|
| nearest bid / ask wall, notional, qty, distance(bps, %) | `context.liquidity.{ASK,BID}.nearest` (MC V1 `_wall`과 동일 값) |
| wall persistence, source(OWN / CARRIED), continuity status | 같은 곳 `persistence_ms`, `own_persistence_ms`, `persistence_source`, `continuity_status` |
| 4가지 "벽 없음" 구분(OK / NONE / PARTIAL / STALE / UNKNOWN) | `wall_state`, `wall_state_reason` |
| bid / ask depth, 밴드별 coverage, 하한 여부 | `context.depth[]` 4개 밴드 |
| imbalance | `depth[].imbalance_usdt`, `liquidity.band_imbalance` |
| observed coverage(관측 구간) | `context.book.known_low / known_high` |
| 레이어 상태 | `liquidity.state` + `reasons` |
| R0가 읽는 정렬된 V2 벽 전체 | `wall_v2`(화면 의미), `wall_r0`(R0 의미) 전이 스트림 |

thin zone은 MC V1 계약의 필드 목록에 없다. R0의 Q2 "thinner liquidity above"는 ±0.1% 밴드의
ask < bid 비교이고 `depth`로 계산된다. 새 필드를 만들지 않았다.

**FLOW**

| 요구 | 공급 위치 |
|---|---|
| aggressive buy / sell (USDT, BTC), 5s / 15s / 60s | `context.flow.windows.{5s,15s,60s}` |
| flow imbalance | 같은 곳 `imbalance_usdt`, `imbalance_btc` (COMPLETE일 때만 값) |
| coverage와 사유, 하한 여부 | `coverage`, `coverage_reason`, `is_lower_bound` |
| CANCEL_LIKE / CONSUMED_CANDIDATE / UNKNOWN | `context.vanished[]` (그 초에 사라진 벽마다 1행) |
| absorption candidate | `context.absorption` |
| freshness | `flow.trade_age_ms`, `trade_state`, 창별 `state` |

LONG / SHORT / score / 방향 예측 / 자동 진입 필드는 없다. 어휘 검사는 J절.

---

## D. 수집기 구조

```
Binance depth WS (/public)  -+
Binance aggTrade WS (/market) +- V0 Runner (변경 없음, 공개 URL 허용목록 3개)
Binance REST /fapi/v1/depth  -+          |
                                         v
                    V0 Collector 상태기계 (변경 없음)
                    메모리 북 · flow 창 · 벽 후보 · 연속성 장부
                                         | 매초 sample()
                    +--------------------+-------------------------+
                    v                    v                         v
          상태파일 원자 교체        V0 레코드 -> ContextStore        context 단계
          (뷰어 호환, 그대로)       raw / derived / wall 버림        뷰어 읽기 경로 그대로
                                    session / telemetry / stats 유지  -> MC V1 LIQUIDITY·FLOW
                                    wall 중 R0 13% -> wall_r0          -> wall_v2 전이, context 1행
                                                                       -> state/context_latest.json
                                         |
                                         v
                         Read-only API (별도 프로세스, GET 3개)
```

설계 원칙과 실제 구현의 대응:

* **실시간 호가창은 메모리**: V0 `DepthBook` 그대로.
* **raw 장기 저장 금지**: `raw_depth`, `raw_trade`, `trade`, `snapshot`, `checkpoint`는 저장소
  쓰기 단계에서 버린다. 상태기계는 이 사실을 모른다(그래서 V0와 결과가 같다).
* **파생 상태만 저빈도 저장**: 1초 `context` 1행, 벽 bin 전이, R0용 13% 후보 행.

**가장 중요한 선택: 새로 계산하지 않는다.** `ContextCollector`는 `Collector`의 서브클래스이고
값을 정하는 메서드를 하나도 덮어쓰지 않는다. context 단계는 부모 `sample()`이 방금 쓴 상태파일을
**뷰어 자신의 읽기 경로**(`checkpoint.read` -> `journal.latest_session` -> `WallFollower.refresh` ->
`view.snapshot_view`)로 읽고, 그 결과를 **MC V1 코드**(`liquidity_view`, `flow_view`)에 넣는다.
그래서 벽 / flow 패리티는 비교해서 맞춘 것이 아니라 구조상 같은 코드다(F절은 그 확인).

MC V1 패키지(`app.crypto.market_context_v1`)는 작업 시점 다른 세션의 미추적 파일이라 이 브랜치에
없다. 재구현은 "새 정의 금지"에 걸리므로 `mcv1_vendored/`에 **복사**했다. `flow.py`는 바이트 동일,
`contract.py`는 상대 import 깊이 두 줄만 다르고, `liquidity.py`는 HTTP 읽기 함수 2개
(`read`, `_read_async`)만 뺐다. 원본 sha256을 기록했고 테스트가 매번 검증한다.

---

## E. 저장 스키마

루트 하나(`CTX_V1_ROOT`)에 단일 writer. V0 kind 이름과 파일 규칙(`<kind>-<UTC날짜>-<session8>-<seq>.jsonl`)을
그대로 쓴다. 자체 kind 3개는 시간별 봉인 시 gzip(`.jsonl.gz`)으로 압축한다.

| 경로 | 내용 | 형식 |
|---|---|---|
| `session/`, `telemetry/`, `storage_stats/` | V0 레코드 그대로 | 평문 JSONL (뷰어 호환) |
| `state/collector_state.json` | V0 상태파일 그대로 (`ms-v0-state.v1-2`) | 매초 원자 교체 |
| `state/context_latest.json` | 최신 MC V1 LIQUIDITY / FLOW 전체 payload + collector 상태 | 매초 원자 교체 |
| `context/` | 1초 1행 | gzip JSONL |
| `wall_v2/` | 화면 의미의 V2 bin 전이 | gzip JSONL |
| `wall_r0/` | R0 의미의 V0 후보 행(13%, 13필드) | gzip JSONL |

모든 레코드는 V0 envelope(`version`, `session_id`, `seq`, `kind`, `receive_ms`, `mono_ns`, `payload`)을
쓰고 **V0 레코드와 같은 seq 열을 공유**한다. 그래서 R0가 했던 "seq로 병합"이 그대로 성립한다.

**`context` payload** (`record: CTX_V1_SNAPSHOT`)

    sample_index, sample_ms, v0_derived_seq
    collector    {state, reasons, session_age_ms, observation_since_ms, is_rollup_of_layers:false}
    book         {state, viewer_state, age_ms, generation, last_invalidation,
                  mid, best_bid, best_ask, spread_bps, known_low, known_high}
    depth[4]     {band_pct, bid_coverage, ask_coverage, bid_notional, ask_notional,
                  bid_is_lower_bound, ask_is_lower_bound, imbalance_usdt}
    liquidity    {state, reasons, wall_set_coverage, candidates, band_imbalance,
                  ASK|BID {wall_state, wall_state_reason, walls_selected, walls_shown,
                           nearest {price, qty_btc, notional_usdt, distance_bps, multiple,
                                    persistence_ms, own_persistence_ms, persistence_source,
                                    continuity_status, bin_low, bin_high, coverage,
                                    first_seen_ms, generation}}}
    flow         {state, reasons, trade_connected, trade_age_ms, trade_state,
                  windows.{5s,15s,60s} {state, coverage, coverage_reason, trades, buy_usdt,
                  sell_usdt, buy_btc, sell_btc, is_lower_bound, imbalance_usdt, imbalance_btc}}
    vanished[]   {state, reason, side, bin_low, bin_high, price, notional_usdt, last_seen_ms,
                  observed_at_ms, path_samples, mid_path_touched_bin}
    absorption   {state, reason[, side, window, aggressive_usdt, wall_notional_usdt, wall_price,
                  bin_low, bin_high, wall_persistence_ms]}
    open_v2_bins {ASK, BID}

밴드와 flow 창의 금액은 **관측값**이다. COMPLETE일 때 정본값과 같고(테스트로 확인), PARTIAL일 때
하한이며 `is_lower_bound`가 그렇게 말한다. 두 번 쓰지 않으려는 선택이다.

**`wall_v2` payload** (`record: CTX_V1_WALL_V2`): `event` OPEN / CHANGE / CLOSE, `side`, `bin_low`.
OPEN은 그 bin의 lead 후보 전체 필드, CHANGE는 `price`, `coverage`, `generation`, `bin_members`,
`persistence_source` 중 바뀐 것과 그때의 notional / distance, CLOSE는 사유
(`NOT_SELECTED` / `NO_USABLE_READING` / `SESSION_END`). notional은 거의 매초 움직이므로 전이로
쓰지 않는다(가장 가까운 벽의 notional은 매초 `context`에 있음).

**`wall_r0` payload**: `v0_seq` + `event, status, side, price, qty, notional, multiple,
first_seen_ms, persistence_ms, coverage, generation, local_average, neighbours`. 근거는 N절.

---

## F. Wall / Flow 패리티

세 겹으로 확인했다.

**1. 같은 입력, 같은 상태기계 (오프라인 재생).** 연구 수집기가 실제로 남긴 원자료(raw depth,
스냅샷, raw trade, 연결 이벤트)를 envelope seq 순서로 다시 먹이는 재생기를 만들었다. 재생기 자체의
충실도부터 확인했다(A). 그다음 같은 입력을 운영 수집기에 넣었다(B).

| 검사 | rec1 (25분) | rec0 (22분, 독립 연결) |
|---|---|---|
| A. V0 재생 derived payload 동일 | 1,499 / 1,499 | 1,274 / 1,274 |
| A. V0 재생 seq 동일 | 1,499 / 1,499 | 1,274 / 1,274 |
| A. V0 재생 wall 행 동일 | 45,793 / 45,793 | 34,906 / 34,906 |
| B. 운영 수집기 내부 derived 동일 | 1,499 / 1,499 | 1,274 / 1,274 |
| B. context 읽기 = 실제 `liquidity_map.api.snapshot` (같은 시각) | 1,498 / 1,499 | 1,273 / 1,274 |
| B. LIQUIDITY = 원본 MC V1 `liquidity_view` | 1,498 / 1,499 | 1,273 / 1,274 |
| B. FLOW(소멸 판정 / absorption 포함) = 원본 MC V1 `flow_view` | 1,498 / 1,499 | 1,273 / 1,274 |

어긋난 1건은 둘 다 **첫 표본**이다. 운영 수집기는 첫 표본을 세션 기록이 디스크에 flush되기 전에
읽으므로 `SYNCING / SESSION_RECORD_NOT_YET_FLUSHED`를 내고, 비교용 호출은 flush 뒤에 돌았다.
실운영에서도 첫 1초는 SYNCING이며, 이것은 계산 차이가 아니라 읽은 시점 차이다.

**2. 커밋된 테스트.** 합성 세션에서 V0 수집기와 운영 수집기를 같은 입력(gap, resync, trade 재접속,
중복 trade 포함)으로 나란히 돌려 `derived`, `wall`, `telemetry`, raw 전부와 상태파일이 같음을 확인한다
(상태파일은 seq 번호만 다르며, context 행이 같은 seq 열을 쓰기 때문이다). 매초 context 읽기와 실제
뷰어 라우트의 동일성, `lm-wall.v2`를 상태파일 행에 손으로 적용한 결과와의 동일성, 5 / 15 / 60초 창의
수기 합계도 테스트한다.

**3. MC V1 golden.** 원본 MC V1 코드로 11개 시나리오 33개 판독의 출력을 만들어
`ctx_v1_mc_golden.json.gz`(19 KB)로 고정했다. CANCEL_LIKE, CONSUMED_CANDIDATE, UNKNOWN(사유 3종),
ABSORPTION_CANDIDATE(양측), NONE(사유 3종)이 모두 들어 있고 복사본이 전부 바이트 동일하게 재현한다.

threshold sweep 없음. `lm-wall.v2`, `lm-continuity.v4` 규칙과 V0 상수는 한 글자도 바꾸지 않았다.

---

## G. 품질 상태

레이어 상태는 MC V1 어휘 그대로(LIVE / STALE / PARTIAL / UNKNOWN, NEUTRAL 없음). 여기에
**수집기 프로세스 자체**에 대한 단어 `collector.state`를 따로 붙였다. 레이어를 합친 판정이 아니며
(`is_rollup_of_layers: false`) 레이어 상태를 대체하지 않는다.

| collector.state | 조건 |
|---|---|
| SYNCING | 세션 기록 flush 전 / 북 UNSYNCED / flow 창이 WARMUP 중 / 마지막 HARD 전이(또는 세션 시작) 후 10초 미만 |
| LIVE | LIQUIDITY와 FLOW 둘 다 LIVE |
| PARTIAL | 하나라도 PARTIAL (예: 재접속 뒤 60초 창이 아직 차지 않음) |
| STALE | 뷰어가 STALE, 또는 레이어 STALE. API는 읽는 시점에 3초 넘은 기록과 끝난 세션을 STALE로 내린다 |
| UNKNOWN | 판독 없음, 상태파일 손상, 계산 예외, 레이어 UNKNOWN |

**SYNCING을 따로 둔 이유가 실측으로 드러났다.** MC V1의 벽 규칙은 R4(관측 10초 이상)가 있어서
재시작이나 HARD resync 직후 10초 동안 어떤 벽도 자격이 없다. 그 사이 MC V1 LIQUIDITY 레이어는
`LIVE`이고 벽은 `NONE`("완전한 관측에서 자격 있는 벽이 없음")이라고 말한다. 실제로는 벽이 있다.
고정 계약이라 레이어 값은 그대로 두고, `collector.state = SYNCING / WALL_PERSISTENCE_WARMUP`으로
같은 행에서 경고한다.

**실 Binance 스트림 장애 주입** (330초, 별도 루트, API 1초 폴링):

| 주입 | 결과 |
|---|---|
| depth 프레임 1개 누락 | gap(`GAP_PU_MISMATCH`) -> REST resync 120 ms -> SYNCING(벽 warmup) 10초 -> LIVE |
| depth 소켓 예외 | websockets 종료 핸드셰이크가 9.2초 걸림. 그 사이 V0 stale(2초) -> REST resync가 3번 반복되며 스냅샷만 있는 북이 1~2초씩 SYNCED로 보임(MC LIQUIDITY가 잠깐 LIVE/NONE). **collector.state는 내내 SYNCING**. 재접속 후 10초 뒤 LIVE |
| trade 중복 전달 | tape `duplicates: 1`, flow 합계 변화 없음, 상태 변화 없음 |
| trade 소켓 예외 | 마지막 체결 후 `TRADE_STALE_MS` 5초에 FLOW UNKNOWN -> 재접속 -> PARTIAL(`RECONNECT`) -> 60초 뒤 LIVE. 그 5초는 V0 고정 신선도 한계 |
| 두 번째 writer | 운영 수집기 exit 3 `REFUSED / WRITER_LOCK_HELD`, V0 수집기 `StoreLocked`. 둘 다 holder pid / session 표시 |
| kill -9 | API가 3.4초 뒤 전 상태 STALE. 재시작 세션이 고아 `.open` 파일 봉인(context 33행 보존) 후 3개 압축, SYNCING으로 시작 |
| 정상 종료 | latest 파일 `session_ended: true`, API `SESSION_ENDED`로 STALE |

---

## H. API

수집기와 분리했다. 비교:

| | 분리 (채택) | 통합 |
|---|---|---|
| 장애 격리 | API 재시작이 수집을 끊지 않음, 수집기 디스크 문제가 API 프로세스를 죽이지 않음 | 한쪽 장애가 다른 쪽을 멈춤 |
| 수집기 메모리 | FastAPI 미적재(테스트로 확인) | 웹 프레임워크가 수집 루프와 같은 이벤트 루프 |
| 지연 | 파일 1개 읽기, 실측 p50 3.66 ms | 메모리 직접, 약간 빠름 |
| 단점 | 최신값이 최대 1초 늦음(샘플 주기와 같음) | 없음에 가깝지만 결합도 상승 |

`GET /health`(상태, writer holder pid / session / 생존 여부), `GET /snapshot`(최신 LIQUIDITY / FLOW,
`?symbol=ETHUSDT|SOLUSDT`는 BTC 루트를 읽지 않고 UNAVAILABLE), `GET /status`(세션, 디스크, 저장 통계).
POST / PUT / PATCH / DELETE는 405. 주문 / 계정 / 포지션 경로 없음. API는 아무것도 쓰지 않는다
(테스트가 루트 트리 해시 전후 비교). **오래된 판독을 현재값으로 내보내지 않는다**: 3초 초과 또는
끝난 세션이면 payload 안의 모든 `state` / `*_state`를 STALE로 내리고(중첩된 `journal.feed_state`까지)
absorption 후보는 NONE으로 바꾸며, 값 자체는 표시용으로 남긴다.

기존 MC V1 패널도 이 루트를 그대로 읽을 수 있다(`MS_V0_ROOT`를 이 루트로 지정). 뷰어가 읽는 것이
전부 남아 있기 때문이다.

---

## I. Writer / Storage 안전

| 요구 | 구현 | 증거 |
|---|---|---|
| single writer | V0 `WriterLock`(flock + holder payload) 상속 | 단위 테스트 + 실 프로세스 거부 2건 |
| holder 표시 | lock 파일의 pid / session_id / started_ms, API `/health` | 테스트 |
| lock 파일 교체 시 fail closed | V0 inode 재확인 상속, latest 파일 쓰기도 거부 | 테스트 |
| restart recovery | V0 `recover_orphans` + 자체 kind 재압축(`compact_root`) | 테스트 + 실 kill -9 |
| partial file | 마지막 줄 찢김은 잘라내고 바이트 수 보고, 내부 손상은 시작 거부 후 lock 반환 | 테스트 |
| atomic state write | 상태파일 / latest 파일 모두 임시파일 + `os.replace` | 테스트 |
| 압축 원자성 | `.gz.tmp` -> fsync -> rename, 그 뒤에만 평문 삭제. 실패 시 평문 유지 | 테스트 |
| disk full | flush 실패 -> `StorageFailed` -> 러너가 `storage_error`로 정지, 상태파일이 늙어 STALE | 테스트 |
| trading service 무영향 | 별도 프로세스 / 별도 경로 / 공유 import 0 | 격리 테스트 |

**V0에서 발견한 공백 1건(V0는 수정하지 않음).** V0 `Runner._sampler`는 `store.tick()`에서
`StoreAuthorityLost`만 잡는다. 디스크 full 같은 `OSError`는 sampler 태스크만 조용히 죽이고 프로세스는
산다. V0는 raw depth가 1 MiB 버퍼를 1분 안에 채워 persist worker가 결국 멈추지만, raw를 버리는 이
수집기에서는 그 버퍼가 거의 차지 않아 **살아 있지만 아무것도 내지 않는 프로세스**가 된다.
`ContextRunner`가 이 경로를 잡아 정지시킨다.

---

## J. 테스트

신규 테스트 6개 파일(+ 픽스처 2개) **107 passed, 1 skipped**(skip은 원본 MC V1 패키지가 브랜치에 없을 때의 직접 비교, 설계상).
기존 MS V0 + Liquidity Map 489개와 합쳐 596 passed. `tests/crypto` 전체는 2,087 passed /
21 failed이고, 21건은 **기준 commit 5cead43의 깨끗한 체크아웃에서도 같은 21건이 실패**한다
(btc_p1 / p2 / vol_a freeze 파일, multisymbol, expert_e1, leverage, paper_feed_seed). 회귀 0.

| 요구 | 테스트 |
|---|---|
| wall parity | `test_the_liquidity_reading_is_the_viewer_route_for_the_same_root`, `test_nearest_walls_are_lm_wall_v2_applied_to_the_live_candidate_set`, golden |
| flow parity | `test_fed_the_same_input_the_v0_state_machine_behaves_identically`, golden |
| rolling windows | `test_rolling_windows_sum_exactly_the_trades_inside_each_window` |
| book resync / gap | `test_a_gap_is_never_live_and_a_hard_resync_restarts_the_persistence_window` |
| stale | `test_a_silent_depth_stream_is_invalidated_not_served`, `test_a_silent_trade_stream_is_never_read_as_zero_flow`, API floor 테스트 |
| partial | `test_a_trade_reconnect_is_unknown_then_partial_never_syncing` |
| reconnect | 같은 테스트 + 실 장애 주입 |
| duplicate trade | `test_a_duplicate_trade_is_not_counted_twice` |
| writer collision | `test_a_second_writer_is_refused_and_told_who_holds_the_root` |
| restart | `test_a_restart_in_the_same_root_is_a_new_session_and_never_inherits_walls`, `test_a_crashed_session_is_recovered_compressed_and_its_torn_line_dropped` |
| corrupted state | `test_interior_damage_refuses_to_start_and_releases_the_lock`, `test_an_unreadable_state_file_is_unknown_never_a_reading`, API unreadable 테스트 |
| disk write failure | `test_a_failing_flush_raises_storage_failed_rather_than_dying_quietly`, `test_the_runner_stops_with_the_storage_error_instead_of_a_silent_dead_sampler`, 압축 / latest 실패 테스트 |
| API read-only | `test_the_app_registers_get_routes_only`, 405 매개변수 테스트, `test_serving_writes_nothing_to_the_root` |
| Q1~Q3 재현 | `test_ctx_v1_reader.py`: R0 재생 규칙을 재진술해 연구 쌍둥이의 전체 V0 wall 스트림에 적용한 결과와 `reader.seconds()`의 `walls_r0`가 매초 동일(gap, HARD resync, 벽 제거 포함) |
| no order / live / account import | `test_ctx_v1_isolation.py`: AST import 검사(상대 import 해석), 새 인터프리터에서 실제 적재 모듈 전수, 환경변수 키, URL / 자격증명 / 방향 어휘 |

AST / import graph 결과: 패키지 전체가 `app.crypto.market_structure_v0`, `app.crypto.liquidity_map`
(단 `liquidity_map.api` 제외) 외의 `app.*`를 import하지 않는다. 새 인터프리터에서 수집기만 적재하면
`app.crypto.paper / live / terminal / c1 / research`, `app.api`, `app.services`, `app.strategy*`,
`app.backtest`, `fastapi`, `starlette`가 하나도 로드되지 않는다. 네트워크는 V0 러너의 공개 URL
허용목록 3개뿐이며 이 패키지는 URL 문자열을 하나도 갖지 않는다.

---

## K. 1시간 실측 Trial

2026-10-10 18:02:04~19:07:10 KST. 연구 수집기(V0 그대로)와 운영 수집기를 **같은 시간에 독립 연결로**
나란히 돌렸다. 운영 쪽은 API 프로세스와 5초 간격 모니터(RSS, CPU, `/snapshot`, `/health`)를 붙였다.
코드 sha256은 실행 디렉터리의 `code_sha256.txt`에 남겼다.

앞선 두 번의 시도는 중간에 멈추고 다시 시작했다. 첫 시도는 HARD 전이 인식 결함(사유 시각이 null인
전이를 매초 새 전이로 봄, 고친 뒤 테스트 추가)이, 둘째 시도는 `wall_r0` 추가가 이유였다. 아래 수치는
최종 코드로 돈 셋째 실행이다.

| 항목 | 연구 수집기 | 운영 수집기 |
|---|---|---|
| 실행 시간 | 3,908.8 s | 3,905.8 s |
| 직렬화 바이트(압축 전) | 196.54 MB | 28.86 MB |
| 종료 시 디스크 점유 | 196.78 MB | **2.64 MB** |
| 행 수 | 194,385 | 26,205 |
| 큐 최대(한도 8,192) | 567 | 688 |
| dropped | 0 | 0 |
| reconnect / gap | 0 / 0 | 0 / 0 |
| resync | 4 (시작 1 + coverage-edge SOFT 3) | 4 (같음) |
| trade stale 사건 | 1 | 1 |
| flush 평균 / 최대 (ms) | raw_depth 0.06 / 3.31 | context 0.02 / 0.94 |
| fsync 평균 / 최대 (ms) | wall 3.18 / 43.4 | context 2.13 / 10.5, 전 kind 최대 24.4 |
| 압축 | 없음 | 25.97 MB -> 1.91 MB (13.6배) |

API(782회 폴링, 오류 0): `/snapshot` p50 3.66 ms, p95 5.11 ms, 최대 17.7 ms. `/health` p50 2.03 ms,
p95 2.95 ms, 최대 6.35 ms.

신선도: 읽는 시점의 최신 파일 나이 p50 530 ms, p95 988 ms, p99 1,014 ms. 실행 중 표본 간격 최대
1,007 ms였고, 시간별 봉인과 gzip 압축(+3,593 s)에서도 sampler가 밀리지 않았다. 3초를 넘어 STALE로
내려간 폴링은 종료 단계의 1건뿐이다.

context 레코드 3,899개의 `collector.state`: LIVE 3,779(96.9%), PARTIAL 59(trade stale 뒤 60초 창이
다시 차는 동안), SYNCING 60(시작 워밍업), UNKNOWN 1(세션 기록 flush 전 첫 표본). context 단계 계산은
표본당 평균 6.67 ms, 최대 40.0 ms.

MC V1 판정: 소멸 1,229건 = CANCEL_LIKE 1,221 / CONSUMED_CANDIDATE 1 / UNKNOWN 7(전부 SOFT refresh의
generation 변경). ABSORPTION_CANDIDATE 1,890 / 3,898 표본(48.5%). 둘 다 R절 2, 3번의 근거다.

**MC V1 원본 호환 확인**: 실행 중인 운영 루트에 원본 Liquidity Map 뷰어와 원본 MC V1 코드를 그대로
붙였더니 LIVE, 벽 ASK 82,900 / BID 82,742.3으로 수집기의 latest 파일과 같은 값을 읽었다.

---

## L. MB/day 추정

| | 연구 수집기 | 운영 수집기 | 배수 |
|---|---|---|---|
| **디스크 점유 / day** | **4,349.6 MB** | **58.4 MB** | **74배** |
| 직렬화 바이트 / day (압축 전) | 4,344.3 MB | 638.5 MB | 6.8배 |
| rows / day | 4,296,681 | 579,672 | 7.4배 |
| bytes / hour (디스크) | 181.2 MB | 2.43 MB | |

기존 보고서의 4.51 GB/day 대비로는 77배다.

운영 수집기의 디스크 점유 내역(per day): `context` 25.1 MB, `wall_r0` 15.0 MB, `storage_stats`
8.3 MB(평문), `wall_v2` 6.2 MB, `telemetry` 3.0 MB(평문), `session` 0.06 MB, 상태파일 2개는 고정
약 0.2 MB. 진행 중인 한 시간은 평문이라 순간 점유가 최대 약 24 MB 더 있다(봉인 시 압축).

`wall_r0`를 끄면(R절 1번에서 화면 의미를 고르면) 43.4 MB/day, 100배다. 거래가 많은 날은 `context`가
아니라 V0 쪽 raw trade가 늘던 구조라 운영 수집기는 거래량에 거의 비례하지 않는다(context는 초당 1행
고정, wall 전이는 호가 구조에 비례).

---

## M. RSS / CPU

| | 연구 수집기 | 운영 수집기 | API |
|---|---|---|---|
| RSS 최소 / 최대 | 39.6 / 42.9 MB | 39.2 / 43.5 MB | 26.7 / 49.0 MB |
| CPU 평균 | 3.71% | 4.05% | 0.31% |
| CPU p95 / 최대 | 4.79% / 6.79% | 5.19% / 9.79% | 0.40% / 0.60% |
| CPU 누적 | 144.8 s | 157.7 s | 12.0 s |

RSS는 둘 다 증가 추세 없이 40 MB대에서 머물렀다. 운영 수집기가 CPU를 약 9% 더 쓴다. raw 직렬화는
아꼈지만 매초 상태파일(약 186 KB)을 뷰어 경로로 다시 읽고 MC V1 계산과 전체 V2 선택을 하는 데 평균
6.67 ms를 쓰기 때문이다. 같은 코드로 패리티를 얻은 대가이며, 줄이는 방법은 R절 8번.

---

## N. Q1~Q3 재사용 가능성

R0의 Q1~Q3는 T1(구조 x 벽 x flow), T2(벽 x flow), T3(구조 x flow, 1분)로 나뉜다.

* **T3**는 1분봉과 taker 분할만 쓴다. 공개 klines 아카이브로 이미 가능하며 이 수집기와 무관하다.
* **T1의 구조(Layer A)**도 공개 klines에서 R1 엔진으로 만든다. 수집기 범위 밖이다.
* 수집기가 책임질 것은 **Layer B / C를 초 단위로 남기는 일**, 즉 T2와 T1의 벽 / flow 절반이다.

**결과: R0의 T2 프레임이 이 저널만으로 정확히 재현된다.** R0 자신의 `journal.replay()`를 연구
저널에 돌린 결과와, 이 수집기 저널(`context` + `wall_r0`)을 커밋된 `reader.seconds()`로 읽은 결과를
비교했다.

| 비교 | rec1 | rec0 |
|---|---|---|
| mid, best bid / ask, book state, generation, ±0.1% 밴드 notional / coverage / imbalance, 60초 flow coverage / imbalance | 1,498 / 1,498 전부 동일 | 1,273 / 1,273 전부 동일 |
| 초마다 측면별 V2 벽 목록(dict 전체) | 2,996 / 2,996 동일 | 2,546 / 2,546 동일 |
| T2 에피소드 | 161개 동일 | 139개 동일 |
| G1~G6 행(판정, 제외 사유 포함) | 6 / 6 동일 | 6 / 6 동일 |

(행 비교에서 `i_dec`는 프레임 인덱스라 첫 표본 1개만큼 일정하게 1 밀린다. 내용은 동일하다.)

**처음에는 재현되지 않았다. 그 이유가 이 절의 핵심이다.** 화면용 `wall_v2`만으로 만든 프레임은
측면-초 2,996개 중 394개만 R0와 같았고 T2 에피소드는 161개 중 99개만 겹쳤다. 분해해 보니 연속성
규칙의 영향은 작고(42개), 거의 전부가 **값의 시점** 차이였다.

* R0의 재생은 V0 `wall` 스트림에서 후보를 재구성하므로 각 후보의 **OPENED 행 값**(열릴 때의
  notional / multiple)으로 `lm-wall.v2`를 적용한다. V0는 그 행을 갱신하지 않는다.
* 뷰어와 MC 패널은 상태파일의 **현재 값**으로 같은 규칙을 적용한다.

같은 규칙, 다른 입력 시점이다. 계약 문구(R0 7절 "ordered V2 walls with price, notional ...")는 어느
쪽인지 정하지 않았고, R0 구현은 저널 형식 때문에 OPENED 행을 썼다.

**해결은 "필요한 것만 최소 event로 추가".** OPENED 행 값은 변하지 않으므로, 열릴 때 R1(notional)이나
R2(multiple)를 못 넘은 후보는 R0 방식으로는 평생 선택될 수 없다. 그래서 R0에 필요한 것은 V0 `wall`
행 전체가 아니라 **열릴 때 R1과 R2를 넘은 후보의 행과, 바로 그 후보들의 종료 행**뿐이다. 그 행들을
R0 선택이 읽는 13개 필드와 V0 seq로만 남긴 것이 `wall_r0`이다. V0 wall 행의 13%, 압축 약
12 MB/day. 거기에 `context`마다 V0 `derived` seq(`v0_derived_seq`)를 남겨 R0의 PIT 병합 규칙
("derived의 seq보다 작은 wall 행만")을 그대로 적용할 수 있게 했다.

Q별 필요 필드 대조:

| 질문 | 필요 | 저널에서 |
|---|---|---|
| Q1 저항 + ask 벽 유지 / 보충 + 매수 공격 + 진행 실패 | 구조 레벨(T1), 벽의 초별 존재, 60초 flow imbalance, mid | 구조: klines. 존재 / 보충(G3 `gap_inside`): `wall_r0` 또는 `wall_v2`. flow / mid: `context` |
| Q2 돌파 + ask 소비 + 매수 공격 + 위쪽 얇음 | 벽 소멸 + mid가 bin 통과, flow, ±0.1% ask < bid | 위와 같음 + `depth` |
| Q3 지지 측 대칭 | 대칭 | 대칭 |

**빠진 필드: 없다.** 단, **결정이 하나 필요하다**(R절 1번): 앞으로의 Q1~Q3 전향 연구가 벽을
OPENED 행 값(R0 구현과 동일)으로 볼지, 현재 값(화면과 동일)으로 볼지. 저널은 둘 다 담으므로 계약
개정 없이 어느 쪽이든 가능하지만, 고르는 것은 새 연구 계약의 일이고 여기서 정하지 않았다.

---

## O. 변경 파일

전부 신규. 기존 tracked 파일 수정 0줄.

    backend/app/crypto/context_collector_v1/__init__.py
    backend/app/crypto/context_collector_v1/__main__.py
    backend/app/crypto/context_collector_v1/api.py
    backend/app/crypto/context_collector_v1/collector.py
    backend/app/crypto/context_collector_v1/context.py
    backend/app/crypto/context_collector_v1/contract.py
    backend/app/crypto/context_collector_v1/reader.py
    backend/app/crypto/context_collector_v1/store.py
    backend/app/crypto/context_collector_v1/wallr0.py
    backend/app/crypto/context_collector_v1/mcv1_vendored/__init__.py
    backend/app/crypto/context_collector_v1/mcv1_vendored/contract.py
    backend/app/crypto/context_collector_v1/mcv1_vendored/flow.py
    backend/app/crypto/context_collector_v1/mcv1_vendored/liquidity.py
    backend/tests/crypto/ctx_v1_fixtures.py
    backend/tests/crypto/ctx_v1_mc_golden.json.gz
    backend/tests/crypto/test_ctx_v1_api.py
    backend/tests/crypto/test_ctx_v1_collector.py
    backend/tests/crypto/test_ctx_v1_isolation.py
    backend/tests/crypto/test_ctx_v1_reader.py
    backend/tests/crypto/test_ctx_v1_store.py
    backend/tests/crypto/test_ctx_v1_vendored_parity.py
    docs/crypto/context_collector_v1/CONTEXT_COLLECTOR_V1_REPORT.md

---

## P. 커밋

`research(crypto): prototype lightweight context collector`, 격리 clone의
`research/crypto-context-collector-v1` 브랜치. push 0, merge 0, 배포 0.

---

## Q. 운영 준비도

판정: **B. VIABLE_FOR_LONGER_LOCAL_TRIAL**

가능하다는 근거: 저장량 74(디스크 기준)배 절감, 값은 연구 수집기 / 뷰어 / MC V1과 구조적으로 같고
두 녹화에서 표본 단위로 확인, 장애는 전부 비-LIVE 상태와 사유로 끝남, writer 안전 상속, 운영 계정 /
주문 경로 접근 불가 증명.

C가 아닌 이유:

1. **24시간 실측이 없다.** 1시간 실측에는 자연 gap / reconnect가 0건이었다(gap 0, reconnect 0, 자연 사건은 trade stale 1회와 SOFT refresh 3회) 있었다. 장애 경로는
   주입으로만 확인했다. Liquidity Map V1.5 판단과 같은 기준이다.
2. **이 브랜치의 V0 수집기는 V1.4다.** 스냅샷 요청 소유권(V1.5, `lm-continuity.v5`)은 원 repo의
   미커밋 변경에만 있다. V1.4에는 "데드라인으로 포기한 refresh의 늦은 스냅샷이 건강한 북에 설치되는"
   알려진 결함이 남아 있다(REST가 1,000 ms를 넘을 때만 도달, 실측 75~485 ms). 운영 수집기는 V1.5가
   커밋된 V0 위에서 돌아야 한다. 이 수집기는 V0를 서브클래스할 뿐이라 코드 변경 없이 따라간다.
3. **MC V1 원본이 미커밋이다.** 복사본은 golden으로 묶여 있지만, 원본이 커밋되면 복사본을 지우고
   import로 바꿔야 한다.
4. **운영 화면이 아직 이 레이어를 소비하지 않는다.** 2026-10-10 재판정대로 운영 라우트에 슬롯이 없고
   프리뷰 라우트는 sentinel이 주문을 막는다. 수집기만 올려서는 화면이 바뀌지 않는다.
5. **운영 호스트 디스크.** 회수 후 1.1 GB. 이 수집기는 하루 약 58 MB를 쓰므로 들어가지만
   보존 기간과 정리 정책(자동 삭제 없음, V0와 같음)을 정해야 한다.

---

## R. 남은 결정

1. **Q1~Q3 전향 연구의 벽 의미**: OPENED 행 값(R0 재현, `wall_r0`) vs 현재 값(화면, `wall_v2`).
   새 연구 계약에서 고정할 것. 둘 다 저장 중이며 하나를 끄면 각각 약 15 MB / 6 MB를 아낀다.
2. **MC V1 ABSORPTION_CANDIDATE 빈도.** 고정 계약 7절은 "거의 항상 비어 있을 것"을 예상했지만
   실측은 표본의 **57%(rec1 856 / 1,499), 46%(rec0 584 / 1,274)**에서 발화했다. 1시간 실측은
   1,890 / 3,898(48.5%)이었고, 발화 시 60초 공격 USDT 중앙값 1.57M 대 가장 가까운 벽 notional 중앙값 0.61M. 크기 조건(60초 공격 USDT ≥ 가장 가까운 벽의 notional)이 표시 필터 500k 위의 벽과
   BTC 선물의 60초 거래대금에서는 쉽게 넘는다. 규칙을 바꾸지 않았다(새 정의 금지). 화면에 "후보"를
   매초 띄울지는 계약 개정 사안이다.
3. **MC V1 소멸 추적의 대상 집합.** `VanishTracker`는 `side.walls`, 즉 **표시 필터 500k 뒤 상위 6개**를
   본다. 더 가까운 벽이 새로 나타나 6번째가 밀려나거나 notional이 500k 아래로 내려가도 "사라짐"으로
   집계되고, 대개 CANCEL_LIKE가 된다. 실측 소멸은 1시간 1,229건이며 그중 741건(60%)은 그 순간에도 `lm-wall.v2`가 선택한 벽(`wall_v2`에 열린 bin)이었다. 역시 바꾸지 않았고 계약 사안이다.
4. **워밍업 NONE.** 재시작 / HARD resync 후 10초 동안 MC LIQUIDITY는 LIVE + `NONE`을 낸다.
   이 수집기는 `collector.state = SYNCING`으로 표시하지만, 패널이 그 단어를 보여줄지는 화면 결정이다.
5. **상태파일 쓰기 대역폭** 하루 약 14~17 GB(점유는 파일 1개). tmpfs(`/run`) 배치 여부.
6. **보존 정책**: 하루 58 MB, 자동 삭제 없음. 며칠분을 운영 디스크에 둘지.
7. **포트**: API 기본 8013(Liquidity Map 8011, MC V1 8012와 겹치지 않게). 운영 배치 시 nginx 경로.
8. **CPU**: context 단계가 매초 상태파일(약 160~190 KB)을 다시 읽고 파싱한다. 뷰어 경로를 그대로 쓰기
   위한 비용이며 측정값은 M절. 줄이려면 메모리 경로로 바꿔야 하고, 그러면 "같은 코드" 패리티를 다시
   증명해야 한다.

# US-B CRYPTO - 페이퍼 테이프 세그먼트 압축 V1

상태: **코드 준비 완료 / 운영 미적용**
작성: 2026-10-10
대상 코드: `backend/app/crypto/paper/segments.py`, `backend/app/crypto/terminal/api.py`
테스트: `backend/tests/crypto/test_paper_segment_compression.py`

---

## 1. 왜 하는가

`crypto_paper/run`은 이 서버 디스크의 단일 최대 소비자다. 2026-10-10 실측:

| 심볼 | 증가율 |
|---|---|
| BTCUSDT | 31.7 MB/day |
| SOLUSDT | 24.2 MB/day |
| ETHUSDT | 23.5 MB/day |
| **합계** | **79.4 MB/day** |

세그먼트는 20,000 레코드 단위로 봉인되는데, 실효 수신율이 1Hz가 아니라 **0.877 rec/s**(세그먼트
manifest의 `first_ts_ms`/`last_ts_ms`로 6.33시간)라서 심볼당 하루 3.79개가 봉인된다. **1Hz로
역산하면 109 MB/day로 과대추정**되고, 세그먼트 1개의 크기만 보고 어림하면 과소추정된다. 정확한
방법은 직전 7일간 봉인된 세그먼트 바이트를 합산하는 것이다.

gzip -6 실측 비율(운영 샘플, 각 심볼 최신 3세그먼트, 디스크 미기록):

| 심볼 | 원본 | 압축 | 비율 |
|---|---|---|---|
| BTC | 25,946,630 | 2,354,984 | 9.08% |
| ETH | 24,675,313 | 2,481,367 | 10.06% |
| SOL | 25,415,516 | 2,903,612 | 11.42% |
| **전체** | **76,037,459** | **7,739,963** | **10.18%** |

→ 79.4 MB/day가 **8.08 MB/day**가 된다. 런웨이는 자유공간 1,520MB 기준 **19.2일 → 188일**.

**삭제가 아니라 압축인 이유**는 두 가지다.

1. 정상 재기동은 최신 checkpoint를 읽고 `replay_from(after_segment=N)`만 하므로 과거 세그먼트를
   읽지 않는다. 그러나 checkpoint가 거부되면(버전 불일치, run_id 불일치, ledger prefix sha
   불일치, ledger_events 수 불일치) **전 세그먼트를 full replay**한다. 세그먼트를 지우면 이
   폴백이 사라지고, 거부 조건이 발생한 날 서비스가 기동하지 못한다.
2. 아카이브 전체가 16일치뿐이라 **21일·30일 보존 정책은 회수량이 0 MB**다. 7일 보존도 316MB를
   회수하는 대가로 폴백을 잃는다. 압축은 785MB를 회수하면서 폴백을 그대로 둔다.

---

## 2. 아키텍처

압축 경로는 원래부터 `segments.py`에 있었다. 이번 작업은 **배선과 안전장치**만 추가했다.

### 2.1 rotate 순서 (기존, 무변경)

```
fsync active tape
  -> rename active to segments/NNNNNN.input.jsonl   (원자적, 평문 확정)
  -> plain_sha = sha256(평문)
  -> [compress=True] segments/NNNNNN.input.jsonl.gz 작성
  -> [compress=True] gz를 다시 읽어 sha256이 plain_sha와 일치하는지 검증
  -> [compress=True] 불일치면 gz 삭제 + CheckpointRefused, 평문은 그대로 남는다
  -> [compress=True] 일치하면 평문 삭제
  -> manifest 작성 (tmp + fsync + rename)
  -> checkpoint 작성 (tmp + fsync + rename)
  -> 새 active tape 열기
```

핵심 성질: **평문은 압축본이 왕복 검증을 통과한 뒤에만 삭제된다.** 되돌릴 수 없는 단계가 마지막이다.

manifest는 두 해시를 모두 보관한다. `sha256`은 저장된 바이트(=gz)의 약속이고,
`uncompressed_sha256`은 그것이 무엇으로 풀리는지의 약속이다. `verify_segments`는 전자를,
복구 경로는 후자를 신뢰한다.

### 2.2 추가한 것 1 - `load_manifests` 중복 인덱스 가드

**이번 변경에서 가장 중요한 부분이다.** 기존 구현은 `*.input.jsonl*` glob을 순회하며 파일마다
인덱스를 뽑았다. 압축 회전이 평문과 `.gz`를 동시에 남기는 창(위 순서의 3~5단계)에서 crash가 나면
한 인덱스에 두 파일이 존재하고, 루프는:

1. `000001.input.jsonl`을 먼저 만나 manifest가 없으므로 평문을 기술하고 manifest를 **쓴다**
2. 같은 루프에서 `000001.input.jsonl.gz`를 만나(방금 자기가 쓴 manifest가 이제 존재하므로)
   **같은 manifest를 두 번째로 append한다**

결과는 full replay가 그 세그먼트를 **두 번 적용**하는 것이고, 재구성된 ledger는 그 구간의 모든
체결을 두 번 들고 있게 된다. 아카이브는 멀쩡한데 읽기가 틀린 종류의 결함이다.

수정: 파일이 아니라 **인덱스를 키로** 순회한다. 동률 처리는 쓰기 순서가 보장하는 대로:

- manifest가 있으면 그것이 어떤 파일을 읽을지에 대한 authority다. manifest는 평문 삭제 **이후**에
  쓰이므로, manifest가 존재하는 인덱스에 평문 쌍둥이가 살아 있을 수 없다.
- manifest가 없으면 **평문이 이긴다.** 평문은 active tape의 원자적 rename으로 도착해 바이트가
  증명된 상태이고, 옆의 `.gz`는 왕복 검증까지 가지 못한 잘린 쓰기일 수 있다.

고아 `.gz`는 삭제하지 않는다. 공간만 쓰고 그 외에는 무해하며, **테이프를 지우는 복구 경로를
갖는 것이 쓰레기를 남기는 것보다 나쁘다.**

### 2.3 추가한 것 2 - `scan_segment`의 gz 투명 읽기

평문 삭제와 manifest 작성 사이에서 crash가 나면 아무도 기술하지 않은 `.gz`가 남는다. 기존 복구
경로는 압축 파일의 레코드 수를 **0으로 선언**했다. 이 숫자는 replay에는 쓰이지 않지만 세션이
테이프 시퀀스를 이어가는 기준이라서, 한 번 0이 박히면 그 run이 끝날 때까지 전체 레코드 수가
틀린다. 이제 gzip을 투명하게 열어 실제 레코드 수와 타임스탬프 범위를 읽는다.

### 2.4 추가한 것 3 - 유효값 노출

`SegmentedTape.storage()`에 `compress_new_segments`를 추가했다. 이미 `compressed_segments`(지금까지
압축된 세그먼트 **개수**)가 있었는데, 이것은 **다음 cut이 압축될 것인가**라는 다른 질문이다. 전환
중인 아카이브는 두 값이 다르고, 플래그를 켠 운영자는 돌아가는 프로세스가 동의했는지를 다음 cut까지
움직이지 않는 개수로부터 추론하면 안 된다. `GET /api/crypto/state`의 `storage` 블록에 그대로 나온다.

---

## 3. 설정 (env)

```
CRYPTO_PAPER_COMPRESS_SEGMENTS=true
```

`backend/app/crypto/terminal/api.py`의 `compress_segments_setting()`이 `(enabled, raw, recognised)`를
돌려준다.

| 입력 | 결과 | recognised |
|---|---|---|
| `on` `1` `true` `yes` (대소문자·공백 무관) | **True** | True |
| `off` `0` `false` `no` `""` (미설정 포함) | False | True |
| 그 외 (`yess`, `enable`, `2`, `y`, …) | **False** | **False** |

설계 결정 두 가지:

- **기본값은 기존과 같은 off.** 켜는 것은 명시적 행위다.
- **인식 불가 값은 예외가 아니라 off.** 이 호출 뒤에는 실포지션을 들고 있는 트레이딩 터미널이
  있다. 디스크 보존 플래그의 오타 때문에 기동을 거부하면 사소한 실수가 장애가 된다. 다만 오타가
  조용히 **on**으로 해석돼 복구가 읽는 대상을 바꾸는 일은 절대 없어야 하므로 허용 목록은 정확하고,
  목록 밖의 값은 `UNRECOGNISED_FAIL_CLOSED`로 기록된다.

기동 시 stdout에 한 줄(유닛이 `PYTHONUNBUFFERED=1`이라 journald로 들어간다):

```json
{"event":"PAPER_SEGMENT_COMPRESSION","env":"CRYPTO_PAPER_COMPRESS_SEGMENTS","raw":"true","effective":true,"value":"RECOGNISED"}
```

`storage.compress_new_segments`는 결정을 보여주지만 그 결정을 만든 **문자열**은 보여주지 않는다.
인식 불가 값이야말로 둘의 차이가 볼 가치가 있는 경우라서 기동 줄에 raw를 남긴다.

값은 `LiveRuntime.build()`에서 **한 번** 해석되어 `self.compress_segments`에 보관되고, 심볼별
세션도 그 값을 쓴다. 매 생성마다 env를 다시 읽으면, 장시간 돌아가는 서버 아래에서 환경이 바뀌었을 때
BTC는 압축하고 ETH는 안 하는 상태가 될 수 있다.

---

## 4. 테스트

`backend/tests/crypto/test_paper_segment_compression.py` (38 케이스)

| 항목 | 내용 |
|---|---|
| A | 플래그 off면 아카이브가 전부 평문이고 **같은 입력에 같은 바이트**(run 간 sha 동일), `.gz` 0개 |
| B | 압축 세그먼트의 **압축 해제 바이트 == 평문 run의 세그먼트 바이트**(바이트 단위), `uncompressed_sha256`이 그 바이트의 해시이고 `sha256`이 저장 바이트의 해시, records·ts 범위 동일 |
| C | 압축 아카이브 재기동이 `source=="CHECKPOINT"`, `checkpoint_segment==최신`, 꼬리만 replay |
| D | full replay가 gz를 읽고 평문 run과 **ledger 바이트·fills·포지션·realized_pnl 동일**. checkpoint 전멸 시에도 동일 |
| E | 혼합 아카이브(기존 평문 + 신규 gz): 인덱스 오름차순·중복 0·공백 0, 기존 봉인분 미변환, 두 인코딩 공존, `verify_segments` 클린, **레코드 1회씩만 replay**(fills 미중복) |
| F | `next_index`가 manifest 기준 단조, 최신이 gz여도 되감김 없음, 새 cut이 기존 파일을 덮지 않음 |
| G | active `input.jsonl`은 gzip 헤더 없음, `input.jsonl.gz` 부재, 라인 JSON 유지 |
| H | crash 3창: ① 평문+gz+manifest 없음 → **인덱스 1회만 기술·평문 선택·replay 1회**(§2.2 회귀 테스트) ② gz만+manifest 없음 → **실제 레코드 수 복원** ③ 잘린 gz → 평문으로 정상 복구 |
| I | 왕복 검증 실패 시 `COMPRESS_NOT_REPRODUCIBLE` 발생, **평문 보존**, 잘린 gz 미잔존, 레코드 손실 0 |
| J | 압축 회전의 peak 디스크가 **정확히 평문 + gz**(제3 사본 없음), `< 평문 × 2` |
| flag | 기본 off, true 목록 6종, false 목록 7종, 인식불가 7종 fail closed, 파서 결정성 |

J의 함정: 측정 계측을 cut **전에** 걸어야 한다. `start()`가 자체 커맨드 레코드를 쓰므로
`segment_records`만큼 구동한 테이프는 이미 회전을 마쳤고, 그 뒤에 계측을 설치하면 아무것도 측정하지
못한다(첫 작성 때 실제로 이 실수로 실패했다).

**운영 규모 환산**: BTC 세그먼트 평문 약 8.65MB, gz 약 0.785MB → cut 1회당 순간 추가 소요 약 **9.4MB**.
세 심볼이 동시에 cut되는 최악의 경우에도 약 28MB로, 1.5GB 자유공간 대비 무의미하다.

전체 `backend/tests/crypto/` 회귀: 기존 2,574개 통과 유지.

---

## 5. 운영 적용 절차 (아직 실행하지 않았다)

crypto 서비스 **재시작이 필요**하다. 재시작은 checkpoint 복구를 타고, 현재 checkpoint는 최신
세그먼트(BTC 000063)에 걸려 있어 과거 세그먼트를 읽지 않는다.

```bash
# 1. 코드 배포 (변경 파일만)
#    backend/app/crypto/paper/segments.py
#    backend/app/crypto/terminal/api.py

# 2. systemd override로 플래그 추가 (유닛 파일 직접 수정 대신)
systemctl edit usb-crypto-paper
#   [Service]
#   Environment=CRYPTO_PAPER_COMPRESS_SEGMENTS=true
systemctl daemon-reload

# 3. 재시작 전 기준 기록
curl -s localhost:8100/api/crypto/state | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d["storage"])'
curl -s localhost:8100/api/crypto/binance/positions
curl -s localhost:8100/api/crypto/binance/fills | python3 -c 'import json,sys;print(json.load(sys.stdin)["total"])'
md5sum /root/usb_runtime/crypto_paper/run/paper-server-001/ledger.jsonl

# 4. 재시작
systemctl restart usb-crypto-paper

# 5. 검증
journalctl -u usb-crypto-paper -n 50 | grep PAPER_SEGMENT_COMPRESSION     # effective=true
curl -s localhost:8100/api/crypto/state | python3 -c 'import json,sys;print(json.load(sys.stdin)["storage"])'
#   compress_new_segments: true / compressed_segments: 0 (다음 cut까지 0이 정상)
#   ledger md5·fills·포지션 identity 불변 확인
```

**첫 압축 세그먼트는 다음 cut(심볼별 최대 약 6.3시간 뒤)에 나타난다.** 그때
`compressed_segments`가 0에서 1로 움직이고, `verify_segments`가 클린이어야 한다.

되돌리기: override에서 `Environment=CRYPTO_PAPER_COMPRESS_SEGMENTS=false` 또는 줄 삭제 후 재시작.
이미 압축된 세그먼트는 그대로 읽힌다(혼합 아카이브는 E/F에서 검증됨).

---

## 6. 과거 세그먼트 소급 마이그레이션 - 설계만 (실행 금지)

현재 봉인된 872.5MB를 소급 압축하면 약 785MB를 회수한다. 폴백도 보존된다. 그러나 **이것은 불변
아카이브를 제자리에서 바꾸는 작업**이고 별도 승인이 필요하다.

### 6.1 핵심 위험

평문과 `.gz`가 동시에 존재하는 모든 순간이 §2.2의 중복 인덱스 창이다. **이번 변경의 가드가 그
창을 메웠지만**, 마이그레이션은 그 창을 수백 번 의도적으로 만드는 작업이므로 가드에만 의존하면
안 된다. 가드는 "중복으로 읽지 않는다"를 보장할 뿐, 어느 파일이 선택되는지는 평문 우선 규칙이
정한다. 즉 마이그레이션 중 crash는 **그 인덱스의 압축이 되돌려진 상태**로 끝나며, 이것이 원하는
실패 모드다.

### 6.2 요구 절차 (인덱스 1개씩)

```
전제: 해당 인덱스의 manifest가 존재하고 sha256이 검증된다 (verify_segments 클린)

인덱스 N에 대해:
 1. manifest 읽기. compressed=true면 이미 끝난 것이므로 skip
 2. 평문 sha256 재계산 → manifest.sha256과 일치 확인. 불일치면 그 인덱스 중단
 3. NNNNNN.input.jsonl.gz.tmp 로 gzip 작성 (최종 이름이 아니라 .tmp)
 4. .tmp를 다시 읽어 sha256이 2단계 값과 일치 확인. 불일치면 .tmp 삭제하고 중단
 5. .tmp -> NNNNNN.input.jsonl.gz 원자적 rename
       (이 시점부터 평문 + gz 공존 = 중복 창. 가드가 평문을 선택하므로 읽기는 안전)
 6. 새 manifest 작성: path=gz, sha256=gz의 해시, uncompressed_sha256=평문 해시,
    compressed=true, records/first_ts/last_ts/bytes는 gz 기준으로 갱신
       (write_atomic = tmp + fsync + rename)
       (manifest가 gz를 가리킨 순간부터 가드의 "manifest가 authority" 규칙이 gz를 선택)
 7. 평문 삭제
 8. verify_segments로 그 인덱스 재검증
```

3단계에서 `.tmp` 확장자를 쓰는 이유는 `load_manifests`가 `.tmp`로 끝나는 파일을 이미 건너뛰기
때문이다. 따라서 **4단계까지는 중복 창이 아예 열리지 않는다.**

5~7단계 사이에서 crash가 나면:
- 5 직후: 평문+gz, manifest는 평문을 가리킴 → 가드가 평문 선택, 정상 동작. gz는 고아가 되고 다음
  마이그레이션 시도가 덮어쓴다
- 6 직후: 평문+gz, manifest는 gz를 가리킴 → 가드의 manifest 우선 규칙으로 gz 선택, 정상 동작.
  평문이 고아가 되며 7단계만 다시 하면 된다

### 6.3 추가 요구사항

- **active 세그먼트와 최신 manifest는 건드리지 않는다.** 돌아가는 프로세스가 `next_index()`를
  호출할 때 manifest 집합이 흔들려서는 안 된다
- 마이그레이션 중 **crypto 서비스는 돌아가고 있다.** rotate가 동시에 일어날 수 있으므로 작업
  대상은 "시작 시점의 최신 인덱스보다 작은" 것으로 고정한다
- 한 인덱스를 끝낼 때마다 `verify_segments` 전체를 다시 돌리는 것은 872MB 재해시라 비싸다.
  해당 인덱스만 검증하고, 전체 검증은 마이그레이션 종료 후 1회
- peak 디스크는 §4의 J와 같이 **인덱스 1개분(평문+gz 약 9.4MB)**뿐이다. 한 번에 하나씩 하므로
  전체 아카이브 크기가 필요하지 않다
- 종료 후 `ledger.jsonl` md5와 `fills` 수가 불변임을, 그리고 `recover(use_checkpoint=False)`가
  마이그레이션 전과 같은 ledger 바이트를 내는지를 확인한다

---

## 7. 범위 밖 (별도 항목)

### exit-guard V2

현재 `enabled=false / state=ERROR / last_error="MONITOR: BinanceError"`로 2026-10-06 10:43:50 KST에
동결된 stale tombstone이다. 이번 작업에서 수정하지 않았고 작업 전후 불변을 확인했다.

재인입 전에 필요한 수정:

- **tick 주기 1s → 5~10s**, 또는 `recent_fills(200)`/`funding(100)`을 포지션 open cycle 단위로 캐시.
  실측으로 `get_position_card()` 1회가 used_weight 37(depth 2 + user_trades 5 + income 30)이고,
  1Hz면 fast tier 8을 더해 분당 2,700이 되어 `adapter.py`가 명시한 Binance IP 예산 2,400을 넘는다
- `_loop`의 **단일 예외 영구 비활성화 제거**. 현재는 `call()`에 -1021 clock skew 외 재시도가 없고
  예외 1건에 가드를 끄므로, 네트워크가 한 번 튀면 운영자가 걸어둔 손절이 조용히 사라진다
- 생애 REST 오류 221건 중 **212건이 rate limit**인 것이 위 산술과 일치한다

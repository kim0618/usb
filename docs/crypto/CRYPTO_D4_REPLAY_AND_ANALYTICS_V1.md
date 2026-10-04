# US-B CRYPTO - D4: Historical Replay, Analytics, 장기 운영성

> 운영 worktree에서 보존한 과거 기록입니다(2026-10-04 reconciliation). 아래 실측값과 배포 상태는 당시 기록이며 이번 Stage에서 재검증하지 않았습니다. 현재 release의 동작과 정책은 커밋된 코드 및 최신 기능별 계약을 우선합니다. 이 문서의 명령은 이번 Stage에서 실행하지 않았습니다.

D3.5 배포본 위에서 진행한 D4 작업 기록. 전략 로직(Edge/Score/Probability/AUTO)은 구현하지 않았다.

---

## 1. Replay 아키텍처

이미 존재하던 구조를 이어받았고, 빠진 데이터 계열만 채웠다.

```
D2 파일 (kline_1m / mark_1m / index_1m / open_interest_5m / funding)
    -> HistoricalTapeBuilder        1분봉 -> 엔진 입력 레코드
    -> as_tape_records              커맨드와 시각순 병합 (PIT 순서 고정)
    -> ReplayDriver                 STEP / ACCELERATED / REALTIME
    -> PaperEngine                  라이브와 **동일한** 체결·회계 경로
    -> ledger.jsonl + performance.json + trades.json
```

핵심은 replay가 별도 백테스터가 아니라는 점이다. 히스토리 테이프는 라이브 세션이 쓰는 테이프와
같은 형식이고, 엔진은 둘을 구분하지 못한다. 그래서 replay는 실제 실행 경로를 검증한다.

### 이번에 채운 것

- **index_1m** 을 실제로 공급한다(이전에는 `index_price: None`).
- **open_interest_5m** 을 바에 붙인다. 5분 주기 발행이므로 **해당 바 시각 이하의 마지막 관측치**를
  쓰고 보간하지 않는다. OI는 연구 컨텍스트이며 엔진에 도달하지 않는다
  (`quote_from_payload` 가 모르는 키를 무시하는 성질을 이용).
- CLI가 세 계열을 자동으로 찾고, 없는 계열은 **채우지 않고 비운다**.

### 모델인 부분 (숨기지 않음)

- **히스토리 호가창은 존재하지 않는다.** Bybit는 호가를 실시간으로만 발행한다. 테이프는 1분봉
  가격 주위에 가정된 스프레드로 양방향 호가를 합성하고, 모든 레코드에 `book_synthetic: true`를
  찍는다. 이 테이프의 체결은 측정값이 아니라 모델 출력이다.
- **바 내부 경로도 모델이다.** 한 바는 low / high / close 세 레코드가 되고 순서는 고정이다.
  청산 탐지에는 문제가 없지만(한 포지션에서 청산을 일으키는 극단은 하나뿐) 바 안의 미실현손익
  궤적은 실제가 아니다.

### 결정성 근거 (실측)

`2021-02-01T00:00:00Z ~ 12:00:00Z` 실 D2 데이터 기준:

| 검증 | 결과 |
|---|---|
| 같은 테이프 3회 재생 | 원장 바이트 동일 |
| STEP / ACCELERATED / REALTIME | 원장 바이트 동일 |
| 1건씩 step vs 통째 run | 원장 바이트 동일 |
| 테이프 파일 저장 후 재적재 | 원장 바이트 동일 |
| 레코드 시각 단조성 | 위반 0 |
| 엔진이 본 quote 시각 <= 방금 적용한 레코드 시각 | 위반 0 |
| funding = 해당 바 이하 마지막 정산분 | 전건 일치 |
| OI = 해당 바 이하 마지막 관측치 | 전건 일치 |
| index / mark / last 가 서로 다른 계열 | 확인 |

---

## 2. Analytics

`summarize()` 가 원장 전체를 접어서 계산한다. D4에서 추가한 항목은 payoff ratio,
평균 보유시간, 시간대별 집계다.

전체: trades / wins / losses / win rate / gross / net / fees / funding / avg win / avg loss /
**payoff ratio** / expectancy / profit factor / MDD / longest losing streak /
보유시간 median·**mean**·total / MAE / MFE / liquidations
집계축: `by_side`(LONG·SHORT) / `by_leverage` / `by_origin` / **`by_hour_utc`**

- payoff ratio는 손실 거래가 아직 없으면 **무한대가 아니라 None**이다. 0으로 나눈 결과는
  대답이 아니라 아직 안 나온 질문이다.
- `by_hour_utc`는 진입 시각의 UTC 시간. 무기한 선물은 24시간 도니까 "언제"는 실제 축이다.
  거래가 없는 시간대는 0으로 채우지 않고 **없는 채로 둔다**.

### ACCOUNT_RESET 처리

- **거래 통계는 리셋과 무관하게 전체 이력**을 쓴다. 리셋은 거래를 지우지 않는다.
- **자본곡선 계열만** 리셋을 경계로 구간을 나눈다(`capital_segments`).
  충전액이 수익으로 잡히거나, 리셋 전 낙폭이 회복된 것처럼 보이면 안 되기 때문이다.
- MDD는 구간별로 계산한 뒤 가장 나쁜 값을 취한다. 경계를 가로질러 peak를 이어가지 않는다.
- 응답에 `capital_resets` 와 `segments[]`(구간별 시작자본·종료자산·순손익·수익률·거래수·MDD)가 함께 실린다.
- 낙폭이 없던 구간의 MDD 비율은 `None` 이다. 0% 하락과 같지 않다.

---

## 3. Tape / Checkpoint 아키텍처

```
run/<run_id>/
  input.jsonl                    활성 테이프 (append only)
  ledger.jsonl                   전체 원장 (분할하지 않음)
  segments/
    000001.input.jsonl[.gz]      닫힌 세그먼트, immutable
    000001.manifest.json         records / bytes / sha256 / 시각범위 / 압축여부
  checkpoints/
    000001.checkpoint.json       세그먼트 경계 시점의 엔진 상태 + 원장 prefix 증명
```

**원장은 여전히 authority다.** 체크포인트는 원장을 대체하지 않고 원장의 **prefix를 해시로 증언**한다.
복구 시 디스크의 원장이 여전히 그 바이트로 시작하는지 확인하고, 아니면 체크포인트를 거부하고
전체 재생으로 내려간다. 즉 체크포인트는 재기동을 빠르게 할 수는 있어도
**틀린 상태를 맞은 것처럼 보이게 만들 수는 없다.**

회전 순서는 되돌릴 수 없는 단계를 마지막에 둔다.

```
활성 테이프 fsync
  -> segments/NNNNNN.input.jsonl 로 rename   (디렉터리 내 원자적)
  -> manifest 기록                            (tmp + fsync + rename)
  -> checkpoint 기록                          (tmp + fsync + rename)
  -> 새 활성 테이프
```

어느 지점에서 죽어도 로더가 알아본다. manifest 없는 세그먼트는 다시 해시해서 manifest를 쓰고,
checkpoint가 없으면 이전 것을 쓰고 tail을 더 재생할 뿐이다. 지우거나 덮어쓰는 단계가 없어서
이력이 사라질 자리가 없다.

원장은 분할하지 않는다. 하루 100여 건이라 통째로 메모리에 올려도 되고, 그래야
체크포인트로 복구해도 analytics가 **전체 이력**을 본다.

### 검증

| 항목 | 결과 |
|---|---|
| 체크포인트 복구 == 전체 재생 (원장 바이트) | MATCH |
| 체크포인트 복구가 리셋 전 원장까지 보유 | 확인 (RUN_START부터) |
| 세그먼트 checksum 검증 | 전건 통과 |
| 세그먼트 1바이트 변조 | CHECKSUM_MISMATCH 검출 |
| manifest 삭제 후 재기동 | 세그먼트에서 재생성, sha256 동일 |
| 원장 prefix 변조 | 체크포인트 거부 -> 전체 재생 |
| 다른 런/다른 버전 체크포인트 | 거부 |
| 체크포인트 파일 torn | 이전 체크포인트로 폴백, 결과 동일 |
| gzip 세그먼트 | 왕복 해시 일치 후에만 원본 삭제 |

압축은 **압축본을 다시 풀어 원본 해시와 일치하는 것을 확인한 뒤에야** 평문을 지운다.
manifest는 저장 바이트 해시와 압축 해제 해시를 **둘 다** 들고 있다.

---

## 4. Storage horizon (실측)

실서버 라이브 테이프 72,987 레코드 29.9MB로 측정:

- 레코드당 430 B, 1Hz -> **하루 약 35MB**
- gzip -6 **압축비 9.8배**

| | raw | gzip |
|---|---|---|
| 1일 | 35 MB | 4 MB |
| 30일 | 1,062 MB | 108 MB |
| 1년 | 12,923 MB | 1,314 MB |

서버 여유 1.3GB 기준 운영 horizon:

- 무압축 **37일**
- gzip **361일 (약 1년)**

즉 압축만으로 1년치를 현재 디스크에 담을 수 있다. 그 이상은 세그먼트를 서버 밖으로
아카이브해야 하고, 세그먼트가 immutable + checksum이라 그 이전이 안전하다.
journald가 이미 420MB를 쓰고 있으므로 같이 관리한다.

**현재 배포본은 압축을 켜지 않았다**(`compress_segments=False`). 회전·체크포인트를 먼저
실운영에서 관찰한 뒤 켜는 것이 순서라고 판단했다. 켜는 방법은 런북에 있다.

---

## 5. Restart / Recovery

| 시나리오 | 결과 |
|---|---|
| FLAT restart | PASS |
| LONG 보유 중 restart | PASS (세그먼트 경계 가로질러) |
| SHORT 보유 중 restart | PASS (세그먼트 경계 가로질러) |
| ACCOUNT_RESET 이후 restart | PASS (anchor·reset_count 보존) |
| 세그먼트 경계 restart | PASS |
| 체크포인트 직후 restart | PASS (2회 연속 복구 동일) |
| 활성 테이프 torn write | PASS (잘라내고 결과 동일) |
| 중복 체결 | 3회 반복 복구, FILL 집합 불변 |
| 회계 불변식 A6/P5 | 전 경로 유지 |

실서버 실측: 기존 런(75,720 레코드)이 재기동 후 backlog를 세그먼트 1로 회전하고 체크포인트를
남겼으며, **그 다음 재기동은 CHECKPOINT 경로로 1.25초**에 끝났다. 원장 117건 전부 유지,
SHORT 포지션·잔고 동일, 원장 꼬리 보정 0바이트.

---

## 6. Fee 모델

요율은 공식이고 **티어가 가정**이다. 이 구분을 숨기지 않는 것이 이 모듈의 존재 이유다.

`paper/fees.py` 가 캡처된 Bybit 수수료 페이지 검증 레코드에서 시나리오를 만든다.
각 시나리오는 출처(URL·페이지 갱신일·html sha256·검증레코드 sha256)와 basis를 들고 다닌다.

| 시나리오 | taker | maker | basis |
|---|---|---|---|
| VIP_0 | 0.00055 | 0.0002 | `OFFICIAL_PUBLIC_VIP0_TIER_ASSUMED` |
| VIP_1 ~ SUPREME_VIP | 0.0004 ~ 0.0003 | | `OFFICIAL_PUBLIC_TIER_HYPOTHETICAL` |
| ZERO | 0 | 0 | `NO_COST_BOUND` |

- VIP_0는 현재 배포본이 쓰는 값과 정확히 일치한다.
- 나머지 VIP는 **공표된 실제 요율**이지만 US-B가 도달하지 않은 티어다. 지어낸 값이 아니라
  "우리에게 적용되지 않는 공식 값"이다.
- ZERO는 수수료 추정이 아니라 **결과가 비용을 이겨내야 하는 상한**이다.
- basis 문자열은 config의 닫힌 어휘(`FeeSchedule.BASES`)를 쓴다. 아무도 이름 붙이지 않은
  수수료 가정은 아무도 생각하지 않은 가정이다.

실행:

```bash
python backend/app/dev/crypto_paper_replay.py --list-fee-scenarios
python backend/app/dev/crypto_paper_replay.py ... --fee-scenario ZERO
```

동일 테이프·동일 커맨드 실측 (2021-02-01 12시간):

| 시나리오 | taker | gross | fees | net | PF |
|---|---|---|---|---|---|
| VIP_0 | 0.00055 | 49.077 | 2.934 | 46.361 | 21.07 |
| SUPREME_VIP | 0.0003 | 49.077 | 1.601 | 47.695 | 27.46 |
| ZERO | 0 | 49.077 | 0 | 49.295 | 42.27 |

gross가 동일하고 비용만 달라진다. D5는 결론을 단일 수수료 가정에 묶지 않고 보고할 수 있다.

---

## 7. Open-source 재사용

이번 D4에서 실제로 필요해진 지점이 없었다. 판정:

| 대상 | 판정 | 사유 |
|---|---|---|
| Freqtrade | DO_NOT_USE | 자체 주문/회계 모델을 갖고 있어 우리 결정적 원장 계약과 충돌한다 |
| NautilusTrader | REFERENCE | 세그먼트 카탈로그 개념은 방향이 같으나, 도입하면 엔진을 함께 가져와야 한다 |
| CCXT | DO_NOT_USE | 거래소 접근 추상화. 우리는 Bybit 공개 엔드포인트 2개만 쓰고, 계정 경로가 없는 것이 안전장치다 |

새로 들여온 서드파티 코드 0건. 표준 라이브러리(`gzip`, `hashlib`)만 썼다.

---

## 8. 남은 위험 / UNKNOWN

- **히스토리 호가창 부재**가 가장 큰 모델 가정이다. 스프레드·깊이를 바꾸면 체결가가 바뀐다.
  D5는 스프레드 민감도도 수수료처럼 시나리오로 돌려야 한다.
- **바 내부 경로**(low -> high -> close 고정)는 실제 순서가 아니다. 바 안에서 진입과 청산이
  모두 일어나는 전략은 이 테이프로 정직하게 평가할 수 없다.
- **압축 미적용 상태**로 배포했다. 37일 안에 켜거나 아카이브해야 한다.
- 세그먼트 회전 임계값 20,000 레코드(약 6시간)는 실운영 관찰 전의 초기값이다.
- 실서버 런의 시작자본은 여전히 1,000,000원이다(RUN_START 스냅샷 불변). 10,000,000원은
  RESET 또는 새 런에 적용된다.

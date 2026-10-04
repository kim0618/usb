# Liquidity Map V1 - Manual Trading Preview 보고서

작성 2026-10-04. 대상 데이터는 Market Structure V0 저널
(계약 `docs/crypto/market_structure_v0/DATA_CONTRACT_V0.md`, sha256 `9eed3862…d52f`, 버전 `btc-ms.v0.1`).

**판정: PREVIEW READY.** 격리 Preview까지만 만들었고 **Production 배포 0 · 커밋 0 · push 0**.
LONG/SHORT 자동판정 0 · score 0 · 주문/Auto 변경 0 · Manual order path 변경 0 · **기존 파일 수정 0**.

| 항목 | 값 |
|---|---|
| branch | `main` (HEAD `848a033`, **커밋하지 않음**) |
| 신규 백엔드 | `backend/app/crypto/liquidity_map/` 6파일 1,352줄 |
| 신규 프론트 | `frontend/lib/liquidity-map.ts` · `frontend/components/liquidity-map-preview.tsx` · `frontend/app/liquidity-preview/page.tsx` 907줄 |
| 신규 테스트 | 백엔드 5파일 + fixtures(1,533줄) · 프론트 1파일(606줄) |
| 기존 파일 수정 | **0개** (`git status`의 `M` 22개는 전부 동시 세션 것) |
| 테스트 | 백엔드 liquidity **127 passed**, 프론트 **53 passed**, crypto 백엔드 전체·프론트 전체 789 통과 |
| 라이브 검증 | collector 2세션(합계 약 90분) 위에서 PC 1680px·모바일 390px 실측 |

---

## 0. 이 단계에서 가장 중요한 결정: "지금 쉬고 있는 wall 집합"은 저널에 없다

V0 수집기는 wall 후보를 **전이(OPENED/ENDED/UNKNOWN)로만** 저장한다. 샘플당 1행을 쓰면 wall이 전체
바이트의 88.5%가 되기 때문이고, 데이터셋으로서는 그 선택이 맞다. 그 대신 **"지금 쉬고 있는 후보가
이것들이다"라고 말하는 줄이 저널에 한 줄도 없다.** 그 집합은 collector 메모리 안에만 있고, 이 뷰어는
거기에 손댈 수 없다(계약이 collector의 UI 연동을 금지한다).

그래서 집합을 **복원하되, 추측하지 않고 증명한다.**

1. wall 스트림을 **뒤에서부터** 읽는다. `(side, price)`별로 뒤로 가며 처음 만난 행이 가장 최신 행이므로
   그 키의 상태를 확정한다. 창이 닿은 키에 대해서는 정확하다.
2. 놓칠 수 있는 것은 **딱 하나**다 - 창보다 먼저 열려서 지금까지 쉬고 있는 후보. 창 안에 그 후보를
   언급하는 줄이 없다. 즉 조용히 모자라는 쪽은 **가장 오래 버틴 wall**이고, 그건 운영자가 제일 보고
   싶어하는 바로 그것이다. 여기서 추측하면 최악의 실패가 된다.
3. 그래서 collector 자신이 쓴 권위와 대조한다. `storage_stats.walls.active`는 collector의 후보 딕셔너리
   **실제 크기**이고 envelope `seq`를 달고 있다. 같은 뒤로읽기에서 그 `seq` 시점의 집합을 다시 세워
   크기를 비교한다. **뒤로읽기는 구조적으로 과소계수만 가능하므로 수가 같으면 그건 증명이다.**
   다르면 `missing_count`로 몇 개가 미복원인지 그대로 내보내고, 화면은 "가장 가까운 wall"을
   **말하지 않는다.**
4. 세션의 첫 wall 바이트까지 닿은 뒤로읽기는 구성상 완전하다(`STREAM_START`). 첫 `storage_stats`가
   나오기 전(세션 60초 이내)에는 이쪽이 유일한 증명 경로다.

첫 스캔 뒤에는 **커서로 앞으로만** 따라간다. 그래서 정상 상태 비용은 세션 길이가 아니라 "직전 폴 이후
새로 쓰인 바이트"에 비례한다. 그리고 한 번 증명된 집합은 계속 증명된 상태로 남는다 - 이후 변경이 전부
그 커서로 들어오기 때문이다.

**PARTIAL이 COMPLETE로 승격되는 경로도 있다.** 커서가 "내가 들고 있지 않은 키의 ENDED"를 보면, 그건
첫 스캔이 닿지 못한 후보 하나가 닫힌 것이다(스캔 종료점과 커서 시작점 사이에 틈이 없으므로 다른 해석이
없다). 그런 종료 하나마다 미복원이 하나 줄고, 0이 되면 집합은 증명된다
(`verified_by = MISSING_CANDIDATES_ALL_CLOSED`). 즉 Preview를 그냥 켜두면 저절로 증명된다.

---

## 1. 실측으로 드러난 것 5가지

### 1.1 "가장 가까운 major wall"은 자주 **호가 1틱**이다

실측(2026-10-04): BUY 측 가장 가까운 후보가 `84,778.0` = best bid, 거리 **0.0001%**, 배수 **77.6x**.
이웃 평균이 `0.139 BTC`밖에 안 되기 때문이다. 호가창 최상단은 잔량이 잘게 쪼개져 있어서 계약 규칙
(이웃 3개 이상, 평균의 3배 이상)을 **터치 레벨이 거의 항상 통과한다.** 다른 샘플에서는 배수가
**1,473x**까지 나왔다(이웃 평균 0.0068 BTC).

→ 이것이 V0 보고서가 남긴 **사용자 결정 ②(wall 규칙 상향)**의 실물이다. 화면은 `multiple`과
`local_average`를 나란히 찍어서 왜 그 레벨이 뽑혔는지 보이게 했고, 임계 자체는 바꾸지 않았다.

### 1.2 표시 필터 기본값은 측정해서 골랐다

복원된 쉬는 후보 280개의 notional 분포(2026-10-04): 중위 **144,380 USDT**, p90 **396,425**,
최대 **2,446,805**. 임계별 잔존 개수:

| 최소 금액(USDT) | 200k | 300k | 500k | 750k | 1M |
|---|---|---|---|---|---|
| 남는 후보 | 93 | 43 | **17** (bid 8 / ask 9) | 10 | 3 |

**500k를 기본값으로 뒀다** - 각 측이 사다리 한 화면에 들어가면서 1줄보다는 많은 지점. 화면에 그대로
적어뒀고(`2026-10-04 실측 분포: 200k 이상 93개 · 300k 43개 · 500k 17개 · 1M 3개`) 입력창과 프리셋으로
바꿀 수 있다. **계약 규칙은 건드리지 않는 표시 전용 필터**이고 payload에
`is_display_filter_not_contract: true`로 박아뒀다.

### 1.3 ±1% 지도는 존재하지 않는다. 실제로는 **±0.15% 지도**다

`limit=1000` 스냅샷의 알려진 구간이 mid 대비 약 **-0.151% ~ +0.147%**(실측 `84,672.0 ~ 84,924.9`,
mid `84,800.45`)이고, wall 후보는 그 구간 안에서만 존재한다. 그래서:

- 밴드 coverage는 **±0.1%만 COMPLETE**, ±0.25% / ±0.5% / ±1%는 전부 PARTIAL이다.
- 더 넓은 세 밴드의 **하한값이 서로 완전히 같다**(같은 레벨만 더하므로). 값이 멈춘 것처럼 보이기
  때문에 화면이 그 이유를 한 줄로 설명한다.
- **차트 overlay 축을 ±1%가 아니라 알려진 구간으로 잡았다.** 구간 밖은 "얇다"가 아니라 "관측 없음"
  이고, 데이터보다 넓은 축을 그리면 빈 공간이 빈 호가창으로 읽힌다.
- 후보 자체의 `coverage`는 **항상 PARTIAL**로 기록된다(±1% 이웃 구역을 다 못 보므로). 화면에 찍는다.

### 1.4 알려진 구간은 mid를 따라오지 않는다 → coverage가 서서히 나빠진다

generation이 유지되는 동안 재스냅샷이 없으므로, mid가 움직이면 알려진 구간이 **비대칭으로 치우친다.**
한 세션 안에서 `-0.1515% / +0.1468%` → `-0.125% / +0.173%`로 이동하는 것을 실측했다. mid가 더 흘러가면
**±0.1% 밴드마저 PARTIAL로 떨어지고**, 끝까지 가면 mid가 구간 밖으로 나가 `CROSSED_BOOK`이 된다.
Preview는 `known_low_pct / known_high_pct`를 상시 표시해서 이 여유가 얼마나 남았는지 보이게 한다.
(V0 계약은 연속성이 깨질 때만 재스냅샷하므로, 이건 결함이 아니라 계약의 성질이다.)

### 1.5 쉬고 있는 후보의 `persistence_ms`는 영원히 0이다

전이만 저장하므로 쉬는 후보의 행은 **열릴 때의 OPENED 행**이고, 그 행의 `persistence_ms`는 0,
`samples`는 1로 고정이다. 그래서 지속시간은 뷰어가 계산한다:
`관측 지속 = 최신 샘플 시각 − first_seen_ms`. 계약이 정의한 "샘플 관측 구간"과 같은 정의이며,
`persistence_rule`로 payload에 명시하고 원본 `row_persistence_ms`도 같이 내보낸다.
kind별 writer가 각자 flush하므로 wall 스트림 머리가 derived보다 최대 1 flush 늦을 수 있고,
그 차이는 `wall_stream_lag_ms`로 공개한다(숨기지 않는다).

또한 **관측은 세션 경계를 넘지 않는다.** 재시작 직후에는 모든 후보가 같은 지속시간으로 읽히므로
(실측: 재시작 45초 뒤 후보 대부분이 `45.0s`), 화면이 `세션 나이 N을 넘을 수 없습니다`를 같이 적는다.

---

## 2. 화면 (UI 캡처)

PC 1680px와 모바일 390px에서 실제 브라우저로 렌더해 측정했다. 캡처 원본은 세션 스크래치패드에 있다
(`final-pc.png`, `final-390.png`, `state-stale.png`, `scn-nowall.png`).

### 2.1 패널 구성

| 영역 | 내용 |
|---|---|
| 품질 스트립 | `LIVE/STALE/SYNCING/NO_DATA` · 밴드 coverage · 책 상태 · 거래 상태 · 저널 나이 / 책 수신 나이 / 거래 수신 나이 / 거래소 지연 / generation / 보유 레벨 · 비-LIVE 사유 전부 |
| 현재가 | best bid · best ask · spread(+bp) · mid · **mark(미보유, 사유 명시)** · 알려진 구간(절대값 + mid 대비 %) |
| 차트 overlay | 알려진 구간 축 · 현재가 mid 선 · nearest sell wall 선 · nearest buy wall 선 · ±0.1% COMPLETE 음영 |
| SELL SIDE (ASK) | 가장 가까운 major wall(가격·거리%·bp·BTC·USDT·관측 지속·배수·이웃 평균) · ask depth 4밴드 · 표시 후보 목록 |
| BUY SIDE (BID) | 동일 구조 |
| FLOW | 5s/15s/60s × coverage·BUY·SELL·net·imbalance·건수 + 마지막 체결 수신 나이 |
| wall 후보 집합 | 복원 개수 · collector 원장 개수와 그 나이 · 미복원 개수 · 이번 폴 읽기 바이트 · major 기준 입력/프리셋 |
| 출처와 읽기 비용 | 세션·계약 sha256 일치 여부·collector 버전·저널 seq·wall 지연·루트·최근 telemetry 6건 |

### 2.2 "틀린 값을 안 보여준다"가 화면에서 어떻게 보이나

- COMPLETE 밴드 → 정식 수량을 흰색으로. PARTIAL 밴드 → **주황색 + `하한` 꼬리표**, 정식 수량은 아예
  렌더하지 않음. UNKNOWN → `-`. **0으로 채운 칸은 한 곳도 없다.**
- 집합이 증명되지 않으면 "가장 가까운 wall" 자리에 `-`와 사유가 뜨고, **차트에서 wall 선 두 개가
  사라진다.** 관측된 후보 목록 자체는 그대로 남는다(그건 관측이므로).
- 좌표를 못 믿으면 overlay는 통째로 그리지 않고 사유를 적는다.
- `mark`는 `-` + `계약 미보유`, 그리고 왜 없는지 한 문장(V0는 공개 엔드포인트 3개만 읽고 그 중
  mark price를 주는 것이 없으며, 계약이 last trade/mark/index 사용을 금지한다).

### 2.3 모바일 390px

실측 `document.scrollWidth = 390`, `window.innerWidth = 390` → **가로 overflow 0**.
scroll container 밖으로 삐져나온 요소 **0개**. PC 1680px도 0.

처음 측정에서 **30px가 새어나왔고**, 원인은 `grid gap-4`였다. 템플릿 없는 grid는 암시적 컬럼을 가장
넓은 자식의 min-content로 잡기 때문에 depth 표가 컬럼을 밀어낸다. `grid-cols-1`
(= `minmax(0, 1fr)`)로 고정해서 해결했다. 표는 전부 `overflow-x-auto` 안에 있어 가로 스크롤되고
페이지를 밀지 않는다. 숫자 필드는 기본 `truncate`지만 **알려진 구간처럼 값 전체가 의미인 필드는
`break-words`로 줄바꿈**한다(`84,924…`로 잘린 범위는 아무것도 읽어낼 수 없으므로).

---

## 3. API 구조

**Preview 전용 ASGI 앱이 자기 포트에서 따로 돈다.** 터미널 앱(수동 주문 경로를 가진 그 앱)에 라우트를
추가하지 않았다 - 읽기 전용이라 해도 같은 프로세스·같은 lifespan·같은 배포에 들어가기 때문이다.
별도 앱이면 주문 경로에 실수로도 닿을 수 없고, 터미널을 건드리지 않고 켜고 끌 수 있으며,
**production이 이걸 import하는 곳이 한 군데도 없으니 실수로 배포될 수가 없다.**

```
GET /api/liquidity-map/health
GET /api/liquidity-map/snapshot?min_notional_usdt=&min_multiple=&wall_limit=
GET /api/liquidity-map/sessions
```

- **GET 전용.** 라우트 집합이 `{GET, HEAD}`를 넘지 않는지 테스트가 강제한다. POST는 405.
- 읽는 환경변수는 **`MS_V0_ROOT` 하나뿐**이고, AST 스캔으로 강제한다(상수 경유도 해석해서 검사).
- `app.crypto.paper` / `live` / `terminal` / `research` / `strategy` import 0. 내부 의존은
  `app.crypto.market_structure_v0`(계약 상수) **한 방향뿐**이고, collector 쪽이 `liquidity_map`을
  언급하지 않는 것도 테스트가 검사한다.
- 소스에 venue URL·`httpx`·`websockets`가 **하나도 없다.** collector만 Binance와 통신한다.
- **파일을 쓰지 않는다.** `open()`의 mode가 `r`/`b`만인지 AST로 검사하고, `os.replace`·`mkdir`·
  `write_text` 같은 변경 호출이 없는지도 검사한다.
- 루트 미설정/디렉터리 없음/세션 없음/샘플 없음은 **200 + `NO_DATA` + 사유**다. 화면에 띄워야 할
  운영 상태이지 스택트레이스가 아니다. 필터 입력이 깨진 경우만 400.

### snapshot 응답 블록

`preview`(버전·모드·scope) / `source`(세션·계약·seq·샘플 시각·저널 나이·세션 나이·읽기 비용) /
`quality`(상태·사유·`sample_is_current`·각종 나이·임계) / `price` / `sides.ASK` · `sides.BID`
(`nearest_wall`, `depth[4밴드]`, `walls[]`, 후보 수) / `flow.windows{5s,15s,60s}` /
`walls`(복원·권위·미복원·스캔비용·필터·규칙) / `overlay`(그려도 되는지와 선 좌표) / `telemetry[]`.

모든 수량은 collector가 쓴 **decimal 문자열 그대로**이고, 뷰어가 계산한 값(거리·%·bp)만 추가로 넣는다.
float을 거치지 않는다.

### 설계상 고친 결함 2개 (라이브 중에 발견)

1. **`collector 원장` 수치가 48.2초에 고정돼 있었다.** 권위 행을 첫 스캔에서 한 번만 읽고 폴마다
   갱신하지 않아서, 지나간 수치와 나이가 현재처럼 보였다. → 폴마다 다시 읽는다(약 5 KB).
2. **collector가 26초 전에 죽었는데 화면이 `거래 LIVE`였다.** 거래 스트림의 나이는 **샘플이 쓰일 때**
   측정된 값인데 그걸 현재 주장으로 들고 있었다. → `sample_is_current`를 도입해서, 저널이 stale이거나
   세션이 끝났으면 거래 상태를 STALE로 내리고 책/거래 칩에 `· 마지막 샘플`을 붙인다.

---

## 4. 실시간 갱신 주기와 비용

| 항목 | 값 |
|---|---|
| collector 샘플 | **1초** (계약 `SAMPLE_INTERVAL_S`) |
| 화면 폴링 | **1초** (더 빨리 받아도 같은 샘플이 온다) |
| 최악 지연 | 약 2초 (샘플 1초 + writer flush 1초). `저널 나이`로 상시 표시 |
| 저널 stale 판정 | **3초** 초과 (샘플 1초 × 3). 버퍼링으로 설명되지 않는 첫 나이 |
| 책 stale / 거래 stale | 2초 / 5초 (계약값을 그대로 읽어 씀, 재선언 금지) |
| 정상 상태 1폴 읽기 | **0 ~ 140 KB · 0 ~ 51행 · 1 ~ 4 ms** (변화가 없으면 0 B) |
| 첫 폴(초기 스캔) | 세션 길이에 비례. 실측 - 세션 12분 **7.1 MB**, 세션 75분 **33.8 MB**(52 ms) |
| 스캔 예산 상한 | 256 MiB. 넘으면 **증명 포기 → PARTIAL** (예산이 후하다고 증명되는 게 아니다) |

`storage_stats`는 60초 간격이므로 `collector 원장` 수치는 최대 60초 낡을 수 있고, 그 나이를 화면에
같이 찍는다.

---

## 5. 테스트

| 파일 | 개수 | 무엇을 막는가 |
|---|---|---|
| `test_liquidity_map_journal.py` | 16 | 잘린 꼬리 줄을 데이터로 읽기 · 로테이션에서 커서 분실 · 세션 오선택(파일 mtime은 거짓말한다) · 마지막 줄 찾겠다고 파일 전체 읽기 |
| `test_liquidity_map_wallstate.py` | 21 | 미검증 집합을 완전하다고 말하기 · 재개장/양측 동일가 키 혼동 · 권위 수치 고정 · 따라가기 비용 폭발 |
| `test_liquidity_map_view.py` | 43 | 하한을 수량으로 보여주기 · 미관측에 0 채우기 · 증명 안 된 집합에서 nearest 말하기 · 못 믿을 좌표에 overlay 그리기 · 방향/score 어휘 |
| `test_liquidity_map_api.py` | 26 | 루트 없음/세션 없음/정지/재접속/무거래/미증명 상황 · 필터 검증 · 쓰기 메서드 |
| `test_liquidity_map_isolation.py` | 21 | 주문 경로·크리덴셜·venue URL·파일 쓰기·환경변수 확산·V1 scope 위반 |
| `liquidity-map-preview.test.tsx` (프론트) | 53 | 포맷(- vs 0) · 축 밖 좌표 클램프 금지 · 하한 라벨 · 미증명 시 선 제거 · 390px 레이아웃 · stale 샘플이 LIVE 주장 금지 |

**합계 백엔드 127 · 프론트 53.** `ms_v0` 163개와 crypto 백엔드 전체, 프론트 전체 789개 모두 통과.

사용자가 지정한 6개 상황을 전부 다뤘다:

| 상황 | 어디서 | 결과 |
|---|---|---|
| stale | 라이브(collector 종료) + 단위 | `STALE` + `collector 세션 종료됨`/`저널이 갱신되지 않음`, 저널 나이 26.4초, overlay 중단, 거래 칩이 LIVE에서 내려감 |
| partial coverage | 라이브 + 단위 | 밴드 3개 PARTIAL(하한 라벨), flow 60s warmup PARTIAL(net·imbalance `-`), wall 집합 PARTIAL 시 nearest withheld |
| wall 없음 | 라이브(임계 99,999,999) | `0 / 전체 164`, `필터를 통과한 후보가 없습니다`, 차트 wall 선 2개 제거, mid 선은 유지 |
| reconnect | 단위(실제 collector 구동) | `SYNCING` + `BOOK_UNSYNCED`, 후보 0, nearest 없음, overlay 중단, telemetry에 `disconnect` |
| no-trade | 단위 | COMPLETE·0건·0 BTC는 0으로 유지하되 imbalance는 `-`(분모 0은 비율 0이 아니다) |
| 390px overflow | 라이브 측정 | `scrollWidth 390 / innerWidth 390`, 미클리핑 overflow **0개** |

### 자기모순 스캔 함정 (V0와 같은 계열)

금지어 스캔(`score`, `spoof`, `LONG`…)을 원문에 걸면 **"방향 판정·score 없음"이라고 쓴 문장 자체가
걸린다.** V0는 docstring을 `ast.unparse`로 제거해서 풀었는데, 여기서는 운영자에게 보여줄 문장이
**코드 리터럴**이라 그것만으로 부족했다. 그래서 `view.py`의 모든 운영자 문구를 `*_NOTE` 상수에 모으고
스캔이 그 대입만 기계적으로 비운다. 제외가 구멍이 아니라는 것은 별도 테스트 2개가 지킨다 -
각 `_NOTE`가 실제로 금지 선언 문장인지, 그리고 **그 문구가 전부 payload에 실제로 실리는지**(스캔에서만
빠지고 화면엔 안 나오면 공짜 면제가 된다).

---

## 6. Preview 접속 방법

세 프로세스가 전부 **별도**다. 셋 중 무엇이 죽어도 trading service는 모른다.

```bash
# 1) 수집기 (이미 돌고 있으면 생략)
cd /home/tjd618/usb/backend
MS_V0_ROOT=/home/tjd618/usb/data/runtime/ms_v0_preview \
  setsid nohup /home/tjd618/usb/.venv/bin/python \
  -m app.crypto.market_structure_v0.collector --duration 86400 > /tmp/ms_v0.log 2>&1 &

# 2) Preview API (읽기 전용, 포트 8011)
cd /home/tjd618/usb/backend
MS_V0_ROOT=/home/tjd618/usb/data/runtime/ms_v0_preview \
  setsid nohup /home/tjd618/usb/.venv/bin/python \
  -m app.crypto.liquidity_map 8011 > /tmp/lm_preview.log 2>&1 &

# 3) 대시보드 dev 서버
cd /home/tjd618/usb/frontend
NEXT_PUBLIC_LIQUIDITY_MAP_BASE_URL=http://127.0.0.1:8011 npm run dev
```

접속: **`http://127.0.0.1:3000/liquidity-preview`** (`npm run dev` 기본 포트).
작성 시점에는 포트 3100으로 띄워 두었으므로 **`http://127.0.0.1:3100/liquidity-preview`**로 바로 볼 수 있다
(수집기는 `--duration 3600`이라 2026-10-04 약 14:35 KST에 끝나고, 그 뒤 화면은 STALE로 넘어간다).
`NEXT_PUBLIC_LIQUIDITY_MAP_BASE_URL`을 생략하면 `http://127.0.0.1:8011`을 기본으로 쓴다.

- 이 경로는 **네비게이션에 없고** 다른 페이지가 import하지 않는다. 주소를 직접 쳐야 들어간다.
- 인터프리터는 **서비스 venv**를 써야 한다(시스템 `python3`에는 `fastapi`가 없다).
- `setsid`는 필수다. 세션이 끝나면 일반 nohup 백그라운드는 죽는다.
- Preview API가 꺼져 있으면 화면은 빈 화면이 아니라 **에러 배너 + 마지막 스냅샷의 나이**를 보여준다.
- **주의:** `npx next dev`를 직접 쓰면 `frontend/next-env.d.ts`의 dist 경로가 `.next`로 바뀐다.
  `npm run dev`(= `NEXT_DIST_DIR=.next-dev`)를 쓰고, 바뀌었으면 `.next-build`로 되돌릴 것.

---

## 7. 알려진 제약 (고치지 않고 적어둠)

1. **페이지가 대시보드 app shell 안에서 렌더된다.** Next의 root layout은 모든 라우트에 적용되므로
   사이드바와 헤더가 같이 뜨고, `isEquityChrome`이 `/crypto-paper`만 제외하기 때문에 이 화면에서도
   미국장 시계·`시장 UNKNOWN`이 보인다. 한 줄로 고칠 수 있지만 그 파일(`components/app-shell.tsx`)이
   **동시 세션에서 수정 중**이라 손대지 않았다. 기능에는 영향 없음.
2. app shell이 자기 몫으로 `/api/v1/dashboard`를 20초마다 폴링한다. Preview가 추가한 호출이 아니라
   대시보드 앱의 기존 동작이고, 주문 경로와 무관하다.
3. 첫 폴의 초기 스캔이 세션 길이에 비례한다(75분 세션 33.8 MB). 24시간 세션이면 수백 MB를 한 번 읽게
   되고, 256 MiB 예산을 넘으면 증명을 포기하고 PARTIAL로 뜬다. 그 경우에도 §0의 "미복원 소진" 경로로
   시간이 지나면 COMPLETE로 승격될 수 있다.
4. wall 규칙 상향(§1.1)은 **계약 변경 사안**이라 건드리지 않았다. 표시 필터로만 좁혔다.

## 8. 다음 단계 (사용자 결정 대기)

- **A. wall 규칙을 올릴 것인가.** §1.1이 그 결정에 필요한 실물이다. 올린다면 계약 버전이 올라간다.
- **B. 표시 필터 기본값 500k를 유지할 것인가.** §1.2 분포로 조정 가능.
- **C. 재스냅샷 주기.** §1.4의 coverage 저하를 막으려면 주기적 재스냅샷이 필요하고, 이것도 계약 사안이다.
- **D. 이 Preview를 운영 UI에 넣을 것인가.** 현재는 격리 Preview까지만이고 **배포하지 않았다.**

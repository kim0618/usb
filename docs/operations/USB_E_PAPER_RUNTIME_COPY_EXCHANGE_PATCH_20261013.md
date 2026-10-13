# usb-e-paper 런타임 사본 exchange 최소 패치 (2026-10-13)

## 왜 별도 패치인가

A-MOVER-LIVE 후보를 만드는 프로세스는 `usb-e-paper.service`(타이머 16:45 KST)이며, 코드는
`/root/usb` 가 아니라 **`/root/usb_runtime/strategy_e_paper/src/backend`** 의 런타임 사본에서 읽는다.
이 사본은 `/root/usb` 와 다른 구버전이다. 정본 `3e2a8ad` 의 파일을 통째로 덮으면
`baseline_readiness`(features.py, mover_scanner_source.py, ScannerUniverseInput) 등 사본에 없는 기능에
의존하게 되어 E paper 워커가 깨진다. 그래서 exchange provenance에 필요한 hunk만 사본 파일에 적용했다.

- 정본 변경: 커밋 `3e2a8ad` (`strategy_a_mover_live/{scanner,gpt_handoff,integration}.py`)
- 증상: 후보의 `score_components_json.exchange` 부재 → entry에서 `Exchange MISSING` (10/12, 2,316회)
- 이 문서는 서버 사본의 drift를 추적하기 위한 기록이다. 사본을 다시 정본과 동기화할 때 이 패치를 잃지 말 것.

## 적용 대상과 hash

| 파일 (사본 기준) | 패치 전 md5 | 패치 후 md5 |
|---|---|---|
| strategy_a_mover_live/scanner.py | 4ee54a7f9c5bb2cc0658cade5ef186f3 | e55294e1a4ade4c8c8d353b63369536c |
| strategy_a_mover_live/gpt_handoff.py | cf4b3419a0ed0e506115e1e5fc44e733 | 62054909eb41ddd8f413b7e796475c57 |
| strategy_a_mover_live/integration.py | 7862bde71d7e03393a6c63c4097a5738 | c45b8506ae59d31777a6376375a05d6d |

서버 백업: `/root/usb_runtime/ui_deploy_20261013/backup_runtime_copy/` (패치 전 3파일).
롤백: 위 3파일을 사본 경로로 복원하면 된다(서비스는 oneshot이라 재시작 불필요).

## 적용한 hunk (런타임 사본 → 패치본)

1. `scanner.py`: `app.market` 선 import(순환 import 순서) + `EXCHANGES` import,
   `stored_exchange(exchanges, symbol)` 추가(ND/NY/NA → NASDAQ/NYSE/AMEX, 그 외 None, fallback 없음),
   `candidate_rows(live, exchanges=None)` 가 지원 venue일 때만 `components["exchange"]` 기록.
2. `gpt_handoff.py`: `persist(..., exchanges=None)` 가 `candidate_rows` 로 전달, 목록에 없는 종목은 warning 로그.
3. `integration.py`: `_handoff` 가 `live.selection.handoff` 의 종목별 `_exchange_of(symbol)` 을 `persist` 로 전달.

사본에 없는 기능(`baseline_readiness`, `ScannerUniverseInput`, `NO_CANDIDATES` 분기 차이)은 건드리지 않았다.
score, rank, threshold, universe, GPT handoff 의미, 정렬, 사이징, 주문은 변경 없음.

## 패치 diff

```diff
--- scanner.py	2026-10-13 14:00:31.643341557 +0900
+++ scanner.py	2026-10-13 14:04:30.486720920 +0900
@@ -28,6 +28,8 @@
 from typing import Any
 
 from app.backtest.mover_scanner_v1 import actionability as A
+import app.market  # noqa: F401  (package init order: kiwoom.mapping imports back into app.market)
+from app.integrations.kiwoom.mapping import EXCHANGES
 from app.backtest.mover_scanner_v1 import contract as K
 from app.backtest.mover_scanner_v1.config import MoverScannerConfig
 from app.backtest.mover_scanner_v1.scan import MoverCandidate, SessionScan, scan_session
@@ -178,8 +180,29 @@
     return payload
 
 
-def candidate_rows(live: LiveScan) -> list[ScannerCandidateData]:
-    """The persistable rows, through the research module's own contiguity assertions."""
+def stored_exchange(exchanges: Mapping[str, str] | None, symbol: str) -> str | None:
+    """The canonical market name (NASDAQ / NYSE / AMEX) for a symbol the collector listed, or None.
+
+    ``exchanges`` is the Kiwoom listing the live collector already used to read this symbol's
+    bars (symbol -> ND / NY / NA). A symbol it does not list, or a value that is not one of the
+    three supported venues, yields None: the row is stored without an exchange and the entry
+    runtime refuses it by name. Nothing here defaults a venue.
+    """
+    raw = str((exchanges or {}).get(symbol) or "").strip().upper()
+    if raw in EXCHANGES:
+        return EXCHANGES[raw]
+    return raw if raw in EXCHANGES.values() else None
+
+
+def candidate_rows(live: LiveScan, exchanges: Mapping[str, str] | None = None,
+                   ) -> list[ScannerCandidateData]:
+    """The persistable rows, through the research module's own contiguity assertions.
+
+    The mover scanner's own components carry no venue (it is a research module that never
+    needed one), but entry binds every market-data lookup to the exchange stored on the
+    candidate. The venue is therefore added here, from the collector's listing, and only when
+    that listing has the symbol.
+    """
     rows = A.candidate_rows(live.selection, live.observed_at)
     contract = live.contract
     out: list[ScannerCandidateData] = []
@@ -196,6 +219,9 @@
         components["baseline_version"] = contract.baseline_version
         components["baseline_provider_contract"] = contract.baseline_provider_contract
         components.update(live.baseline_stamp)
+        exchange = stored_exchange(exchanges, row.symbol)
+        if exchange is not None:
+            components["exchange"] = exchange
         out.append(ScannerCandidateData(
             symbol=row.symbol, rank=row.rank, is_top8=row.is_top8, score=row.score,
             score_components=components, observed_at=row.observed_at,
--- gpt_handoff.py	2026-10-13 14:00:32.295336381 +0900
+++ gpt_handoff.py	2026-10-13 14:04:30.486774944 +0900
@@ -24,6 +24,8 @@
 
 from __future__ import annotations
 
+import logging
+from collections.abc import Mapping
 from dataclasses import dataclass
 from datetime import datetime
 from typing import Any
@@ -36,6 +38,8 @@
 from app.strategy_a_mover_live import contract as LC
 from app.strategy_a_mover_live import scanner as SCAN
 
+logger = logging.getLogger(__name__)
+
 
 @dataclass(frozen=True)
 class HandoffResult:
@@ -60,7 +64,8 @@
         trading_date, score_version=LC.RUN_SCORE_VERSION, provider=LC.RUN_PROVIDER)
 
 
-def persist(session: Session, live: SCAN.LiveScan, *, now: datetime) -> HandoffResult:
+def persist(session: Session, live: SCAN.LiveScan, *, now: datetime,
+            exchanges: Mapping[str, str] | None = None) -> HandoffResult:
     """Persist the run and its candidates, then render the deployed prompt if there are any."""
     repository = ScannerSnapshotRepository(session)
     found = existing_run(session, live.session)
@@ -72,7 +77,12 @@
                              prompt=prompt, prompt_chars=len(prompt or ""),
                              gpt_calls_expected=1 if candidates else 0,
                              metadata=live.metadata() | {"reused_run": True})
-    rows = SCAN.candidate_rows(live)
+    rows = SCAN.candidate_rows(live, exchanges)
+    unlisted = [row.symbol for row in rows if "exchange" not in row.score_components]
+    if unlisted:
+        # Persisted as they are: entry refuses a candidate with no exchange by name, which is
+        # the contract. This line is what makes that refusal visible at handoff time.
+        logger.warning("A-MOVER HANDOFF: no listed exchange for %s", ", ".join(unlisted))
     run = repository.create_run(
         trading_date=live.session, started_at=live.observed_at, provider=LC.RUN_PROVIDER,
         score_version=LC.RUN_SCORE_VERSION, status="RUNNING",
--- integration.py	2026-10-13 14:00:32.779332464 +0900
+++ integration.py	2026-10-13 14:04:30.486798822 +0900
@@ -240,8 +240,10 @@
         if self.session_factory is None:
             return {"status": "NOT_PERSISTED_NO_DATABASE", "gpt_calls": 0, "persisted": False,
                     "candidates": live.candidate_count}
+        listed = {item.symbol: self._exchange_of(item.symbol)
+                  for item in live.selection.handoff}
         with self.session_factory() as database:
-            result = GPT.persist(database, live, now=self.now())
+            result = GPT.persist(database, live, now=self.now(), exchanges=listed)
         return {"status": "PERSISTED", "run_id": result.run_id, "reused": result.reused,
                 "candidates": result.candidate_count, "prompt_chars": result.prompt_chars,
                 "gpt_calls": result.gpt_calls_expected, "persisted": True,
```

## 검증

- 런타임 사본 전체(849개 .py)를 로컬에 복제해 패치 후 `tests/test_mover_exchange_provenance.py` (사본에서 실행 가능한
  19건) 통과, 같은 환경의 `tests/strategy_a_mover_live` 는 패치 전 24 failed/212 passed/3 errors 에서
  24 failed(동일 집합)/231 passed/3 errors 로 신규 실패 0.
- 24 failed 와 3 errors 는 사본이 구버전이라 정본 테스트가 요구하는 기능이 없어서 생기는 선행 건이다.
- `repair_mover_candidate_exchange.py` 는 사본에 설치하지 않았다. 이미 생성된 잘못된 run의 응급 복구 및 진단용
  fallback 도구이며 `/root/usb` 쪽에만 있다(정상 운영 flow에서는 쓰지 않는다).

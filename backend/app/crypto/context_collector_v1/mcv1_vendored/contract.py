"""Every constant the frozen display contract fixes, in one place, plus the document's own hash.

Nothing here is a tuning knob. Each value is a clause of
``docs/crypto/market_context_v1/CONTRACT_MC_V1.md``, and ``contract_identity()`` recomputes the
document's hash from disk and reports whether the recorded sidecar still agrees - so a panel can
never quietly claim a contract it has drifted away from.

Freshness bounds are deliberately *not* restated here when the source contract already owns them.
``DEPTH_STALE_MS`` and ``TRADE_STALE_MS`` are imported from the V0 collector contract and
``JOURNAL_STALE_MS`` from the Liquidity Map viewer, because a second copy of a threshold is a
second thing to forget to change.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ...liquidity_map.view import JOURNAL_STALE_MS
from ...market_structure_v0.contract import (COMPLETE, DEPTH_STALE_MS, PARTIAL, TRADE_STALE_MS,
                                            UNKNOWN)

#: Identity of the display contract. Independent of every contract this panel reads.
CONTRACT_VERSION = "mc-display.v1"
CONTRACT_RELATIVE_PATH = "docs/crypto/market_context_v1/CONTRACT_MC_V1.md"

# --------------------------------------------------------------------------- layers

#: Section 2. Rendering order, which is the priority order and does not change with the data.
LAYERS: tuple[str, ...] = ("PRICE", "LIQUIDITY", "FLOW", "DERIVATIVES", "AUXILIARY")

# --------------------------------------------------------------------------- quality vocabulary

#: Section 4. The closed set a layer's state is drawn from.
LIVE = "LIVE"
STALE = "STALE"
LAYER_STATES: tuple[str, ...] = (LIVE, STALE, PARTIAL, UNKNOWN)

#: Section 3 and 4. Not a layer state: it means the layer does not exist for this instrument,
#: which is a different statement from "its data is missing right now".
UNAVAILABLE = "UNAVAILABLE"

#: Section 4. The string every missing value renders as. A hyphen-minus, matching the rest of the
#: crypto screens; never a zero, because zero is a real reading of a depth band and a flow window.
MISSING = "-"

# --------------------------------------------------------------------------- symbols

BTC = "BTCUSDT"
#: Section 3. The instruments the manual terminal offers, in its own order.
SUPPORTED_SYMBOLS: tuple[str, ...] = (BTC, "ETHUSDT", "SOLUSDT")

#: Section 3. Why a layer does not exist for a non-BTC instrument. One reason per layer, because
#: the two reasons are different facts and collapsing them would hide one of them.
REASON_NOT_CALIBRATED = "NOT_CALIBRATED_FOR_SYMBOL"
REASON_COLLECTOR_BTC_ONLY = "COLLECTOR_IS_BTC_ONLY"
REASON_RESEARCH_BTC_ONLY = "RESEARCH_IS_BTC_ONLY"

# --------------------------------------------------------------------------- price structure

#: Section 5. The thetas frozen by R0's held-out calibration. Asserted against the research
#: module's own constants at import time by `tests`, so a drift there fails here.
THETA_1H = 3.0
THETA_5M = 6.0

#: Section 5. Default anchor distance. 30 days floored to a UTC day boundary gives the newest
#: minute at least 29 days of book history, against the 20 days R0/R1 warmed up with.
ANCHOR_DAYS = 30
#: Section 5. How much of the anchored series is published. Warmup is everything before it.
PUBLISH_WINDOW_MS = 86_400_000
#: Section 5. A 1m bar is knowable only at its close, so the panel trails real time by up to one
#: minute by construction. Past one minute plus this grace the structure layer reads STALE.
STRUCTURE_GRACE_MS = 90_000
MINUTE_MS = 60_000
DAY_MS = 86_400_000

# --------------------------------------------------------------------------- liquidity

#: Section 6. The four ways there can be no wall. Kept apart because they call for opposite
#: reactions: NONE is a complete reading, UNKNOWN is the absence of one.
WALL_OK = "OK"
WALL_NONE = "NONE"
WALL_STATES: tuple[str, ...] = (WALL_OK, WALL_NONE, PARTIAL, STALE, UNKNOWN)

#: Section 6. Measured over the 3.61 h journal in Market Context R0: a near ask wall was present
#: in 99.0% of samples and a near bid wall in 98.5%. Carried onto the screen beside the wall rows
#: because a condition true 99% of the time cannot separate states, and an operator not told the
#: base rate will read presence as information.
WALL_PRESENCE_BASE_RATE_ASK_PCT = 99.0
WALL_PRESENCE_BASE_RATE_BID_PCT = 98.5
WALL_MEAN_PER_SIDE_WHEN_PRESENT = 8.8

# --------------------------------------------------------------------------- flow

#: Section 7. The V0 contract's windows, in the order they are rendered.
FLOW_WINDOWS: tuple[str, ...] = ("5s", "15s", "60s")

#: Section 7. How a vanished near wall is described. `CONSUMED` alone is not in the vocabulary:
#: `btc-ms.v0.1` cannot prove order identity, so a bin that fell below the notional floor through
#: partial fills is indistinguishable from one that was cancelled.
VANISH_CONSUMED_CANDIDATE = "CONSUMED_CANDIDATE"
VANISH_CANCEL_LIKE = "CANCEL_LIKE"
VANISH_STATES: tuple[str, ...] = (VANISH_CONSUMED_CANDIDATE, VANISH_CANCEL_LIKE, UNKNOWN)

#: Section 7. Measured in Market Context R0: of near walls that disappeared, price had touched the
#: bin in 3.8% (ask) / 6.9% (bid) of cases, and the whole journal held 15 consumption candidates.
#: Carried on screen so that `CANCEL_LIKE` is read as the normal case that it is.
VANISH_TOUCHED_BIN_ASK_PCT = 3.8
VANISH_TOUCHED_BIN_BID_PCT = 6.9

#: Section 7. The absorption row's only value, other than NONE. The word `CANDIDATE` is inside the
#: value so the string cannot be rendered without it.
ABSORPTION_CANDIDATE = "ABSORPTION_CANDIDATE"

# --------------------------------------------------------------------------- derivatives

#: Section 8. The horizons the open-interest change is reported over. 5m is the endpoint's own
#: bucket period; the rest are whole multiples of it, so each is a difference of closed buckets
#: and never an interpolation.
OI_CHANGE_WINDOWS: tuple[str, ...] = ("5m", "15m", "1h", "4h")
OI_HIST_PERIOD = "5m"
#: Section 8. Past this the mark/funding read reads STALE rather than being shown as current.
DERIVATIVES_STALE_MS = 60_000

# --------------------------------------------------------------------------- auxiliary

#: Section 9. The weight label each auxiliary entry carries in the payload, not only in prose.
WEIGHT_AUXILIARY = "auxiliary"
WEIGHT_WEAK_AUXILIARY = "weak_auxiliary"

# --------------------------------------------------------------------------- journal

#: Section 10. The record name every forward context line carries.
JOURNAL_RECORD = "MC_V1_FORWARD_CONTEXT"
JOURNAL_SCHEMA_VERSION = 1

# --------------------------------------------------------------------------- operator notes
#
# Every operator-facing sentence that *declares* a ban lives in a `*_NOTE` constant. The
# vocabulary scan in `test_market_context_v1_isolation.py` blanks these assignments before
# reading the code, exactly the way the Liquidity Map scan does, because these sentences have to
# contain the words they are banning. A companion test asserts the exclusion is not a hole: each
# note must reach the payload and must contain the phrase it is being trusted for.

SCOPE_NOTE = (
    "읽기 전용 컨텍스트 패널입니다. 방향 판정(LONG/SHORT)·score·확률을 만들지 않고, "
    "주문·AUTO·C1 계약을 건드리지 않습니다. 모든 수치는 각자의 동결 계약에서 그대로 읽습니다.")

NO_ROLLUP_NOTE = (
    "레이어 5개를 하나의 상태로 합치지 않습니다. 전체 Market Context를 NEUTRAL로 표시하는 칸은 "
    "없고, 데이터가 모자라면 그 레이어가 UNKNOWN입니다. Market Context R0에서 결합 모델은 "
    "A_NO_CONTEXT_EDGE였습니다(T1 NOT_MEASURABLE, 측정된 최대 효과 +4.83bp < 왕복비용 11.25bp).")

MISSING_VALUE_NOTE = (
    "관측하지 못한 값은 0이 아니라 '-'와 사유로 표시합니다. 0은 이 호가창과 플로우 창의 실제 "
    "관측값이라 '미관측'과 구분되어야 합니다.")

WALL_PRESENCE_NOTE = (
    "벽의 존재 자체는 신호가 아닙니다. 10bp 이내 ask벽은 샘플의 99.0%, bid벽은 98.5%에서 "
    "관측됐고 있을 때 평균 8.8개/측이었습니다. 99% 참인 조건은 상태를 가르지 못하므로 "
    "거리·명목만 측정값으로 표시하고 bullish/bearish 표식은 붙이지 않습니다.")

VANISH_NOTE = (
    "사라진 벽을 CONSUMED로 단정하지 않습니다. btc-ms.v0.1은 주문 identity를 증명하지 못해 "
    "부분체결로 명목 하한 아래로 떨어진 벽과 취소된 벽이 같은 칸에 들어갑니다. 실측은 "
    "가격이 bin에 닿은 경우가 ask 3.8% / bid 6.9%로, 벽은 대개 취소됩니다.")

ABSORPTION_NOTE = (
    "absorption은 candidate만 표시하며 판정이 아닙니다. order_identity_proven=false이고, "
    "집계하지 않으며 방향 색을 붙이지 않습니다. 3.61시간 저널에서 소비 후보는 15건이었습니다.")

DERIVATIVES_NOTE = (
    "파생 정보는 공개 엔드포인트에서 읽은 원값입니다. basis = mark - index, "
    "premium_pct = (mark - index) / index 두 개만 산술 환산이고, 그 밖의 파생 신호는 "
    "만들지 않습니다(OI 대비 가격, funding regime, 혼잡도 등 없음).")

AUXILIARY_NOTE = (
    "BTCUSDT 전용입니다. C1/C1x는 auxiliary, Directional Probability는 weak_auxiliary이며 "
    "현재 시점 확률을 계산하지 않고 동결 아티팩트의 verdict와 범위를 그대로 읽습니다. "
    "ETHUSDT·SOLUSDT에는 이 칸 자체가 없습니다(빈 칸이 아니라 부재).")

DIRECTIONAL_NOTE = (
    "Directional Probability R0은 verdict=WEAK_DIRECTIONAL_MODEL, usable_horizons=[]입니다. "
    "BTC-P1의 방향 AUC는 0.502~0.533이었고 BTC-P2는 방향레이어 STRONG 0/64에 "
    "P3 NOT_AUTHORIZED로 종결됐습니다. 이 칸은 '무엇이 있고 무엇으로 끝났는지'입니다.")

PREV_SESSION_NOTE = (
    "전일 고저는 레벨 북 밖의 별도 context입니다. R2에서 이 소스를 북에 머지하면 27건 중 1건 "
    "증가였고 그 1건도 새 선이 아니라 PROVISIONAL 승격이었습니다. 27건 표본의 해상도 한계라 "
    "production rule로 올리지 않습니다.")

STRUCTURE_SERIES_NOTE = (
    "레벨 북은 R1 큐레이션 그대로이고 시리즈 시작점은 프로세스 시작 시 한 번 고정합니다. "
    "롤링 윈도우면 과거가 뒤에서 떨어져 나가며 레벨 수명이 바뀌어 repaint와 같아집니다. "
    "확정된 마지막 1분봉만 표시하므로 실시간보다 최대 1분 뒤입니다.")

OBSERVED_RANGE_NOTE = (
    "관측 구간(known_low~known_high) 밖은 '호가가 얇다'가 아니라 '보지 못했다'입니다. "
    "limit=1000 스냅샷은 약 ±0.15%를 덮으므로 ±0.25%·±0.5%·±1% 밴드는 하한값입니다.")

JOURNAL_NOTE = (
    "forward context journal은 주문과 연결되지 않고 수동매매를 강제 기록하지도 않습니다. "
    "시장 context만 append-only로 남기며, Market Context R0의 Q1~Q3가 Layer B 3.61시간에 "
    "막혔던 원자료를 잃지 않는 것이 목적입니다. 패널은 이 파일을 다시 읽지 않습니다.")

#: Every note, in the order the panel renders them. The payload carries this list so that a note
#: cannot be stripped from the vocabulary scan and then quietly never shown.
NOTES: tuple[tuple[str, str], ...] = (
    ("scope", SCOPE_NOTE),
    ("no_rollup", NO_ROLLUP_NOTE),
    ("missing_value", MISSING_VALUE_NOTE),
    ("structure_series", STRUCTURE_SERIES_NOTE),
    ("prev_session", PREV_SESSION_NOTE),
    ("observed_range", OBSERVED_RANGE_NOTE),
    ("wall_presence", WALL_PRESENCE_NOTE),
    ("vanish", VANISH_NOTE),
    ("absorption", ABSORPTION_NOTE),
    ("derivatives", DERIVATIVES_NOTE),
    ("auxiliary", AUXILIARY_NOTE),
    ("directional", DIRECTIONAL_NOTE),
    ("journal", JOURNAL_NOTE),
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def contract_identity() -> dict[str, Any]:
    """What the panel claims to implement, and whether the document on disk still agrees.

    Returned with every payload. A recorded hash that no longer matches the file is reported as
    `AGREES: false` rather than raised, because a panel that refuses to render is a worse failure
    than one that renders with a visible warning - and the operator can see the warning.
    """
    document = _repo_root() / CONTRACT_RELATIVE_PATH
    sidecar = document.with_suffix(".sha256")
    computed = None
    if document.exists():
        with open(document, "rb") as handle:
            computed = hashlib.sha256(handle.read()).hexdigest()
    recorded = None
    if sidecar.exists():
        with open(sidecar, "r", encoding="utf-8") as handle:
            recorded = handle.read().split()[0] or None
    return {"contract_version": CONTRACT_VERSION,
            "contract_path": CONTRACT_RELATIVE_PATH,
            "contract_sha256": computed,
            "contract_sha256_recorded": recorded,
            "agrees": bool(computed is not None and computed == recorded)}


def freshness_bounds() -> dict[str, int]:
    """The bounds each layer is judged against, read from the contracts that own them."""
    return {"depth_stale_ms": DEPTH_STALE_MS, "trade_stale_ms": TRADE_STALE_MS,
            "journal_stale_ms": JOURNAL_STALE_MS, "structure_grace_ms": STRUCTURE_GRACE_MS,
            "derivatives_stale_ms": DERIVATIVES_STALE_MS}


def notes_payload() -> dict[str, str]:
    return {key: text for key, text in NOTES}


__all__ = ["CONTRACT_VERSION", "CONTRACT_RELATIVE_PATH", "LAYERS", "LIVE", "STALE", "PARTIAL",
           "UNKNOWN", "COMPLETE", "LAYER_STATES", "UNAVAILABLE", "MISSING", "BTC",
           "SUPPORTED_SYMBOLS", "REASON_NOT_CALIBRATED", "REASON_COLLECTOR_BTC_ONLY",
           "REASON_RESEARCH_BTC_ONLY", "THETA_1H", "THETA_5M", "ANCHOR_DAYS",
           "PUBLISH_WINDOW_MS", "STRUCTURE_GRACE_MS", "MINUTE_MS", "DAY_MS", "WALL_OK",
           "WALL_NONE", "WALL_STATES", "WALL_PRESENCE_BASE_RATE_ASK_PCT",
           "WALL_PRESENCE_BASE_RATE_BID_PCT", "WALL_MEAN_PER_SIDE_WHEN_PRESENT", "FLOW_WINDOWS",
           "VANISH_CONSUMED_CANDIDATE", "VANISH_CANCEL_LIKE", "VANISH_STATES",
           "VANISH_TOUCHED_BIN_ASK_PCT", "VANISH_TOUCHED_BIN_BID_PCT", "ABSORPTION_CANDIDATE",
           "OI_CHANGE_WINDOWS", "OI_HIST_PERIOD", "DERIVATIVES_STALE_MS", "WEIGHT_AUXILIARY",
           "WEIGHT_WEAK_AUXILIARY", "JOURNAL_RECORD", "JOURNAL_SCHEMA_VERSION", "NOTES",
           "SCOPE_NOTE", "NO_ROLLUP_NOTE", "MISSING_VALUE_NOTE", "WALL_PRESENCE_NOTE",
           "VANISH_NOTE", "ABSORPTION_NOTE", "DERIVATIVES_NOTE", "AUXILIARY_NOTE",
           "DIRECTIONAL_NOTE", "PREV_SESSION_NOTE", "STRUCTURE_SERIES_NOTE",
           "OBSERVED_RANGE_NOTE", "JOURNAL_NOTE", "contract_identity", "freshness_bounds",
           "notes_payload", "DEPTH_STALE_MS", "TRADE_STALE_MS", "JOURNAL_STALE_MS"]

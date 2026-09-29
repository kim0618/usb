"""The frozen BTC-VOL-A contract: constants bound to the prose, and the freeze record."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

CONTRACT = Path("docs/crypto/btc_vol_a/CRYPTO_BTC_VOL_A_DERIBIT_FREE_AUDIT_CONTRACT_V1.md")
FREEZE = Path("data/research/crypto/btc_vol_a/contract_v1.json")

RECORD = "CRYPTO_BTC_VOL_A_DERIBIT_FREE_AUDIT_CONTRACT_V1"

DATASET_URL = ("https://datasets.tardis.dev/v1/deribit/options_chain/"
               "{year:04d}/{month:02d}/{day:02d}/OPTIONS.csv.gz")

#: P1's own preregistered confidence levels. Nothing new is invented here.
PRIMARY_THRESHOLD = 0.75
SECONDARY_THRESHOLDS = (0.70, 0.60)

#: (horizon_minutes, threshold_bp). The primary was chosen before any price was read, on the
#: count of signals that land on free dates.
PRIMARY_COMBO = (720, 100)
REPORTED_COMBOS: tuple[tuple[int, int], ...] = ((240, 100), (720, 100), (1440, 200))
EXCLUDED_COMBOS: tuple[tuple[int, int], ...] = ((720, 200),)

#: Expiry must clear the horizon by this much, so settlement mechanics do not distort the quote.
EXPIRY_BUFFER_MINUTES = 120

#: A quote older than this is not used; the event becomes NOT_MEASURABLE rather than being
#: filled from a stale book.
STALE_TOLERANCE_SECONDS = 300

STRADDLE_COEFFICIENT = 0.7978845608
HOURS_PER_YEAR = 365.25 * 24

# --- VOL-P0 reference values for the primary combo, read-only ------------------------------
VOL_P0_PROXY_IV = 1.035
VOL_P0_FORWARD_RV = 0.864
VOL_P0_BREAKEVEN_IV = 0.865

# --- verdict gates ---------------------------------------------------------------------------
MIN_MEASURABLE_EVENTS = 5
MAX_UNMEASURABLE_SHARE = 0.50

CONFIRMS_UNPROMISING = "CONFIRMS_UNPROMISING"
EARLY_EXIT_WORTH_STUDYING = "EARLY_EXIT_WORTH_STUDYING"
INCONCLUSIVE = "INCONCLUSIVE"

NOT_MEASURABLE = "NOT_MEASURABLE"

STRING_BINDINGS: tuple[tuple[str, str], ...] = (
    ("dataset url", "`https://datasets.tardis.dev/v1/deribit/options_chain/"
                    "{YYYY}/{MM}/{DD}/OPTIONS.csv.gz`"),
    ("free policy quote", "only historical CSV market datasets for the first day of each month "
                          "are available"),
    ("free date count", "| 그 안의 무료 날짜 | **57** (2022-01-01 ~ 2026-09-01, 매달 1일) |"),
    ("primary threshold", "| **PRIMARY** | **p >= 0.75** |"),
    ("primary combo", "| **PRIMARY** | **12H ±1%** |"),
    ("excluded combo", "| 제외 | 12H ±2% | p>=0.75 신호 **0개** |"),
    ("expiry buffer", "| **buffer** | **2시간** (만기 근접 왜곡 회피) |"),
    ("stale tolerance", "| stale 허용치 | **300초** |"),
    ("no future quote", "| **t 이후 quote** | **사용 금지** |"),
    ("atm rule", "| 규칙 | `abs(strike_price - underlying_price)` **최소** |"),
    ("iv aggregation", "**call `mark_iv`와 put `mark_iv`의 단순 평균**"),
    ("straddle ask", "`call.ask_price + put.ask_price` (**매수자가 실제로 내는 값**)"),
    ("breakeven", "| **VOL-P0 손익분기 IV** | **86.5%** |"),
    ("check b", "### 8.2 Check B - 만기 일치 (**결정적**)"),
    ("min events", "측정 가능한 event가 **5개 미만**"),
    ("unmeasurable share", "event의 **50% 초과**가 quote 부재/품질 실패"),
    ("confirms gate", "Check B의 **median edge <= 0**"),
    ("early exit gate", "Check B의 **median edge > 0**"),
    ("no early exit calc", "**early-exit 계산** (5분/15분/30분/최적 exit 전부)"),
    ("btc only", "`symbol`이 `BTC-`로 시작하는 행만"),
    ("deribit quotes in btc", "**Deribit BTC 옵션은 BTC로 호가된다.**"),
)


class ContractHashMismatch(RuntimeError):
    """The contract document changed after it was frozen."""


class ContractMismatch(RuntimeError):
    """The document and the code disagree about a preregistered value."""


def sha256(path: Path = CONTRACT) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_bindings(text: str | None = None) -> None:
    body = CONTRACT.read_text(encoding="utf-8") if text is None else text
    missing = [label for label, needle in STRING_BINDINGS if needle not in body]
    if missing:
        raise ContractMismatch(
            "the contract no longer states these preregistered values: " + ", ".join(missing))


def freeze(write: bool = True) -> dict[str, Any]:
    verify_bindings()
    payload = {
        "record": RECORD,
        "contract_path": str(CONTRACT),
        "contract_sha256": sha256(),
        "frozen_before_any_option_price_was_read": True,
        "permitted_before_freeze": ["file existence", "schema", "date counts", "signal counts"],
        "forbidden_before_freeze": ["implied volatility", "premium", "PnL",
                                    "realized comparison"],
        "primary_threshold": PRIMARY_THRESHOLD,
        "primary_combo": {"horizon_minutes": PRIMARY_COMBO[0],
                          "threshold_bp": PRIMARY_COMBO[1]},
        "orders": 0, "api_keys": 0, "data_purchased": False, "deployed": False,
    }
    if write:
        FREEZE.parent.mkdir(parents=True, exist_ok=True)
        FREEZE.write_text(json.dumps(payload, indent=1))
    return payload


def require_frozen() -> str:
    if not FREEZE.exists():
        raise ContractHashMismatch(f"no freeze record at {FREEZE}")
    recorded = json.loads(FREEZE.read_text())["contract_sha256"]
    actual = sha256()
    if recorded != actual:
        raise ContractHashMismatch(
            f"CONTRACT_HASH_MISMATCH: frozen {recorded[:16]}... now {actual[:16]}...")
    verify_bindings()
    return actual

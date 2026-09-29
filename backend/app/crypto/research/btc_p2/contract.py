"""The frozen BTC-P2 contract: constants, string bindings, and the freeze record.

The string bindings matter more here than usual. P2 exists because P1's gate measured something
other than what P1's question asked, and the only defence against repeating that is a
preregistration whose numbers cannot quietly drift away from the prose describing them.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

CONTRACT = Path("docs/crypto/btc_p2/CRYPTO_BTC_P2_DIRECTION_CONTRACT_V1.md")
FREEZE = Path("data/research/crypto/btc_p2/contract_v1.json")

RECORD = "CRYPTO_BTC_P2_DIRECTION_CONTRACT_V1"
SEED = 20260928

# --- combos: (horizon_minutes, threshold_bp) ----------------------------------------------
COMBOS: tuple[tuple[int, int], ...] = ((240, 100), (720, 100), (720, 200), (1440, 200))

# --- gate ----------------------------------------------------------------------------------
GATE_QUANTILE = 0.75
GATE_DIAGNOSTIC_QUANTILE = 0.90
PRIMARY_SUBSET = "GATED"
REFERENCE_SUBSET = "ALL"

# --- models ----------------------------------------------------------------------------------
M1_L2 = 1.0
M1_ITERS = 30
M2_TREES = 200
M2_MAX_DEPTH = 3
M2_LEARNING_RATE = 0.05
M2_MIN_SAMPLES_LEAF = 50
M2_BINS = 32
M2_LEAF_L2 = 1.0

# --- permutation control ------------------------------------------------------------------
PERMUTATIONS = 200
PERMUTATION_PERCENTILE = 99

# --- metrics --------------------------------------------------------------------------------
RELIABILITY_BUCKETS = 10
CONFIDENCE_HIGH = (0.60, 0.65, 0.70)
CONFIDENCE_LOW = (0.40, 0.35, 0.30)
DECILES = 10

# --- gates -----------------------------------------------------------------------------------
MIN_DIRECTIONAL_ROWS = 2_000
D1_AUC = 0.55
D2_FOLD_AUC = 0.52
D2_MIN_FOLDS = 6
D2_TOTAL_FOLDS = 8
D3_MIN_YEARS = 4
D3_TOTAL_YEARS = 5
D4_ECE_MAX = 0.06
D5_DECILE_GAP = 0.08

WEAK_AUC = 0.52

STRONG = "STRONG_DIRECTION"
WEAK = "WEAK_DIRECTION"
NO_DIRECTION = "NO_DIRECTION"
INCONCLUSIVE = "INCONCLUSIVE"

VALIDATION_YEARS = ("2022", "2023", "2024", "2025", "2026")

STRING_BINDINGS: tuple[tuple[str, str], ...] = (
    ("p1 is not a direction model", "**P1은 `LARGE_MOVE MODEL`이다.** 방향 모델이 아니다."),
    ("gate source", "fold별 validation 예측**만 쓴다"),
    ("no frozen artifact gate",
     "**forward용 fold-9 frozen artifact를 과거 전체에 적용해 gate를 만들지 않는다.**"),
    ("gate quantile", "`P_LARGE_MOVE >= (train fold의 0.75 분위)` = **상위 4분의 1**"),
    ("gate train only", "**train fold 행에서만.**"),
    ("combo 4h", "| **4H ±1.00%** |"),
    ("combo 12h 100", "| **12H ±1.00%** |"),
    ("combo 12h 200", "| **12H ±2.00%** |"),
    ("combo 24h", "| **24H ±2.00%** |"),
    ("ambiguous dropped", "**`AMBIGUOUS`는 제외한다. 규칙으로 채우지 않는다.**"),
    ("t3 rejected", "### 5.3 T3 — Dominant Excursion (**동결 전 기각**)"),
    ("feature count", "**7 family, D2-only 34컬럼 + external 6컬럼 = 40.**"),
    ("no volatility family", "**변동성 family가 없다.**"),
    ("primary feature set", "| **`D2_ONLY`** | 34 | **PRIMARY** |"),
    ("folds", "**8개 fold.**"),
    ("embargo", "**embargo 1,440분**"),
    ("single target", "**`P(UP_FIRST)` 하나의 이진 target**"),
    ("m2 leaf", "**min samples/leaf 50**"),
    ("permutations", "| 반복 | **200** |"),
    ("permutation percentile", "치환 분포의 **99분위를 초과**해야 한다"),
    ("min rows", "gate 후 directional 행 < **2,000**"),
    ("d1", "direction AUC > **0.55**"),
    ("d2", "**8개 중 6개 이상**"),
    ("d3", "**5개 중 4개 이상**"),
    ("d4", "ECE <= **0.06**"),
    ("d5", "**실제 UP 비율 차이 >= 8%p**"),
    ("weak", "AUC > **0.52** 이나 STRONG 미달"),
    ("no direction", "AUC <= **0.52**"),
    ("p3 gate",
     "**PRIMARY target(T1)에서 최소 하나가 `STRONG_DIRECTION`이어야 BTC-P3 = AUTHORIZED**"),
    ("no per side pass",
     "**UP target AUC와 DOWN target AUC가 각각 높다는 이유로 통과시키지 않는다.**"),
    ("seed", "seed **20260928**"),
    ("binance unit trap",
     "**Binance spot은 2025-01부터 타임스탬프를 마이크로초로 바꿨고 USD-M 선물은 밀리초를 유지한다.**"),
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
        "seed": SEED,
        "frozen_before_any_direction_metric": True,
        "permitted_before_freeze": ["class counts", "feature availability", "missingness",
                                    "base rates"],
        "forbidden_before_freeze": ["direction AUC", "Brier", "log loss", "model outcome",
                                    "PnL"],
        "combos": [{"horizon_minutes": h, "threshold_bp": b} for h, b in COMBOS],
        "gate_quantile": GATE_QUANTILE,
        "primary_target": "FIRST_TOUCH",
        "primary_feature_set": "D2_ONLY",
        "trading": {"orders": 0, "pnl_computed": False},
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
            f"CONTRACT_HASH_MISMATCH: frozen {recorded[:16]}... but the document now hashes to "
            f"{actual[:16]}...")
    verify_bindings()
    return actual

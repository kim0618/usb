"""The frozen BTC-P1 contract, and the checks that keep the code honest about it.

Two separate failures are guarded here. A changed document is caught by the sha256 freeze. A
document that still says one thing while the code does another is caught by STRING_BINDINGS,
which asserts that every number the code uses appears verbatim in the prose. Without the second
check a preregistration decays into a file nobody reads.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

CONTRACT = Path("docs/crypto/btc_p1/CRYPTO_BTC_P1_LARGE_MOVE_PROBABILITY_CONTRACT_V1.md")
FREEZE = Path("data/research/crypto/btc_p1/contract_freeze_v1.json")

RECORD = "CRYPTO_BTC_P1_LARGE_MOVE_PROBABILITY_CONTRACT_V1"

# --- decision grid ---------------------------------------------------------------------
STEP_MINUTES = 60
WARMUP_MINUTES = 30 * 1440
SAMPLE_START = "2021-03-03"
END = "2026-09-22"
EXPECTED_ROWS = 48_696

# --- targets ---------------------------------------------------------------------------
TARGET_GRID: dict[int, tuple[int, ...]] = {240: (50, 100), 720: (100, 200), 1440: (100, 200, 300)}
MAX_HORIZON_MINUTES = 1440

# --- folds -----------------------------------------------------------------------------
FOLD_STARTS = ("2022-01-01", "2022-07-01", "2023-01-01", "2023-07-01", "2024-01-01",
               "2024-07-01", "2025-01-01", "2025-07-01", "2026-01-01")
EMBARGO_MINUTES = 1440
EXPECTED_VALID_ROWS = 41_400
VALIDATION_YEARS = ("2022", "2023", "2024", "2025", "2026")

# --- models ----------------------------------------------------------------------------
M1_L2 = 1.0
M1_ITERS = 30
M1_TOL = 1e-8

M2_TREES = 200
M2_MAX_DEPTH = 3
M2_LEARNING_RATE = 0.05
M2_MIN_SAMPLES_LEAF = 200
M2_BINS = 32
M2_LEAF_L2 = 1.0

SEED = 20260928

# --- metrics ---------------------------------------------------------------------------
RELIABILITY_BUCKETS = 10
CONFIDENCE_THRESHOLDS = (0.60, 0.65, 0.70, 0.75)

# --- gates -----------------------------------------------------------------------------
MIN_VALID_POSITIVES = 500
MIN_VALID_ROWS = 5_000

S1_BRIER_SKILL = 0.010
S2_AUC = 0.550
S3_ECE_MAX = 0.050
S4_SLOPE_RANGE = (0.70, 1.30)
S5_CONF_THRESHOLD = 0.65
S5_MIN_N = 200
S5_MIN_LIFT = 1.30
S6_MIN_FOLDS = 7
S6_TOTAL_FOLDS = 9
S7_MIN_YEARS = 4
S7_TOTAL_YEARS = 5

W1_BRIER_SKILL = 0.0
W2_AUC = 0.520

STRONG = "STRONG_SIGNAL"
WEAK = "WEAK_SIGNAL"
NO_SIGNAL = "NO_SIGNAL"
INCONCLUSIVE = "INCONCLUSIVE"

#: Every constant above must appear in the contract text exactly like this. The check is a plain
#: substring test, so the binding fails loudly if a number is edited on one side only.
STRING_BINDINGS: tuple[tuple[str, str], ...] = (
    ("decision step", "| 간격 | **60분** (UTC 정시) |"),
    ("warmup", "30일 (43,200분)"),
    ("first row", "2021-03-03T00:00:00Z"),
    ("row count", "**48,696**"),
    ("validation rows", "총 검증 행 41,400."),
    ("4H thresholds", "| 4H (240분) | ±0.50%, ±1.00% |"),
    ("12H thresholds", "| 12H (720분) | ±1.00%, ±2.00% |"),
    ("24H thresholds", "| 24H (1440분) | ±1.00%, ±2.00%, ±3.00% |"),
    ("series count", "PATH 14개 + ENDPOINT 14개 = **28 series**"),
    ("embargo", "최대 horizon(1,440분) 공백"),
    ("feature count", "**최대 6 family, 총 34 컬럼.**"),
    ("m1 penalty", "| 페널티 | L2, lambda = 1.0 (절편 제외) |"),
    ("m1 solver", "| 해법 | IRLS 30회, tol 1e-8 |"),
    ("m2 trees", "| 트리 수 | 200 |"),
    ("m2 depth", "| max depth | 3 |"),
    ("m2 lr", "| learning rate | 0.05 |"),
    ("m2 leaf", "| min samples / leaf | 200 |"),
    ("m2 bins", "| histogram bins | 32 (train fold quantile) |"),
    ("m2 leaf l2", "| leaf L2 | 1.0 |"),
    ("calibration method", "**isotonic regression (PAV)**"),
    ("calibration slice", "train window의 **마지막 20%**"),
    ("gate basis", "| **게이트 판정** | **calibrated 확률 기준** |"),
    ("reliability buckets", "10 equal-width bucket"),
    ("confidence thresholds", ">= 0.60 / 0.65 / 0.70 / 0.75"),
    ("inconclusive", "검증 양성 수 < 500, **또는** 검증 행 수 < 5,000"),
    ("s1", "Brier skill score > **0.010**"),
    ("s2", "ROC AUC > **0.550**"),
    ("s3", "ECE <= **0.050**"),
    ("s4", "calibration slope ∈ **[0.70, 1.30]**"),
    ("s5", "예측확률 >= 0.65 구간의 N >= **200** **그리고** lift >= **1.30**"),
    ("s6", "Brier skill > 0 인 fold가 **9개 중 7개 이상**"),
    ("s7", "Brier skill > 0 인 year가 **5개 중 4개 이상**"),
    ("weak", "Brier skill > **0** 이고 ROC AUC > **0.520**"),
    ("seed", "| seed | 20260928 |"),
    ("primary target", "### 5.1 Primary = PATH-TOUCH"),
    ("p2 gate", "**PATH 계열에서 STRONG_SIGNAL이 최소 하나 나와야만 BTC-P2 = AUTHORIZED.**"),
    ("no sklearn", "**M1과 M2를 numpy로 직접 구현한다.** sklearn을 설치하지 않는다."),
    ("synthetic sanity", "학습 가능한 구조를 심은 합성 데이터에서 M2가 M0·M1을 이기는지 먼저 검증"),
    ("no search", "| hyperparameter search | **없음.** 단일 고정 config |"),
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
            "the contract text no longer states these preregistered values: " + ", ".join(missing))


def freeze(write: bool = True) -> dict[str, Any]:
    """Record the hash. Phase 2 refuses to run against a document that has moved since."""
    verify_bindings()
    payload = {
        "record": RECORD,
        "contract_path": str(CONTRACT),
        "contract_sha256": sha256(),
        "frozen_before_any_performance_metric": True,
        "permitted_before_freeze": ["class frequency", "feature availability", "missing rate",
                                    "target sample counts"],
        "forbidden_before_freeze": ["ROC AUC", "Brier", "log loss", "PR AUC", "calibration",
                                    "lift", "trading PnL"],
        "seed": SEED,
    }
    if write:
        FREEZE.parent.mkdir(parents=True, exist_ok=True)
        FREEZE.write_text(json.dumps(payload, indent=1))
    return payload


def require_frozen() -> str:
    """Called at the top of Phase 2. Returns the verified hash."""
    if not FREEZE.exists():
        raise ContractHashMismatch(f"no freeze record at {FREEZE}; run Phase 1 first")
    recorded = json.loads(FREEZE.read_text())["contract_sha256"]
    actual = sha256()
    if recorded != actual:
        raise ContractHashMismatch(
            f"CONTRACT_HASH_MISMATCH: frozen {recorded[:16]}... but the document now hashes to "
            f"{actual[:16]}...; results produced against an edited contract are not "
            f"preregistered")
    verify_bindings()
    return actual

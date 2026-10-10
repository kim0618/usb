"""The vendored Market Context V1 code is the source, not a re-implementation.

Three checks, from weakest to strongest:

1. **Provenance.** `flow.py` hashes to the recorded source hash; `contract.py` does too once its two
   import lines are put back one level shallower; `liquidity.py` is pinned to its own hash, and the
   only functions it lacks are the two I/O functions the package docstring names.
2. **Golden vectors.** `ctx_v1_mc_golden.json.gz` holds 33 viewer readings from eleven scripted
   scenarios and the LIQUIDITY and FLOW payloads the **source** code produced for them, with one
   `VanishTracker` across the whole sequence. Every verdict the panel can print appears in it:
   CANCEL_LIKE, CONSUMED_CANDIDATE, UNKNOWN for three different reasons, ABSORPTION_CANDIDATE on
   both sides, and NONE for three different reasons. The vendored code must reproduce every byte.
3. **Direct.** When `app.crypto.market_context_v1` is importable, the same inputs go through both.
"""
from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from app.crypto.context_collector_v1 import mcv1_vendored as VENDORED
from app.crypto.context_collector_v1.mcv1_vendored import flow as VF
from app.crypto.context_collector_v1.mcv1_vendored import liquidity as VL

HERE = Path(__file__).resolve().parent
VENDOR_DIR = Path(VENDORED.__file__).resolve().parent
GOLDEN = HERE / "ctx_v1_mc_golden.json.gz"
#: `liquidity.py` as vendored. It has no source hash of its own because two functions were cut.
VENDORED_LIQUIDITY_SHA256 = "0ccbfd10c74036fe20d4c76cd678ee20976c061d3c064f990a29993bf7c17ce6"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def golden() -> dict:
    return json.loads(gzip.decompress(GOLDEN.read_bytes()))


def run(liquidity_module, flow_module, inputs: list[dict]) -> list[dict]:
    tracker = flow_module.VanishTracker()
    outputs = []
    for snapshot in inputs:
        liquidity = liquidity_module.liquidity_view(snapshot, symbol="BTCUSDT")
        flow = flow_module.flow_view(snapshot, liquidity, symbol="BTCUSDT", tracker=tracker)
        outputs.append({"liquidity": liquidity, "flow": flow})
    return outputs


# --------------------------------------------------------------------------- provenance

def test_flow_is_byte_identical_to_the_source():
    assert sha((VENDOR_DIR / "flow.py").read_bytes()) == VENDORED.SOURCE_SHA256["flow.py"]


def test_contract_differs_only_in_the_depth_of_two_relative_imports():
    text = (VENDOR_DIR / "contract.py").read_text()
    assert text.count("from ...") == 2
    restored = text.replace("from ...liquidity_map", "from ..liquidity_map").replace(
        "from ...market_structure_v0", "from ..market_structure_v0")
    assert sha(restored.encode()) == VENDORED.SOURCE_SHA256["contract.py"]


def test_liquidity_lost_only_its_io_functions():
    data = (VENDOR_DIR / "liquidity.py").read_bytes()
    assert sha(data) == VENDORED_LIQUIDITY_SHA256
    text = data.decode()
    assert "def read(" not in text and "_read_async" not in text
    assert "import asyncio" not in text and "liquidity_map import api" not in text
    for kept in ("def liquidity_view(", "def _layer_state(", "def _wall_state(",
                 "def _side_view(", "def unavailable_view("):
        assert kept in text


def test_the_golden_vectors_were_made_by_the_recorded_source():
    assert golden()["reference_sha256"] == VENDORED.SOURCE_SHA256


# --------------------------------------------------------------------------- golden

def test_the_golden_vectors_cover_every_verdict_the_panel_can_print():
    branches = golden()["coverage_of_branches"]
    for key in ("CANCEL_LIKE", "CONSUMED_CANDIDATE", "UNKNOWN",
                "absorption_ABSORPTION_CANDIDATE", "reason_BOOK_NOT_SYNCED",
                "reason_RESNAPSHOT_GENERATION_CHANGED",
                "reason_PATH_GAP_WIDER_THAN_SAMPLE_INTERVAL", "reason_MID_PATH_REACHED_THE_BIN",
                "reason_MID_PATH_NEVER_REACHED_THE_BIN",
                "absreason_AGGRESSIVE_FLOW_BELOW_WALL_NOTIONAL",
                "absreason_FLOW_WINDOW_NOT_COMPLETE", "wall_OK", "wall_NONE", "wall_UNKNOWN",
                "flow_LIVE", "flow_PARTIAL", "flow_UNKNOWN"):
        assert branches.get(key, 0) > 0, key
    sides = {o["flow"]["absorption"].get("side") for o in golden()["outputs"]
             if o["flow"]["absorption"]["state"] == "ABSORPTION_CANDIDATE"}
    assert sides == {"ASK", "BID"}


def test_the_vendored_code_reproduces_every_golden_output():
    data = golden()
    outputs = run(VL, VF, data["inputs"])
    assert len(outputs) == data["samples"] == len(data["outputs"])
    for index, (mine, expected) in enumerate(zip(outputs, data["outputs"])):
        assert json.loads(json.dumps(mine)) == expected, (index, data["scenario_of_sample"][index])


# --------------------------------------------------------------------------- direct

@pytest.mark.skipif(importlib.util.find_spec("app.crypto.market_context_v1") is None,
                    reason="the source package is not committed on this branch")
def test_the_vendored_code_matches_the_source_package_directly():
    from app.crypto.market_context_v1 import flow as SF
    from app.crypto.market_context_v1 import liquidity as SL
    data = golden()
    assert run(VL, VF, data["inputs"]) == run(SL, SF, data["inputs"])

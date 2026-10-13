"""Layer AUXILIARY: two closed BTC studies, read-only, and labelled by how much they are worth.

Rendered for BTCUSDT and absent - not blank - for every other instrument. A blank auxiliary panel
on ETHUSDT would read as "no signal at the moment" when the truth is "this work was never done on
this instrument", and those are different enough to be worth the extra branch.

Both entries are read, never evaluated:

**C1 / C1x** is read out of the store's own files as bytes. No C1 module is imported, no runtime is
constructed, no directory is created and nothing is written, so the C1 contract cannot be changed
from here even by accident. The panel does not decide whether a signal is valid; it reports what the
store last recorded and how old that is. Each record is passed through from a fixed key list, so a
C1 schema change shows up as a missing key on the screen rather than as this module reaching into a
shape it was not told about.

**Directional Probability** is read verbatim from the frozen research artifact. **No probability is
computed for the current moment**: no model is loaded and no inference is run. What is shown is the
study's verdict (`WEAK_DIRECTIONAL_MODEL`, `usable_horizons = []`), its sample window, and the
per-horizon discrimination as the artifact records it - together with the two later results that
bound how that may be read, because an AUC of 0.56 shown on its own will be over-read.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from . import contract as C

#: Where the C1 store keeps its append-only files. Overridable by the variable the deployed
#: terminal already uses for the same path, so the panel reads the same store the operator sees.
C1_ROOT_ENV = "CRYPTO_C1_ROOT"
C1_DEFAULT_ROOT = "data/runtime/crypto/c1"

#: Where the frozen research artifacts live. One variable so a reader can point the panel at a
#: copy without editing code.
RESEARCH_ROOT_ENV = "MC_V1_RESEARCH_ROOT"
RESEARCH_DEFAULT_ROOT = "data/research/crypto"

DIRECTIONAL_RELATIVE = "directional_probability_r0/results.json"

#: Keys lifted out of a C1 record. A fixed list, so a schema change is visible as an absence
#: rather than silently reshaping this panel.
#:
#: What is deliberately **not** on it: the shadow trade's plan - its entry instant, its entry
#: price and its planned exit. Those four are a proposal, and a context panel carrying a proposal
#: reads as one however it is labelled. The row's job is "what does C1 currently say exists", and
#: that is answered by the identity, the instant and the state.
SIGNAL_KEYS: tuple[str, ...] = ("signal_id", "triggered_at_ms", "signal_bar_ms", "state",
                                "strategy", "direction", "horizon_min", "schema_version",
                                "contract_sha256")
C1X_KEYS: tuple[str, ...] = ("signal_id", "parent_signal_id", "status", "confirmation_count",
                             "triggered_at_ms", "observed_at_ms", "executable_at_ms",
                             "premium_value", "premium_bucket", "schema_version")
STATE_KEYS: tuple[str, ...] = ("armed", "bars", "signals", "last_decided_at_ms",
                               "schema_version", "contract_sha256")

#: Horizons the frozen artifact reports, as the keys it uses.
DIRECTIONAL_HORIZONS: tuple[str, ...] = ("30", "60", "120", "240")

#: C1 decides once per closed 1m bar. Five missed decisions is not a slow poll, it is an engine
#: that is not running, so past this the row reads STALE rather than showing its last state as
#: current. The figure is the panel's own display bound and changes nothing in C1.
C1_STALE_MS = 5 * 60_000

#: Section 9. The later results that bound how the directional artifact may be read. Transcribed
#: from the two study records rather than recomputed, with the record each one comes from, so a
#: reader can check the sentence against its source.
DIRECTIONAL_BOUNDS: tuple[dict[str, Any], ...] = (
    {"study": "BTC-P1", "record": "data/research/crypto/btc_p1/model_results_v1.json",
     "finding": "a calibrated magnitude predictor; its directional AUC was 0.502 to 0.533"},
    {"study": "BTC-P2", "record": "data/research/crypto/btc_p2/results_v1.json",
     "finding": ("direction layer STRONG 0/64, all four primary cells below 0.5, and the P1 gate "
                 "made direction worse; P3 NOT_AUTHORIZED")},
)


def _c1_root() -> Path:
    """The C1 store's root. The key is read here as a literal rather than through a parameter.

    Not a style choice: `test_market_context_v1_isolation` resolves every environment read off the
    AST so it can assert the key list is closed, and a key arriving as a function argument reads as
    `UNRESOLVED` - which would let the test pass by finding nothing, defeating its purpose.
    """
    return Path(os.environ.get(C1_ROOT_ENV) or C1_DEFAULT_ROOT)


def _research_root() -> Path:
    """Where the frozen research artifacts live. Same reason for the literal."""
    return Path(os.environ.get(RESEARCH_ROOT_ENV) or RESEARCH_DEFAULT_ROOT)


def _read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def _last_line_record(path: Path) -> tuple[dict[str, Any] | None, int, str | None]:
    """The last intact JSON object in an append-only file, plus how many lines were intact.

    A final partial line is dropped rather than repaired, the way the store's own reader does: a
    process killed between write and flush leaves one and it is not a record.
    """
    if not path.exists():
        return None, 0, "FILE_NOT_FOUND"
    last: dict[str, Any] | None = None
    intact = 0
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    last = parsed
                    intact += 1
    except OSError as caught:
        return None, 0, f"UNREADABLE:{type(caught).__name__}"
    return last, intact, None if last is not None else "NO_INTACT_RECORD"


def _pick(record: dict[str, Any] | None, keys: tuple[str, ...]) -> dict[str, Any] | None:
    if record is None:
        return None
    return {key: record.get(key) for key in keys}


def _age(now_ms: int, path: Path) -> int | None:
    try:
        return max(0, now_ms - int(path.stat().st_mtime * 1000))
    except OSError:
        return None


def c1_view(now_ms: int, *, root: Path | None = None) -> dict[str, Any]:
    """What the C1 store last recorded. Reads bytes; imports no C1 module; writes nothing."""
    base = root or _c1_root()
    state = _read_json(base / "state.json")
    signal, signal_count, signal_reason = _last_line_record(base / "signals.jsonl")
    c1x, c1x_count, c1x_reason = _last_line_record(base / "c1x.jsonl")
    reasons = [f"signals.jsonl:{signal_reason}" for _ in (1,) if signal_reason]
    reasons += [f"c1x.jsonl:{c1x_reason}" for _ in (1,) if c1x_reason]
    present = state is not None or signal is not None or c1x is not None

    # Freshness is judged on the engine's own last decision, not on the file's mtime: an atomic
    # rewrite touches the mtime whether or not a bar was decided, so mtime can say "fresh" about
    # a state that has not moved. `last_decided_at_ms` is the figure C1 itself advances.
    decided = (state or {}).get("last_decided_at_ms") if isinstance(state, dict) else None
    decision_age = (None if not isinstance(decided, int)
                    else max(0, now_ms - int(decided)))
    if not present:
        layer = C.UNKNOWN
        reasons = ["C1_STORE_EMPTY_OR_ABSENT"] + reasons
    elif decision_age is None:
        layer = C.UNKNOWN
        reasons = ["C1_STATE_HAS_NO_LAST_DECISION"] + reasons
    elif decision_age > C1_STALE_MS:
        layer = C.STALE
        reasons = ["C1_ENGINE_LAST_DECIDED_OLDER_THAN_FRESHNESS_BOUND"] + reasons
    else:
        layer = C.LIVE
    return {
        "weight": C.WEIGHT_AUXILIARY,
        "state": layer,
        "reasons": reasons,
        "last_decision_age_ms": decision_age,
        "stale_ms": C1_STALE_MS,
        "root": str(base),
        "root_exists": base.exists(),
        "engine_state": _pick(state if isinstance(state, dict) else None, STATE_KEYS),
        "engine_state_age_ms": _age(now_ms, base / "state.json"),
        "latest_signal": _pick(signal, SIGNAL_KEYS),
        "latest_signal_age_ms": _age(now_ms, base / "signals.jsonl"),
        "signal_records": signal_count,
        "latest_c1x": _pick(c1x, C1X_KEYS),
        "c1x_records": c1x_count,
        "read_mode": "BYTES_ONLY_NO_C1_MODULE_IMPORTED",
        "contract_evaluated": False,
    }


def directional_view(*, root: Path | None = None) -> dict[str, Any]:
    """The frozen Directional Probability R0 verdict, read verbatim. Nothing is inferred."""
    base = root or _research_root()
    path = base / DIRECTIONAL_RELATIVE
    body = _read_json(path)
    if not isinstance(body, dict):
        return {"weight": C.WEIGHT_WEAK_AUXILIARY, "state": C.UNKNOWN,
                "reasons": ["ARTIFACT_NOT_READABLE"], "artifact": str(path),
                "verdict": None, "usable_horizons": None, "weak_horizons": None,
                "horizons": None, "bounds": list(DIRECTIONAL_BOUNDS),
                "computed_for_now": False, "note": C.DIRECTIONAL_NOTE}
    horizons: dict[str, Any] = {}
    for key in DIRECTIONAL_HORIZONS:
        entry = (body.get("horizons") or {}).get(key)
        if not isinstance(entry, dict):
            continue
        # The bucket names come out of the artifact rather than being spelled here. That keeps
        # this module independent of the study's own labelling, and it keeps the panel's source
        # free of the direction vocabulary it is banned from containing - the words belong to the
        # frozen artifact, and are rendered as data read from it.
        rows: dict[str, Any] = {}
        for bucket, payload in sorted(entry.items()):
            if not isinstance(payload, dict):
                continue
            probability = payload.get("probability")
            if not isinstance(probability, dict) or "auc" not in probability:
                continue
            rows[bucket] = {"auc": probability.get("auc"),
                            "brier_skill": probability.get("bss"),
                            "base_rate": probability.get("base_rate"),
                            "calibration_error": probability.get("ece")}
        horizons[key] = {"horizon_min": int(key), "samples": entry.get("n"),
                         "first": entry.get("first"), "last": entry.get("last"),
                         "buckets": rows}
    return {
        "weight": C.WEIGHT_WEAK_AUXILIARY,
        "state": C.LIVE,
        "reasons": [],
        "artifact": str(path),
        "verdict": body.get("verdict"),
        "usable_horizons": body.get("usable_horizons"),
        "weak_horizons": body.get("weak_horizons"),
        "horizons": horizons,
        "bounds": list(DIRECTIONAL_BOUNDS),
        # The field a later reader will look for first, so it is stated rather than implied.
        "computed_for_now": False,
        "inference_run": False,
        "note": C.DIRECTIONAL_NOTE,
    }


def absent_view(symbol: str) -> dict[str, Any]:
    """AUXILIARY for a non-BTC instrument: absent, with the reason, and no rows at all."""
    return {"state": C.UNAVAILABLE, "rendered": False, "symbol": symbol,
            "reasons": [C.REASON_RESEARCH_BTC_ONLY], "c1": None, "directional": None,
            "note": C.AUXILIARY_NOTE}


def auxiliary_view(*, symbol: str, now_ms: int, c1_root: Path | None = None,
                   research_root: Path | None = None) -> dict[str, Any]:
    """The AUXILIARY layer payload for one poll."""
    if symbol != C.BTC:
        return absent_view(symbol)
    c1 = c1_view(now_ms, root=c1_root)
    directional = directional_view(root=research_root)
    states = {c1["state"], directional["state"]}
    # The frozen artifact is always LIVE when it is readable - it is a file, not a feed - so the
    # layer's state is really C1's, and PARTIAL is the honest word for "one of the two reads".
    state = (C.LIVE if states == {C.LIVE}
             else C.STALE if C.STALE in states and C.UNKNOWN not in states
             else C.PARTIAL if C.LIVE in states else C.UNKNOWN)
    return {"state": state, "rendered": True, "symbol": symbol,
            "reasons": c1["reasons"] + directional["reasons"],
            "c1": c1, "directional": directional, "note": C.AUXILIARY_NOTE}


__all__ = ["c1_view", "directional_view", "auxiliary_view", "absent_view", "C1_ROOT_ENV",
           "RESEARCH_ROOT_ENV", "DIRECTIONAL_RELATIVE", "DIRECTIONAL_BOUNDS", "SIGNAL_KEYS",
           "C1X_KEYS", "STATE_KEYS", "DIRECTIONAL_HORIZONS", "C1_STALE_MS"]

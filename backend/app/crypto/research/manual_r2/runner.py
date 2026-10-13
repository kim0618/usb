"""Write the MANUAL-R2 contracts as machine-readable artefacts.

    PYTHONPATH=backend python -m app.crypto.research.manual_r2.runner

Design only. Nothing here reads or writes the production paper account.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import contract as C
from . import decision as D

REPO_ROOT = Path(__file__).resolve().parents[5]
OUT_DIR = REPO_ROOT / "data/research/crypto/manual_r2"


def run(write: bool = True) -> dict[str, Any]:
    provenance = C.contract_payload()
    schema = D.schema_payload()
    if write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "provenance_contract_v1.json").write_text(json.dumps(provenance, indent=1))
        (OUT_DIR / "human_decision_schema_v1.json").write_text(json.dumps(schema, indent=1))
    return {"provenance_contract": provenance, "human_decision_schema": schema}


def main() -> int:
    payload = run()
    origins = payload["provenance_contract"]["vocabulary"]["origins"]
    codes = payload["human_decision_schema"]["reason_codes"]["values"]
    print("MANUAL-R2  design only, production untouched")
    print(f"  provenance: {len(origins)} origins, "
          f"default {payload['provenance_contract']['defaults']['unstated_origin_on_live_call']}")
    print(f"  decisions:  {len(codes)} reason codes, "
          f"option {payload['human_decision_schema']['option_choice']}")
    print(f"  known violation: "
          f"{payload['provenance_contract']['known_violation']['file']} "
          f"{payload['provenance_contract']['known_violation']['current']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

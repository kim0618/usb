"""Generate docs/crypto/CRYPTO_D5_2_CANDIDATE_REGISTRY_V1.md from data/runtime/crypto/d5_2/cells_v1.json."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

SRC = Path("data/runtime/crypto/d5_2/cells_v1.json")
DOC = Path("docs/crypto/CRYPTO_D5_2_CANDIDATE_REGISTRY_V1.md")


def _bp(x):
    return "-" if x is None else f"{x * 1e4:+.2f}"


def _ratio(c):
    r = c["cost_ratio"]["VIP0_BASE"]["ratio"]
    return r if isinstance(r, str) else ("-" if r is None else f"{r:.2f}")


def main() -> None:
    r = json.loads(SRC.read_text())
    cells = sorted(r["cells"].values(), key=lambda c: (c["feature"], c["horizon_min"], c["bucket"], c["side"]))
    cnt = Counter(c["verdict"] for c in cells)
    rep = r.get("repackaging", {})
    lines = ["# US-B CRYPTO D5.2 Candidate Registry V1", "",
             f"생성물 (`backend/app/crypto/research/registry_d5_2.py`). 계약 sha256 `{r['contract_sha256']}`. 원본 `{SRC}`.", "",
             f"전체 {len(cells)}셀: " + ", ".join(f"{k} {v}" for k, v in sorted(cnt.items())) + f". Decision gate CASE **{r['gate']['case']}**.", "",
             "gross/net = OOS 합산 평균(bp, ZERO / VIP0_BASE). ratio = gross / (gross - net) (VIP0_BASE). REPACK = 계약 11절 |Spearman| ≥ 0.7.", "",
             "| cell | verdict | gross | net | net 95% CI | ratio | N_eff | 판정 fold | REPACK | 사유(앞 4개) |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for c in cells:
        o = c["oos"]
        ci = o["VIP0_BASE"].get("ci95")
        folds = sum(1 for f in c["folds"].values() if f["N"] >= 500 and f["days"] >= 20)
        flag = any(v.get("flag") for v in rep.get(c["feature_name"], {}).values())
        lines.append(f"| {c['cell']} | {c['verdict']} | {_bp(o['ZERO']['mean'])} | {_bp(o['VIP0_BASE']['mean'])} | "
                     f"{'-' if not ci else f'[{ci[0]*1e4:+.1f}, {ci[1]*1e4:+.1f}]'} | {_ratio(c)} | {o['N_eff']:.0f} | {folds} | "
                     f"{'Y' if flag else ''} | {', '.join(c['reasons'][:4])} |")
    DOC.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()

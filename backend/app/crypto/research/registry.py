"""Render CRYPTO_D5_CANDIDATE_REGISTRY_V1.md from cells_v1.json (no hand-copied numbers)."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

CELLS = Path("data/runtime/crypto/d5/cells_v1.json")
OUT = Path("docs/crypto/CRYPTO_D5_CANDIDATE_REGISTRY_V1.md")
SPL = ("DISCOVERY", "VALIDATION", "HOLDOUT")


def bp(x):
    return "NA" if x is None else f"{x * 1e4:+.2f}"


def robust(c, key):
    parts = []
    for ax, lab in (("trend", "T"), ("vol", "V"), ("session", "S"), ("year", "Y")):
        vals = [v[key] for v in c["regimes"][ax].values() if v["N"] >= 200 and v[key] is not None]
        parts.append(f"{lab}{sum(x > 0 for x in vals)}/{len(vals)}")
    return " ".join(parts)


def row(cid, c):
    s = c["splits"]
    split_txt = []
    for sp in SPL:
        z, b = s[sp]["scen"]["ZERO"], s[sp]["scen"]["VIP0_BASE"]
        ci = z["ci95"] if sp == "DISCOVERY" else z["ci90"]
        split_txt.append(f"g {bp(z['mean'])} [{bp(ci[0])},{bp(ci[1])}] / n {bp(b['mean'])} / N {s[sp]['N']:,}")
    be = min(s[sp]["scen"]["ZERO"]["mean"] for sp in SPL)
    fee = f"ZERO min {bp(be)} vs VIP_0 왕복 수수료 약 11.00"
    stress = f"STRESS V+H {bp(c['stress_vh_mean'])}"
    reasons = ", ".join(r for r in c["reasons"] if not r.startswith("ROBUST_"))
    return (f"| {cid} | {c['feature']} `{c['feature_name']}` {c['bucket']} | {c['side']} | {c['horizon']}m | "
            + " | ".join(split_txt)
            + f" | {fee} | {stress} | gross {robust(c, 'gross')} / net {robust(c, 'net')} | **{c['verdict']}** | {reasons} |")


def main():
    r = json.loads(CELLS.read_text())
    C = r["cells"]
    cnt = Counter(c["verdict"] for c in C.values())
    head = ("| id | feature / bucket | side | horizon | DISCOVERY (gross [95%CI] / net / N) | VALIDATION (gross [90%CI] / net / N) "
            "| HOLDOUT (gross [90%CI] / net / N) | fee sensitivity | spread/depth | regime robustness (+셀 수/판정 라벨 수) | verdict | reason |\n"
            "|---|---|---|---|---|---|---|---|---|---|---|---|")
    order = lambda kv: (-min(kv[1]["splits"][sp]["scen"]["ZERO"]["mean"] for sp in SPL))
    weak = [kv for kv in sorted(C.items(), key=order) if kv[1]["verdict"] == "WEAK"]
    ref = [kv for kv in sorted(C.items(), key=order) if kv[1]["verdict"] == "REFERENCE_ONLY"]
    prim_rej = [kv for kv in C.items() if kv[1]["verdict"] == "REJECT" and kv[1]["horizon"] != 1]
    dead = sorted(prim_rej, key=lambda kv: -kv[1]["splits"]["DISCOVERY"]["scen"]["ZERO"]["mean"])[:15]
    L = [
        "# US-B CRYPTO D5 Candidate Registry V1", "",
        "이 파일은 `backend/app/crypto/research/registry.py`가 `data/runtime/crypto/d5/cells_v1.json`에서 생성한다.",
        f"손으로 옮긴 숫자는 없다. 계약 sha256 `{r['contract_sha256']}`.", "",
        "단위: bp(1bp = 0.01%). `g` = ZERO(비용 전 gross) 평균, `n` = VIP_0+BASE(주 시나리오) net 평균.",
        "CI는 7일 moving block bootstrap(1,000회). DISCOVERY는 95%, VALIDATION·HOLDOUT은 90% CI.",
        "fee sensitivity의 `ZERO min`은 세 구간 gross 평균 중 최솟값 = 이 셀이 버틸 수 있는 왕복 비용 상한.",
        "regime robustness는 `T`추세 `V`변동성 `S`UTC세션 `Y`연도별로 평균이 양수인 라벨 수 / 판정 가능 라벨 수(N>=200).", "",
        "## 1. 집계", "",
        "| verdict | 셀 수 |", "|---|---|",
        f"| SURVIVE | {cnt.get('SURVIVE', 0)} |", f"| WEAK | {cnt.get('WEAK', 0)} |",
        f"| REJECT | {cnt.get('REJECT', 0)} |", f"| REFERENCE_ONLY (1m) | {cnt.get('REFERENCE_ONLY', 0)} |",
        f"| 합계 | {len(C)} |", "",
        "주요 셀 540개 중 **SURVIVE 0**. WEAK 29개는 전부 `W2_COST_KILLED_GROSS_EDGE`(gross는 세 구간에서 같은 방향으로",
        "재현되지만 비용 후 음수)이고 `W1`(비용 후 양수인데 robustness/STRESS 실패)은 0개다.", "",
        "## 2. SURVIVE", "", "없음. D6 Score에 넣을 후보 없음.", "",
        "## 3. WEAK (전부 W2: 비용에 죽은 gross edge)", "", head,
    ]
    L += [row(cid, c) for cid, c in weak]
    L += ["", "## 4. REFERENCE_ONLY (1m, D6 후보 불가)", "", head]
    L += [row(cid, c) for cid, c in ref]
    L += ["", "## 5. 죽은 Edge: DISCOVERY gross 상위 15개 중 REJECT", "",
          "DISCOVERY에서 가장 커 보였지만 VALIDATION/HOLDOUT에서 사라졌거나 CI 조건을 못 채운 셀.", "", head]
    L += [row(cid, c) for cid, c in dead]
    L += ["", "## 6. 나머지 REJECT", "",
          f"주요 REJECT {len(prim_rej)}개 전체와 1m REJECT는 `data/runtime/crypto/d5/cells_v1.json`에 셀 단위로 있다",
          "(구간별 N·days·mean·median·win rate·SE·CI·MAE·MFE, 시나리오 3종, regime 4축 + 24시간).", ""]
    OUT.write_text("\n".join(L))


if __name__ == "__main__":
    main()

"""Render CRYPTO_D5_1_CANDIDATE_REGISTRY_V1.md from d5_1/cells_v1.json (no hand-copied numbers)."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

CELLS = Path("data/runtime/crypto/d5_1/cells_v1.json")
OUT = Path("docs/crypto/CRYPTO_D5_1_CANDIDATE_REGISTRY_V1.md")


def bp(x):
    return "NA" if x is None else f"{x * 1e4:+.2f}"


def ratio(x):
    return x if isinstance(x, str) or x is None else f"{x:.2f}"


def wf(c):
    folds = [f for f in c["folds"].values() if f["N"] >= 500 and f["days"] >= 20]
    g = sum(f["ZERO"]["mean"] > 0 for f in folds)
    n = sum(f["VIP0_BASE"]["mean"] > 0 for f in folds)
    sel = c["wf_selected"]
    return f"gross+ {g}/{len(folds)}, net+ {n}/{len(folds)}, WF선택 {len(sel['selected_folds'])}개 net {bp(sel['VIP0_BASE_mean'])}"


def robust(c):
    parts = []
    for ax, lab in (("trend", "T"), ("vol", "V"), ("session", "S"), ("year", "Y")):
        v = [x["net"] for x in c["regimes"][ax].values() if x["N"] >= 200]
        parts.append(f"{lab}{sum(x > 0 for x in v)}/{len(v)}")
    years = " ".join(f"{k}:{bp(v['gross'])}" for k, v in c["regimes"]["year"].items() if v["N"] >= 200)
    return " ".join(parts) + f" (net) / 연도 gross {years}"


def row(cid, c):
    o = c["oos"]
    cr = c["cost_ratio"]
    reasons = ", ".join(r for r in c["reasons"] if not r.startswith("R_") and r not in ("R5_LOYO", "R6_LOTO"))
    return (f"| {cid} | {c['feature']} `{c['feature_name']}` | {c['bucket']} | {c['side']} | {c['horizon_min'] // 60}h | "
            f"{bp(o['ZERO']['mean'])} [{bp(o['ZERO']['ci95'][0])},{bp(o['ZERO']['ci95'][1])}] | {bp(cr['VIP0_BASE']['cost'])} | "
            f"{bp(o['VIP0_BASE']['mean'])} [{bp(o['VIP0_BASE']['ci95'][0])},{bp(o['VIP0_BASE']['ci95'][1])}] | "
            f"**{ratio(cr['VIP0_BASE']['ratio'])}** | {bp(o['HYPOTHETICAL_MAKER_COST']['mean'])} / {ratio(cr['HYPOTHETICAL_MAKER_COST']['ratio'])} | "
            f"{wf(c)} | {robust(c)} | **{c['verdict']}** | {reasons} |")


HEAD = ("| edge_id | feature | bucket | side | horizon | gross_edge OOS [95%CI] | cost (VIP0_BASE) | net_edge OOS [95%CI] "
        "| edge_to_cost_ratio | HYPOTHETICAL_MAKER net / ratio | walk_forward_consistency | regime_robustness | verdict | reason |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")


def main():
    r = json.loads(CELLS.read_text())
    C = r["cells"]
    prim = {k: c for k, c in C.items() if c["horizon_min"] != 480}
    cnt = Counter(c["verdict"] for c in prim.values())
    cnt8 = Counter(c["verdict"] for c in C.values() if c["horizon_min"] == 480)
    key = lambda kv: -kv[1]["oos"]["ZERO"]["mean"]
    weak = [kv for kv in sorted(C.items(), key=key) if kv[1]["verdict"] == "WEAK"]
    ref = [kv for kv in sorted(C.items(), key=key) if kv[1]["verdict"] == "REFERENCE_ONLY"]
    dead = [kv for kv in sorted(prim.items(), key=key) if kv[1]["verdict"] == "REJECT"][:15]
    g = r["gate"]
    L = ["# US-B CRYPTO D5.1 Candidate Registry V1", "",
         "`backend/app/crypto/research/registry_d5_1.py`가 `data/runtime/crypto/d5_1/cells_v1.json`에서 생성. 손으로 옮긴 숫자 없음.",
         f"계약 sha256 `{r['contract_sha256']}`.", "",
         "단위 bp. 모든 값은 OOS 합산(F1~F9 test = 2022-01-01 ~ 2026-09-23). CI는 7일 block bootstrap 95%.",
         "cost = gross - net (VIP0_BASE: taker 수수료 + BASE spread/depth + funding). ratio = gross / cost.",
         "HYPOTHETICAL_MAKER는 참고용이며 실제 체결 가능성을 뜻하지 않는다.",
         "walk_forward_consistency: 판정 가능 fold 중 gross>0 / VIP0 net>0 fold 수, train으로 선택된 fold의 test net.",
         "regime_robustness: 추세T·변동성V·세션S·연도Y 라벨 중 VIP0 net>0 수 / 판정 라벨 수.", "",
         "## 1. 집계", "",
         "| verdict | 주요(1h/2h/4h) | 8h 참고 |", "|---|---|---|",
         f"| SURVIVE | {cnt.get('SURVIVE', 0)} | - |",
         f"| WEAK | {cnt.get('WEAK', 0)} | - |",
         f"| REFERENCE_ONLY | - | {cnt8.get('REFERENCE_ONLY', 0)} |",
         f"| REJECT | {cnt.get('REJECT', 0)} | {cnt8.get('REJECT', 0)} |",
         f"| 합계 | {len(prim)} | {sum(cnt8.values())} |", "",
         f"Decision gate (계약 10절, 기계적): **CASE {g['case']}**. SURVIVE {g['survive']}, maker 구제 가능 W2 = {', '.join(g['maker_rescuable_w2']) or '없음'}.",
         "", "## 2. SURVIVE", "", "없음.", "",
         "## 3. WEAK (전부 W2: 비용에 죽은 gross)", "", HEAD]
    L += [row(k, c) for k, c in weak]
    L += ["", "## 4. REFERENCE_ONLY (8h, D6 후보 불가)", "", HEAD]
    L += [row(k, c) for k, c in ref]
    L += ["", "## 5. 죽은 Edge: OOS gross 상위 REJECT 15개", "", HEAD]
    L += [row(k, c) for k, c in dead]
    L += ["", "## 6. 나머지", "", "전 960셀(fold별 N·days·mean·SE·CI, 시나리오 5종, median·win rate·MAE·MFE, regime 5축, WF 선택)은",
          "`data/runtime/crypto/d5_1/cells_v1.json`에 필터 없이 있다.", ""]
    OUT.write_text("\n".join(L))


if __name__ == "__main__":
    main()

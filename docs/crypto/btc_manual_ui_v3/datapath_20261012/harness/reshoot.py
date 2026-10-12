import json, sys, time
from playwright.sync_api import sync_playwright
sys.path.insert(0, "/tmp/claude-1000/-home-tjd618/4fd8a997-a438-4f62-becb-27ec5a2f6eb7/scratchpad/preview")
OUT = sys.argv[1]
import importlib.util
spec = importlib.util.spec_from_file_location("qa", "/tmp/claude-1000/-home-tjd618/4fd8a997-a438-4f62-becb-27ec5a2f6eb7/scratchpad/preview/qa_shots.py")
# reuse helpers by exec of the top part only
src = open(spec.origin).read().split("with sync_playwright() as p:")[0].replace("OUT = sys.argv[1]", f"OUT = {OUT!r}")
g = {}; exec(src, g)
R = g["R"]; page_for = g["page_for"]; wait_strip = g["wait_strip"]; shot = g["shot"]; open_detail = g["open_detail"]; scroll_to_strip = g["scroll_to_strip"]
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx, pg = page_for(b, 4400, 1440, 900); wait_strip(pg); scroll_to_strip(pg); shot(pg, "1440-long-sim-fixture")
    open_detail(pg); scroll_to_strip(pg); shot(pg, "1440-long-sim-fixture-expanded"); ctx.close()
    ctx, pg = page_for(b, 4500, 1440, 900); wait_strip(pg); scroll_to_strip(pg); shot(pg, "1440-no-wall-fallback")
    open_detail(pg); scroll_to_strip(pg); shot(pg, "1440-no-wall-fallback-expanded"); ctx.close()
    b.close()
old = json.load(open(f"{OUT}/qa_shots.json")); old["shots"].update(R["shots"]); old["requests"]["total"] += R["requests"]["total"]; old["requests"]["non_get"] += R["requests"]["non_get"]; old["requests"]["binance"] += R["requests"]["binance"]
json.dump(old, open(f"{OUT}/qa_shots.json", "w"), indent=1, ensure_ascii=False)
print("reshoot requests", R["requests"])

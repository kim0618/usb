import glob, json, os, sys, time
from playwright.sync_api import sync_playwright

S = "/tmp/claude-1000/-home-tjd618/8bbdfdf2-dc8c-4f0d-8bce-c1ea176e386c/scratchpad/ui"
os.makedirs(S, exist_ok=True)
URL = "http://127.0.0.1:3199/crypto-paper"
chrome = glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-1223/chrome-linux*/chrome"))[0]
os.environ["LD_LIBRARY_PATH"] = os.path.expanduser("~/.local/emulib")
report = {}


def box(page, testid):
    el = page.locator(f'[data-testid="{testid}"]').locator("visible=true").first
    b = el.bounding_box()
    return None if b is None else {k: round(v, 1) for k, v in b.items()}


def overflow(page):
    return page.evaluate("() => ({sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth})")


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=chrome, args=["--no-sandbox"])
    for i, width in enumerate((390, 1440, 1600)):
        ctx = browser.new_context(viewport={"width": width, "height": 900}, device_scale_factor=1)
        page = ctx.new_page()
        reqs = []
        page.on("request", lambda r: reqs.append({"method": r.method, "url": r.url}) if "8199" in r.url else None)
        page.goto(URL)
        page.wait_for_selector('[data-testid="market-header"]', timeout=30000)
        page.wait_for_timeout(1500)
        r = {"before": {"tabs": box(page, "symbol-tabs"), "ticket": box(page, "order-ticket"),
                        "button": box(page, "hot-scan-button"), "overflow": overflow(page)}}
        page.screenshot(path=f"{S}/{width}_0_before.png")
        hot_before = sum("hot-coins" in q["url"] for q in reqs)
        # double click -> one request
        page.locator('[data-testid="hot-scan-button"]').dblclick()
        page.wait_for_timeout(120)
        r["scanning_label"] = page.locator('[data-testid="hot-scan-button"]').inner_text()
        r["scanning_disabled"] = page.locator('[data-testid="hot-scan-button"]').is_disabled()
        page.screenshot(path=f"{S}/{width}_1_scanning.png")
        page.wait_for_selector('[data-testid="hot-row-5"]', timeout=30000)
        page.wait_for_timeout(300)
        r["after_scan"] = {"tabs": box(page, "symbol-tabs"), "ticket": box(page, "order-ticket"),
                           "panel": box(page, "hot-coins-panel"), "overflow": overflow(page)}
        r["rows"] = page.locator('[data-testid^="hot-row-"]').evaluate_all("els => els.map(e => e.dataset.symbol)")
        r["scanned_at"] = page.locator('[data-testid="hot-scanned-at"]').inner_text()
        r["status"] = page.locator('[data-testid="hot-status"]').inner_text()
        r["selected_tab"] = page.locator('[role="tab"][aria-selected="true"]').get_attribute("data-testid")
        r["hot_requests_after_dblclick"] = sum("hot-coins" in q["url"] for q in reqs) - hot_before
        page.screenshot(path=f"{S}/{width}_2_result.png")
        # clicking a row selects nothing
        page.locator('[data-testid="hot-row-1"]').click()
        r["selected_tab_after_row_click"] = page.locator('[role="tab"][aria-selected="true"]').get_attribute("data-testid")
        r["stored_symbol"] = page.evaluate("() => localStorage.getItem('usb.crypto.symbol')")
        # failure keeps previous (browser-side 502 for the next scan only)
        page.route("**/api/crypto/hot-coins*", lambda route: route.fulfill(
            status=502, content_type="application/json",
            body=json.dumps({"error": {"code": "HOT_SCAN_UPSTREAM", "message": "Binance 시장 데이터를 읽지 못했습니다"}})))
        page.locator('[data-testid="hot-scan-button"]').click()
        page.wait_for_function("() => document.querySelector('[data-testid=hot-status]').innerText.includes('유지')", timeout=10000)
        r["failure_status"] = page.locator('[data-testid="hot-status"]').inner_text()
        r["rows_after_failure"] = page.locator('[data-testid^="hot-row-"]').evaluate_all("els => els.map(e => e.dataset.symbol)")
        page.screenshot(path=f"{S}/{width}_3_failure_kept.png")
        page.unroute("**/api/crypto/hot-coins*")
        # close, no polling for 12 s
        page.locator('[data-testid="hot-close"]').click()
        n = sum("hot-coins" in q["url"] for q in reqs)
        page.wait_for_timeout(12000)
        r["hot_requests_during_12s_idle"] = sum("hot-coins" in q["url"] for q in reqs) - n
        r["closed"] = {"tabs": box(page, "symbol-tabs"), "ticket": box(page, "order-ticket"), "overflow": overflow(page)}
        # reload restores
        page.reload()
        page.wait_for_selector('[data-testid="market-header"]', timeout=30000)
        page.wait_for_selector('[data-testid="hot-show-button"]', timeout=10000)
        m = sum("hot-coins" in q["url"] for q in reqs)
        page.locator('[data-testid="hot-show-button"]').click()
        r["reload_rows"] = page.locator('[data-testid^="hot-row-"]').evaluate_all("els => els.map(e => e.dataset.symbol)")
        r["reload_scanned_at"] = page.locator('[data-testid="hot-scanned-at"]').inner_text()
        r["reload_scanned_requests"] = sum("hot-coins" in q["url"] for q in reqs) - m
        page.screenshot(path=f"{S}/{width}_4_reload_restored.png")
        r["browser_methods"] = sorted({q["method"] for q in reqs})
        r["browser_hot_requests_total"] = sum("hot-coins" in q["url"] for q in reqs)
        r["browser_requests_total"] = len(reqs)
        report[width] = r
        ctx.close()
        if i < 2:
            time.sleep(11)  # past the server's 10 s floor so each width does a real scan
    browser.close()
json.dump(report, open(f"{S}/report.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(report, ensure_ascii=False, indent=1))

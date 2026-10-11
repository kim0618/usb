"""Stage-by-stage RSS/PSS of the 31b6f2d btc_context_ui_v1 process shape. Usage: stages.py <backend_dir> <idle_s> <viewer:0|1>"""
import sys, time, json, gc
sys.path.insert(0, sys.argv[1])
IDLE, VIEWER = int(sys.argv[2]), sys.argv[3] == "1"
def mem():
    rss = int(open('/proc/self/status').read().split('VmRSS:')[1].split()[0])
    pss = None
    try:
        for line in open('/proc/self/smaps_rollup'):
            if line.startswith('Pss:'): pss = int(line.split()[1])
    except OSError: pass
    return round(rss/1024, 1), (round(pss/1024, 1) if pss else None)
out = []
def mark(name, **kw):
    r, p = mem(); out.append({"stage": name, "rss_mb": r, "pss_mb": p, **kw}); print(json.dumps(out[-1]), flush=True)
mark("0_python")
import fastapi, uvicorn, starlette, pydantic  # noqa
mark("A_fastapi_uvicorn")
from app.crypto.btc_context_ui_v1 import view  # noqa
from app.crypto.context_collector_v1 import api as CTX  # noqa
mark("B_ui_view_and_collector_api")
from app.crypto.market_context_v1 import venue, derivatives  # noqa
mark("C_mc_venue_derivatives")
from app.crypto.market_context_v1 import structure as STR  # noqa
mark("D_price_structure_imports")
from app.crypto.btc_context_ui_v1 import api as A
mark("D2_api_module")
now = int(time.time()*1000)
s = A.Sources()
t = time.monotonic(); s.series.extend(now); fetch_s = time.monotonic()-t
mark("E_30d_series_loaded", rows=len(s.series.rows), fetch_s=round(fetch_s, 1))
t = time.monotonic(); s.refresh(int(time.time()*1000)); run_s = time.monotonic()-t
lay = s.structure_layer(int(time.time()*1000))
mark("F_after_run_and_derivatives", run_s=round(run_s, 2), state=lay["state"], res=lay["levels"]["resistance"]["distance_pct"] if lay.get("levels") else None)
peak = 0
end = time.time() + IDLE
while time.time() < end:
    time.sleep(5)
    if VIEWER:
        s.refresh(int(time.time()*1000))
    r, _ = mem(); peak = max(peak, r)
gc.collect()
mark("G_after_idle", idle_s=IDLE, viewer=VIEWER, peak_rss_mb=peak, rows=len(s.series.rows))
hwm = int(open('/proc/self/status').read().split('VmHWM:')[1].split()[0])
mark("HWM", vmhwm_mb=round(hwm/1024, 1))

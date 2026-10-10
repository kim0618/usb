"""Build the isolated preview: Price, Wall and Flow on one screen for the journal window.

Static files only, written into this study's own output directory, served by
``python3 -m http.server``. It reads the journal and the frozen kline series and writes JSON plus
one HTML page. It starts no server, touches no production module and holds no credential.

    PYTHONPATH=backend .venv/bin/python -m app.crypto.research.market_context_r0.preview_build
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from . import state
from .layers import WALL_PROX_BPS_HI, build_minute_frame, build_second_frame

OUT = Path(__file__).resolve().parents[5] / "data/research/crypto/market_context_r0/preview"
BUCKET_MS = 2_000
WALL_BAND_BPS = 20.0
MAX_WALLS_PER_SIDE = 3
MINUTE_MS = 60_000


def _structure_rows(first_ms: int, last_ms: int) -> list[dict]:
    mf = build_minute_frame()
    out = []
    for r in mf.rows:
        if not (first_ms - 2 * MINUTE_MS <= r["info_ts"] <= last_ms + MINUTE_MS):
            continue
        out.append({"info_ts": r["info_ts"], "close": r["close"],
                    "res_price": r["res_price"], "res_id": r["res_id"],
                    "res_lifecycle": r["res_lifecycle"], "res_strength": r["res_strength"],
                    "sup_price": r["sup_price"], "sup_id": r["sup_id"],
                    "sup_lifecycle": r["sup_lifecycle"], "sup_strength": r["sup_strength"],
                    "published_prices": r["published_prices"],
                    "trend_structure": r["trend_structure"], "range_state": r["range_state"],
                    "retest_state": r["retest_state"],
                    "prev_session_high": r["prev_session_high"],
                    "prev_session_low": r["prev_session_low"],
                    "flow_imb_3m": r["flow_imb_3m"], "atr_5m": r["atr_5m"]})
    return out


def build() -> dict:
    sf = build_second_frame()
    first_ms, last_ms = int(sf.ms[0]), int(sf.ms[-1])
    buckets: list[dict] = []
    bucket_of = (sf.ms // BUCKET_MS)
    seen: set[int] = set()
    for i, b in enumerate(bucket_of):
        if int(b) in seen:
            continue
        seen.add(int(b))
        s = sf.samples[i]
        if s.mid is None:
            continue
        lab = state.label(sf, i)
        row = {"ms": int(sf.ms[i]), "mid": s.mid, "state": lab["state"], "fires": lab["fires"],
               "ok": bool(s.ok), "flow60": s.flow60_imb if s.flow60_complete else None,
               "flow60_complete": bool(s.flow60_complete),
               "band_bid": s.band_bid, "band_ask": s.band_ask, "walls": []}
        # Inside 10 bp the book is crowded: a mark per qualifying level buries the price line and
        # says less than the two numbers that matter. So each side contributes the span its near
        # walls occupy, drawn as a band, plus its three biggest walls, drawn as marks.
        for side, bins in (("ASK", s.ask_bins), ("BID", s.bid_bins)):
            inband = [w for w in bins.values()
                      if abs(float(w["distance_bps"])) <= WALL_BAND_BPS]
            near = [w for w in inband if abs(float(w["distance_bps"])) <= WALL_PROX_BPS_HI]
            tag = "a" if side == "ASK" else "b"
            if near:
                prices = [float(w["price"]) for w in near]
                row[tag + "_lo"] = min(prices)
                row[tag + "_hi"] = max(prices)
                row[tag + "_n"] = len(near)
                row[tag + "_k"] = round(sum(float(w["notional_usdt"]) for w in near) / 1000.0, 1)
            for w in sorted(inband, key=lambda w: -float(w["notional_usdt"]))[:MAX_WALLS_PER_SIDE]:
                row["walls"].append({"s": 1 if side == "ASK" else -1,
                                     "p": float(w["price"]),
                                     "n": round(float(w["notional_usdt"]) / 1000.0, 1),
                                     "d": round(abs(float(w["distance_bps"])), 2),
                                     "near": bool(abs(float(w["distance_bps"]))
                                                  <= WALL_PROX_BPS_HI)})
        buckets.append(row)
    payload = {
        "symbol": "BTCUSDT", "exchange": "binance_usdm",
        "window": {"first_ms": first_ms, "last_ms": last_ms,
                   "span_s": round((last_ms - first_ms) / 1000, 1)},
        "bucket_ms": BUCKET_MS, "wall_band_bps": WALL_BAND_BPS,
        "wall_near_bps": WALL_PROX_BPS_HI,
        "notes": {"state_is_backward": state.BACKWARD_NOTE,
                  "state_has_no_direction": state.NO_DIRECTION_NOTE,
                  "bands": ("only the +-0.1 percent band is COMPLETE; wider bands are permanent "
                            "lower bounds and are not drawn"),
                  "walls": ("lm-wall.v2 selection, bin width 5.0 USDT, notional is the bin's "
                            "leading member in thousands of USDT"),
                  "consumption": ("a wall that leaves the screen may have been cancelled rather "
                                  "than eaten: order identity is not provable under btc-ms.v0.1")},
        "buckets": buckets,
        "structure": _structure_rows(first_ms, last_ms),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "preview_data.json").write_text(json.dumps(payload, separators=(",", ":")) + "\n")
    (OUT / "index.html").write_text(PAGE)
    size = (OUT / "preview_data.json").stat().st_size
    return {"buckets": len(buckets), "structure_minutes": len(payload["structure"]),
            "json_bytes": size, "path": str(OUT)}


PAGE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:,">
<title>Market Context R0 Preview</title>
<style>
  :root {
    --bg: #0e1116; --panel: #161b22; --line: #2b313a; --text: #e6edf3; --muted: #8b949e;
    --ask: #f0883e; --bid: #3fb950; --price: #58a6ff; --level: #a371f7; --warn: #d29922;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text);
         font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }
  header { padding: 14px 16px; border-bottom: 1px solid var(--line); }
  h1 { font-size: 15px; margin: 0 0 4px; font-weight: 600; }
  .sub { color: var(--muted); font-size: 12px; }
  main { padding: 16px; display: grid; gap: 14px; }
  main > * { min-width: 0; }
  .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
           padding: 12px; }
  .panel h2 { font-size: 12px; margin: 0 0 8px; color: var(--muted); font-weight: 600;
              letter-spacing: .04em; text-transform: uppercase; }
  canvas { width: 100%; display: block; }
  .legend { display: flex; flex-wrap: wrap; gap: 10px 14px; color: var(--muted); font-size: 12px;
            margin-top: 8px; }
  .legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px;
              margin-right: 5px; vertical-align: -1px; }
  .scroll { overflow-x: auto; }
  table { border-collapse: collapse; width: 100%; font-size: 12px; }
  th, td { text-align: left; padding: 4px 8px 4px 0; border-bottom: 1px solid var(--line);
           white-space: nowrap; }
  th { color: var(--muted); font-weight: 600; }
  .note { color: var(--muted); font-size: 12px; margin: 6px 0 0; }
  .warn { color: var(--warn); }
  #readout { font-size: 12px; color: var(--muted); min-height: 1.5em;
              overflow-wrap: anywhere; }
</style>
</head>
<body>
<header>
  <h1>Market Context R0 Preview &mdash; Price / Wall / Flow</h1>
  <div class="sub" id="head">loading</div>
</header>
<main>
  <div class="panel">
    <h2>Price, walls and R1 structure</h2>
    <canvas id="main" height="440"></canvas>
    <div id="readout"></div>
    <div class="legend">
      <span><i style="background:var(--price)"></i>mid, 1 s</span>
      <span><i style="background:var(--ask)"></i>ask wall</span>
      <span><i style="background:var(--bid)"></i>bid wall</span>
      <span><i style="background:var(--level)"></i>R1 published level</span>
      <span><i style="background:rgba(240,136,62,.35)"></i>near wall band, inside 10 bp</span>
      <span>mark size = notional, faded = beyond 10 bp</span>
    </div>
  </div>
  <div class="panel">
    <h2>Trade flow, 60 s imbalance</h2>
    <canvas id="flow" height="130"></canvas>
    <p class="note">A gap means the 60 s window was not COMPLETE. An unobserved window is not a
      zero.</p>
  </div>
  <div class="panel">
    <h2>State label, contract section 15</h2>
    <canvas id="state" height="56"></canvas>
    <div class="scroll"><table id="stats"></table></div>
    <p class="note warn" id="caveat"></p>
  </div>
</main>
<script>
const COL = {ASK: '#f0883e', BID: '#3fb950', PRICE: '#58a6ff', LEVEL: '#a371f7',
             LONG_LEANING: '#3fb950', SHORT_LEANING: '#f0883e', CONFLICT: '#d29922',
             NEUTRAL: '#30363d', UNKNOWN: '#484f58'};
let D = null;

function iso(ms) { return new Date(ms).toISOString().slice(11, 19) + 'Z'; }

function fit(c) {
  const r = window.devicePixelRatio || 1;
  const w = c.clientWidth;
  c.width = Math.floor(w * r);
  c.height = Math.floor(c.getAttribute('height') * r);
  const x = c.getContext('2d');
  x.setTransform(r, 0, 0, r, 0, 0);
  return {x, w, h: +c.getAttribute('height')};
}

function priceRange() {
  let lo = Infinity, hi = -Infinity;
  for (const b of D.buckets) { if (b.mid < lo) lo = b.mid; if (b.mid > hi) hi = b.mid; }
  for (const s of D.structure) {
    for (const p of s.published_prices) {
      if (p > lo - 400 && p < hi + 400) { if (p < lo) lo = p; if (p > hi) hi = p; }
    }
  }
  const pad = (hi - lo) * 0.06;
  return [lo - pad, hi + pad];
}

function drawMain() {
  const c = document.getElementById('main');
  const {x, w, h} = fit(c);
  const L = 62, R = 10, T = 8, B = 22;
  const t0 = D.window.first_ms, t1 = D.window.last_ms;
  const [lo, hi] = priceRange();
  const px = ms => L + (ms - t0) / (t1 - t0) * (w - L - R);
  const py = p => T + (hi - p) / (hi - lo) * (h - T - B);
  x.clearRect(0, 0, w, h);
  x.strokeStyle = '#2b313a'; x.lineWidth = 1; x.fillStyle = '#8b949e'; x.font = '11px monospace';
  for (let k = 0; k <= 5; k++) {
    const p = lo + (hi - lo) * k / 5, y = Math.round(py(p)) + 0.5;
    x.beginPath(); x.moveTo(L, y); x.lineTo(w - R, y); x.stroke();
    x.fillText(p.toFixed(0), 6, y + 3);
  }
  // Tick density follows the width: at 390 px seven labels overlap into a smear.
  const ticks = w > 980 ? 6 : w > 640 ? 4 : 2;
  for (let k = 0; k <= ticks; k++) {
    const ms = t0 + (t1 - t0) * k / ticks;
    const label = iso(ms);
    const tx = Math.min(Math.max(px(ms) - 24, L), w - R - 52);
    x.fillText(label, tx, h - 6);
  }
  // R1 levels: one horizontal segment per minute, so a level that dies stops being drawn.
  x.lineWidth = 2;
  for (let i = 0; i < D.structure.length; i++) {
    const s = D.structure[i];
    const nxt = D.structure[i + 1] ? D.structure[i + 1].info_ts : s.info_ts + 60000;
    for (const p of s.published_prices) {
      if (p < lo || p > hi) continue;
      x.strokeStyle = 'rgba(163,113,247,0.55)';
      x.beginPath(); x.moveTo(px(s.info_ts), py(p)); x.lineTo(px(nxt), py(p)); x.stroke();
    }
  }
  // the near wall band, one translucent column per bucket per side
  const bw = Math.max(1, (w - L - R) / D.buckets.length);
  for (const b of D.buckets) {
    if (b.a_lo !== undefined) {
      x.fillStyle = 'rgba(240,136,62,0.16)';
      x.fillRect(px(b.ms), py(b.a_hi), Math.max(bw, 1), Math.max(py(b.a_lo) - py(b.a_hi), 1));
    }
    if (b.b_lo !== undefined) {
      x.fillStyle = 'rgba(63,185,80,0.16)';
      x.fillRect(px(b.ms), py(b.b_hi), Math.max(bw, 1), Math.max(py(b.b_lo) - py(b.b_hi), 1));
    }
  }
  // the three biggest walls each side, sized by notional
  for (const b of D.buckets) {
    for (const wl of b.walls) {
      if (wl.p < lo || wl.p > hi) continue;
      const a = wl.near ? 0.85 : 0.3;
      x.fillStyle = (wl.s === 1 ? 'rgba(240,136,62,' : 'rgba(63,185,80,') + a + ')';
      const hgt = Math.min(5, 1.2 + Math.log10(Math.max(wl.n, 1)) * 1.6);
      x.fillRect(px(b.ms), py(wl.p) - hgt / 2, Math.max(bw, 1.2), hgt);
    }
  }
  // mid
  x.strokeStyle = 'rgba(14,17,22,0.9)'; x.lineWidth = 3.4; x.beginPath();
  D.buckets.forEach((b, i) => { const X = px(b.ms), Y = py(b.mid);
    i ? x.lineTo(X, Y) : x.moveTo(X, Y); });
  x.stroke();
  x.strokeStyle = COL.PRICE; x.lineWidth = 1.5; x.beginPath();
  D.buckets.forEach((b, i) => { const X = px(b.ms), Y = py(b.mid);
    i ? x.lineTo(X, Y) : x.moveTo(X, Y); });
  x.stroke();
  c.onmousemove = ev => {
    const rect = c.getBoundingClientRect();
    const frac = (ev.clientX - rect.left - L) / (rect.width - L - R);
    const ms = t0 + frac * (t1 - t0);
    let best = D.buckets[0], bd = Infinity;
    for (const b of D.buckets) { const d = Math.abs(b.ms - ms); if (d < bd) { bd = d; best = b; } }
    const near = best.walls.filter(v => v.near);
    const spread = (best.a_n || 0) + '/' + (best.b_n || 0);
    document.getElementById('readout').textContent =
      iso(best.ms) + '  mid ' + best.mid.toFixed(1) + '  state ' + best.state +
      '  flow60 ' + (best.flow60 === null ? 'UNKNOWN' : best.flow60.toFixed(3)) +
      '  near walls ask/bid ' + spread +
      (near.length ? '  nearest ' + near[0].p.toFixed(1) + ' (' + near[0].n + 'k, ' +
        near[0].d + 'bp)' : '');
  };
}

function drawFlow() {
  const c = document.getElementById('flow');
  const {x, w, h} = fit(c);
  const L = 62, R = 10;
  const t0 = D.window.first_ms, t1 = D.window.last_ms;
  const px = ms => L + (ms - t0) / (t1 - t0) * (w - L - R);
  let m = 0.001;
  for (const b of D.buckets) if (b.flow60 !== null) m = Math.max(m, Math.abs(b.flow60));
  const mid = h / 2;
  x.clearRect(0, 0, w, h);
  x.strokeStyle = '#2b313a'; x.beginPath();
  x.moveTo(L, mid + 0.5); x.lineTo(w - R, mid + 0.5); x.stroke();
  x.fillStyle = '#8b949e'; x.font = '11px monospace';
  x.fillText('+' + m.toFixed(2), 6, 12); x.fillText('-' + m.toFixed(2), 6, h - 4);
  const bw = Math.max(1, (w - L - R) / D.buckets.length);
  for (const b of D.buckets) {
    if (b.flow60 === null) continue;
    const v = b.flow60 / m * (mid - 6);
    x.fillStyle = b.flow60 >= 0 ? 'rgba(63,185,80,0.8)' : 'rgba(240,136,62,0.8)';
    x.fillRect(px(b.ms), v >= 0 ? mid - v : mid, Math.max(bw, 1), Math.abs(v));
  }
}

function drawState() {
  const c = document.getElementById('state');
  const {x, w, h} = fit(c);
  const L = 62, R = 10;
  const t0 = D.window.first_ms, t1 = D.window.last_ms;
  const px = ms => L + (ms - t0) / (t1 - t0) * (w - L - R);
  const bw = Math.max(1, (w - L - R) / D.buckets.length);
  x.clearRect(0, 0, w, h);
  for (const b of D.buckets) {
    x.fillStyle = COL[b.state] || '#30363d';
    x.fillRect(px(b.ms), 8, Math.max(bw, 1), h - 24);
  }
  x.fillStyle = '#8b949e'; x.font = '11px monospace';
  x.fillText('state', 6, h / 2 + 3);
}

function stats() {
  const by = {};
  let unknownFlow = 0, nearAsk = 0, nearBid = 0, withAsk = 0, withBid = 0;
  for (const b of D.buckets) {
    by[b.state] = (by[b.state] || 0) + 1;
    if (b.flow60 === null) unknownFlow++;
    if (b.a_n) { nearAsk += b.a_n; withAsk++; }
    if (b.b_n) { nearBid += b.b_n; withBid++; }
  }
  const n = D.buckets.length;
  const rows = Object.entries(by).sort((a, b) => b[1] - a[1]).map(
    ([k, v]) => `<tr><td style="color:${COL[k] || '#8b949e'}">${k}</td><td>${v}</td>` +
                `<td>${(v / n * 100).toFixed(1)}%</td></tr>`).join('');
  document.getElementById('stats').innerHTML =
    '<tr><th>state</th><th>buckets</th><th>share</th></tr>' + rows +
    `<tr><td>flow 60 s UNKNOWN</td><td>${unknownFlow}</td>` +
    `<td>${(unknownFlow / n * 100).toFixed(1)}%</td></tr>` +
    `<tr><td>buckets with a wall inside 10 bp</td><td>${withAsk} ask / ${withBid} bid</td>` +
    `<td>${(withAsk / n * 100).toFixed(1)}% / ${(withBid / n * 100).toFixed(1)}%</td></tr>` +
    `<tr><td>mean walls inside 10 bp</td>` +
    `<td>${(nearAsk / Math.max(withAsk, 1)).toFixed(1)} ask / ` +
    `${(nearBid / Math.max(withBid, 1)).toFixed(1)} bid</td><td>per bucket that has one</td></tr>`;
  document.getElementById('caveat').textContent =
    D.notes.state_has_no_direction + '. ' + D.notes.state_is_backward + '.';
}

fetch('preview_data.json').then(r => r.json()).then(d => {
  D = d;
  document.getElementById('head').textContent =
    d.exchange + ' ' + d.symbol + '  ' + iso(d.window.first_ms) + ' to ' +
    iso(d.window.last_ms) + '  (' + (d.window.span_s / 3600).toFixed(2) + ' h, ' +
    d.buckets.length + ' buckets of ' + d.bucket_ms / 1000 + ' s)  |  ' + d.notes.walls;
  drawMain(); drawFlow(); drawState(); stats();
  window.addEventListener('resize', () => { drawMain(); drawFlow(); drawState(); });
});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    print(json.dumps(build(), indent=1))

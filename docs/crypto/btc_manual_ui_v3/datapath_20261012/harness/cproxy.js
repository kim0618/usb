// Caching GET-only QA proxy. node cproxy.js LISTEN BTC_API_PORT TALLY MODE [ENTRY QTY]
// MODE: REAL | FLAT | SHORT | LONG (the last three rewrite the PAPER BTC position, display only).
// /api/crypto/binance/* is refused outright (503): no signed Binance read can be caused from here.
// Every other production GET is fetched at most once per URL (shared disk cache), with a hard cap.
const http = require('http'); const fs = require('fs'); const crypto = require('crypto'); const path = require('path');
const [LISTEN, BTC, TALLY, MODE, ENTRY, QTY] = process.argv.slice(2);
const CACHE = path.join(__dirname, 'cache'); const CAP = 150;
const capFile = path.join(CACHE, '_upstream_count');
const tally = { get: 0, non_get: [], btc: 0, crypto_cached: 0, crypto_upstream: 0, binance_refused: 0, upgrades: 0 };
const save = () => fs.writeFileSync(TALLY, JSON.stringify(tally));
const upstreamCount = () => { try { return Number(fs.readFileSync(capFile, 'utf8')) || 0; } catch { return 0; } };
function rewrite(body) {
  if (MODE === 'REAL') return body;
  try {
    const obj = JSON.parse(body); const a = obj && obj.account;
    if (!a || !('position_side' in a)) return body;
    if (MODE === 'FLAT') Object.assign(a, { position_side: null, avg_entry: null, position_qty: '0', position_signed_qty: '0', liquidation_price: null });
    else Object.assign(a, { position_side: MODE, avg_entry: ENTRY, position_qty: QTY,
      position_signed_qty: String((MODE === 'LONG' ? 1 : -1) * Number(QTY)), liquidation_price: null });
    return JSON.stringify(obj);
  } catch { return body; }
}
const srv = http.createServer((req, res) => {
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    tally.non_get.push({ method: req.method, url: req.url }); save(); res.writeHead(405); return res.end('QA_REFUSES_WRITES');
  }
  tally.get++;
  if (req.url.startsWith('/btc-context-api/')) {
    tally.btc++; save();
    const up = http.request({ host: '127.0.0.1', port: Number(BTC), path: req.url.slice('/btc-context-api'.length), method: 'GET' },
      r => { res.writeHead(r.statusCode, r.headers); r.pipe(res); });
    up.on('error', () => { res.writeHead(502); res.end(); }); return up.end();
  }
  if (!req.url.startsWith('/crypto-api/')) {
    save();
    const up = http.request({ host: '127.0.0.1', port: Number(process.env.NEXT_PORT || 4101), path: req.url, method: req.method, headers: req.headers },
      r => { res.writeHead(r.statusCode, r.headers); r.pipe(res); });
    up.on('error', () => { res.writeHead(502); res.end(); }); return req.pipe(up);
  }
  const p = req.url.slice('/crypto-api'.length);
  if (p.startsWith('/api/crypto/binance')) { tally.binance_refused++; save(); res.writeHead(503); return res.end('{"error":{"code":"QA_NO_BINANCE"}}'); }
  const key = path.join(CACHE, crypto.createHash('sha1').update(p).digest('hex') + '.json');
  const serve = (status, body) => {
    const isState = /\/api\/crypto\/state\?/.test(p) && /symbol=BTCUSDT/.test(p);
    const out = isState ? rewrite(body) : body;
    res.writeHead(status, { 'content-type': 'application/json' }); res.end(out);
  };
  if (fs.existsSync(key)) { tally.crypto_cached++; save(); const c = JSON.parse(fs.readFileSync(key, 'utf8')); return serve(c.status, c.body); }
  if (upstreamCount() >= CAP) { save(); res.writeHead(503); return res.end('{"error":{"code":"QA_CAP"}}'); }
  fs.writeFileSync(capFile, String(upstreamCount() + 1)); tally.crypto_upstream++; save();
  const up = http.request({ host: '127.0.0.1', port: Number(process.env.TUNNEL_PORT || 18101), path: p, method: 'GET', headers: { accept: 'application/json' } }, r => {
    const chunks = []; r.on('data', c => chunks.push(c)); r.on('end', () => {
      const body = Buffer.concat(chunks).toString();
      if (r.statusCode === 200) fs.writeFileSync(key, JSON.stringify({ status: 200, url: p, body }));
      serve(r.statusCode, body);
    });
  });
  up.on('error', () => { res.writeHead(502); res.end(); }); up.end();
});
srv.on('upgrade', (req, sock) => { tally.upgrades++; save(); sock.destroy(); });
save(); srv.listen(Number(LISTEN), '127.0.0.1');

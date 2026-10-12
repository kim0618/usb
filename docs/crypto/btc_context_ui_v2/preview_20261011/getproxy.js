// GET-only QA proxy: / -> local next (3101), /crypto-api/ -> tunnel 18100, /btc-context-api/ -> local V2 API 18015.
// Every non-GET/HEAD is refused here with 405 and logged; nothing but GET reaches production.
const http = require('http'); const fs = require('fs');
const tally = { get: 0, non_get: [], btc: 0, crypto: 0, upgrades: 0 };
const save = () => fs.writeFileSync(process.argv[2], JSON.stringify(tally));
function route(url) {
  if (url.startsWith('/crypto-api/')) return [18100, url.slice('/crypto-api'.length), 'crypto'];
  if (url.startsWith('/btc-context-api/')) return [18015, url.slice('/btc-context-api'.length), 'btc'];
  return [3101, url, null];
}
const srv = http.createServer((req, res) => {
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    tally.non_get.push({ method: req.method, url: req.url }); save();
    res.writeHead(405); return res.end('QA_REFUSES_WRITES');
  }
  const [port, path, kind] = route(req.url);
  tally.get++; if (kind) tally[kind]++; save();
  const up = http.request({ host: '127.0.0.1', port, path, method: req.method, headers: req.headers }, (r) => {
    res.writeHead(r.statusCode, r.headers); r.pipe(res);
  });
  up.on('error', () => { res.writeHead(502); res.end('bad gateway'); });
  req.pipe(up);
});
srv.on('upgrade', (req, sock) => { tally.upgrades++; tally.non_get.push({ method: 'UPGRADE', url: req.url }); save(); sock.destroy(); });
save(); srv.listen(3100, '127.0.0.1');

"""Manual Market Context V1 (`mc-display.v1`) LIQUIDITY and FLOW logic, vendored.

The Production Context Collector must publish exactly what the Market Context V1 panel would
publish for the same reading, and the panel's code is not in this branch: on 2026-10-10 the
`app.crypto.market_context_v1` package existed only as untracked files in another session's
working tree. Re-implementing CANCEL_LIKE, CONSUMED_CANDIDATE and ABSORPTION_CANDIDATE would be a
second definition of a frozen rule, so the three modules are copied instead, byte for byte except
for one removal, and the hashes below record what was copied.

* `flow.py` is identical to the source.
* `contract.py` differs only in the depth of two relative imports (`..liquidity_map` and
  `..market_structure_v0` became `...`), because this copy sits one package deeper.
* `liquidity.py` lost `read()`, `_read_async()` and their two imports (`asyncio` and the Liquidity
  Map FastAPI module). Those functions poll the viewer over its HTTP route; the collector holds the
  same reading in memory and passes it in, so the I/O half has nothing to do here. Every function
  that decides a value is unchanged.

**This copy is temporary by design.** Once `app.crypto.market_context_v1` is committed, delete this
package and import that one. `tests/crypto/test_ctx_v1_vendored_parity.py` compares this copy
against golden outputs produced by the source and, when the source package is importable, against
the source directly, so the two cannot drift silently while both exist.
"""

#: sha256 of the source files at the moment they were copied.
SOURCE_SHA256 = {
    "contract.py": "14849a1eb7b9579a8362984b8a6a55a6879b40159d81babca4dd503aeb805c22",
    "flow.py": "106979e09f21690fd36ad33ebdf195003b369130bf14223dbd18632ffb03d08e",
    "liquidity.py": "06dfc4c491d78f129850294bf6b3f3560b4485feff3d1b4a4269951186e07fda",
}
#: The frozen display contract these modules implement.
SOURCE_CONTRACT = "mc-display.v1"
SOURCE_CONTRACT_SHA256 = "25891d6267dcd9e4e0b0e719802e1f95c79a6aa6b1d99676fc637870e169a3b1"
SOURCE_PACKAGE = "app.crypto.market_context_v1"
COPIED_ON = "2026-10-10"

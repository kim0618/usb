"""PIT-1 poisoned tapes (PIT-2 lives in ``universe.poisoned_panel``; PIT-3 is ``features.leaky_minute_part``).

For session index d: every bar of D starting at or after 16:00 ET and every bar of a later session
is replaced by finite positive noise, and a synthetic bar is added at every empty minute of D in
the declared synthetic window. The regular bars of D and of earlier sessions are left untouched.
"""

import re
import zlib

import numpy as np

from app.backtest.strategy_f0_regular_after.config import F0HardFail, F0Rules
from app.backtest.strategy_f0_regular_after.params import Params
from app.backtest.strategy_f0_regular_after.tape import Tape


def synthetic_window(rules: F0Rules) -> tuple[int, int]:
    found = re.findall(r"empty minute in \[(\d+), (\d+)\)", rules.raw["pit_audits"]["PIT-1"]["action"])
    if len(found) != 1:
        raise F0HardFail("PIT-1 synthetic window not parseable")
    return int(found[0][0]), int(found[0][1])


def poisoned(tape: Tape, d: int, p: Params, window: tuple[int, int]) -> Tape:
    rng = np.random.default_rng([p.pit1_seed, d, zlib.crc32(tape.symbol.encode())])
    hit = ((tape.day == d) & (tape.minute >= p.reg_end)) | (tape.day > d)
    n = int(hit.sum())
    present = set(tape.minute[tape.day == d].tolist())
    fill = np.array([m for m in range(window[0], window[1]) if m not in present], dtype=np.int16)
    k = fill.size

    def noisy(a: np.ndarray, lo: float, hi: float) -> np.ndarray:
        out = a.copy()
        out[hit] = rng.uniform(lo, hi, n)
        return np.concatenate([out, rng.uniform(lo, hi, k)])

    return Tape(tape.symbol,
                np.concatenate([tape.day, np.full(k, d, dtype=np.int16)]),
                np.concatenate([tape.minute, fill]),
                noisy(tape.o, 1.0, 1000.0), noisy(tape.h, 1.0, 1000.0), noisy(tape.l, 1.0, 1000.0),
                noisy(tape.c, 1.0, 1000.0), noisy(tape.v, 1.0, 1e7), noisy(tape.vw, 1.0, 1000.0),
                tape.integrity, tape.read_set)

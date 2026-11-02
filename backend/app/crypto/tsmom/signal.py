"""V3 앙상블 비중 계산. 연구 스크립트 data/research/crypto/btc_tsmom_v1/run_tsmom_v3.py 의 sub_positions/weight 와 같은 규칙.

입력은 4시간봉 [(open_time_ms, close)] 오름차순. 봉 i의 비중 = 봉 i 동안 들고 있을 비중(직전 봉 마감까지의 결정).
하위 전략 (h, n): 매일 h시(UTC)에 마감하는 봉의 종가가 n일 전 같은 시각 종가보다 높으면 1, 아니면 0.
"""
from __future__ import annotations

from dataclasses import dataclass

H4_MS = 4 * 3600 * 1000
DAY_MS = 24 * 3600 * 1000
HOURS = (0, 4, 8, 12, 16, 20)
LOOKBACKS = (20, 24, 28, 32, 36, 40)


@dataclass(frozen=True)
class Weight:
    close_time_ms: int          # 이 비중을 정한 봉의 마감 시각(= 다음 봉 시작)
    weight: float               # 0 ~ 1
    longs: int                  # 36개 중 롱 개수
    known: int                  # 결정이 난 하위 전략 수(36이면 완전)


def sub_decisions(bars: list[tuple[int, float]]) -> dict[tuple[int, int], list[int | None]]:
    """하위 전략별, 봉 i 마감 직후의 결정(다음 봉부터 적용). 결정이 없으면 직전 값 유지, 처음엔 None."""
    close_at = {t + H4_MS: c for t, c in bars}
    out = {}
    for h in HOURS:
        for n in LOOKBACKS:
            cur, seq = None, []
            for t, c in bars:
                end = t + H4_MS
                if (end // 3_600_000) % 24 == h:
                    past = close_at.get(end - n * DAY_MS)
                    cur = None if past is None else (1 if c / past - 1 > 0 else 0)
                seq.append(cur)
            out[(h, n)] = seq
    return out


def weights(bars: list[tuple[int, float]]) -> list[Weight | None]:
    """봉 i 마감 직후 정해지는 비중(다음 봉에 적용). 36개가 모두 결정되기 전에는 None."""
    subs = sub_decisions(bars)
    res = []
    for i, (t, _) in enumerate(bars):
        vals = [subs[k][i] for k in subs]
        known = sum(v is not None for v in vals)
        if known < len(vals):
            res.append(None)
            continue
        longs = sum(vals)
        res.append(Weight(t + H4_MS, longs / len(vals), longs, known))
    return res

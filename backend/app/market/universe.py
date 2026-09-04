"""Kiwoom acquisition pre-filter; it does not replace Scanner eligibility."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.integrations.kiwoom.mapping import canonical_exchange, map_metadata
from app.market.kiwoom import KiwoomMarketDataProvider
from app.market.reference import SymbolMetadata
from app.market.symbols import normalize_symbol


@dataclass(frozen=True)
class UniverseCandidate:
    symbol: str
    exchange_code: str
    company_name: str | None
    market_cap: float | None


class KiwoomUniverseSource:
    """Build a bounded liquid universe from official US ranking TRs."""

    def __init__(self, provider: KiwoomMarketDataProvider) -> None:
        self.provider = provider

    def acquire(self, limit: int) -> tuple[UniverseCandidate, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("universe limit must be between 1 and 100")
        liquid = self.provider.client.transaction_amount_ranking()
        caps = self.provider.client.market_cap_ranking()
        cap_by_symbol = self._rows_by_symbol(caps)
        result: list[UniverseCandidate] = []
        seen: set[str] = set()
        for row in liquid:
            raw_symbol = row.get("stk_cd")
            if not raw_symbol:
                continue
            symbol = normalize_symbol(str(raw_symbol))
            if symbol == "SPY" or symbol in seen:
                continue
            seen.add(symbol)
            cap_row = cap_by_symbol.get(symbol, {})
            raw_cap = cap_row.get("mac")
            exchange_code = self._exchange_code(row.get("stex_tp") or cap_row.get("stex_tp"))
            result.append(UniverseCandidate(
                symbol=symbol,
                exchange_code=exchange_code,
                company_name=row.get("stk_enm") or row.get("stk_nm") or cap_row.get("stk_enm") or cap_row.get("stk_nm"),
                market_cap=self._optional_number(raw_cap),
            ))
            if len(result) == limit:
                break
        return tuple(result)

    def prime_provider(self, candidates: tuple[UniverseCandidate, ...], received_at: datetime) -> None:
        for item in candidates:
            self.provider._exchanges[item.symbol] = item.exchange_code
            payload: dict[str, Any] = {
                "stex_tp": item.exchange_code,
                "stk_cd": item.symbol,
                "stk_enm": item.company_name,
                "mac": item.market_cap,
                "trd_susp_tp": "N",
            }
            metadata = map_metadata(item.symbol, payload, received_at)
            self.provider._metadata_cache[(item.symbol, item.exchange_code)] = metadata

    @staticmethod
    def _rows_by_symbol(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {normalize_symbol(str(row["stk_cd"])): row for row in rows if row.get("stk_cd")}

    @staticmethod
    def _optional_number(value: object) -> float | None:
        if value in (None, ""):
            return None
        return abs(float(str(value).replace(",", "")))

    @staticmethod
    def _exchange_code(value: object) -> str:
        raw = str(value).strip().upper()
        if raw in {"NA", "ND", "NY"}:
            return raw
        numeric = {"1": "NY", "2": "ND", "3": "NA"}.get(raw)
        if numeric:
            return numeric
        canonical = canonical_exchange(raw)
        return {"NYSE": "NY", "NASDAQ": "ND", "AMEX": "NA"}.get(canonical, "ND")

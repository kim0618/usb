from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.backtest.strategy_h0.facts import CanonicalFact

TAGS = {
    "revenue": "Revenues",
    "operating_income": "OperatingIncomeLoss",
    "eps_diluted": "EarningsPerShareDiluted",
    "operating_cash_flow": "NetCashProvidedByUsedInOperatingActivities",
    "capex": "PaymentsToAcquirePropertyPlantAndEquipment",
    "cash": "CashAndCashEquivalentsAtCarryingValue",
    "total_debt": "LongTermDebt",
    "assets": "Assets",
    "equity": "StockholdersEquity",
    "shares_outstanding": "EntityCommonStockSharesOutstanding",
    "net_income": "NetIncomeLoss",
}


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def mkfact(
    field: str,
    value: float,
    end: date,
    *,
    start: date | None = None,
    form: str = "10-Q",
    fiscal_period: str | None = "Q1",
    accession: str = "ACC-0000000000",
    accepted_at: datetime | None = None,
    unit: str = "USD",
) -> CanonicalFact:
    return CanonicalFact(
        field=field,
        taxonomy="us-gaap",
        tag=TAGS[field],
        unit=unit,
        value=value,
        start=start,
        end=end,
        filed=end,
        accepted_at=accepted_at or datetime.combine(end + timedelta(days=25), datetime.min.time(), tzinfo=timezone.utc),
        accession=accession,
        form=form,
        fiscal_year=end.year,
        fiscal_period=fiscal_period,
        frame=None,
    )

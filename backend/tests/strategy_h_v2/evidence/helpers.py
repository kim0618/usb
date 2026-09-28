from __future__ import annotations

from datetime import datetime, timezone


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def mkrow(
    form: str, accession: str, filing_date: str, accepted: str | None, *,
    items: str = "", primary_document: str = "doc.htm",
) -> dict:
    return {
        "accessionNumber": accession, "form": form, "filingDate": filing_date,
        "acceptanceDateTime": accepted, "items": items, "primaryDocument": primary_document,
    }

"""Canonical validation for US market symbols used at domain and file boundaries."""

import re


MAX_SYMBOL_LENGTH = 32
_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.-]*$")


def normalize_symbol(value: str) -> str:
    symbol = value.strip().upper()
    if not symbol:
        raise ValueError("symbol must not be empty")
    if len(symbol) > MAX_SYMBOL_LENGTH:
        raise ValueError(f"symbol must be at most {MAX_SYMBOL_LENGTH} characters")
    if not _SYMBOL_PATTERN.fullmatch(symbol):
        raise ValueError("symbol may contain only A-Z, 0-9, dot, and dash")
    return symbol

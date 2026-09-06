"""Semantic schema contract shared by isolated tests and adoption tooling."""

import re

from sqlalchemy import Connection, Engine, inspect


def _sql_tokens(value: str) -> list[str]:
    # Preserve quoted text verbatim; whitespace and case inside literals matter.
    return re.findall(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|[a-zA-Z_][a-zA-Z_0-9]*|[0-9]+|[^\s]", value)


def _outer_parentheses(value: str) -> bool:
    tokens = _sql_tokens(value)
    if not tokens or tokens[0] != '(' or tokens[-1] != ')':
        return False
    depth = 0
    for index, token in enumerate(tokens):
        depth += (token == '(') - (token == ')')
        if depth == 0 and index != len(tokens) - 1:
            return False
    return depth == 0


def _normalize_default(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    while _outer_parentheses(normalized):
        normalized = normalized[1:-1].strip()
    if normalized.casefold() == "false":
        return "0"
    if normalized in {"'0'", '"0"'}:
        return "0"
    if normalized in {"'{}'", '"{}"'}:
        return "{}"
    return normalized


def _normalize_sql(value: str) -> str:
    return ' '.join(token if token.startswith(("'", '"')) else token.casefold()
                    for token in _sql_tokens(value))


def schema_fingerprint(engine: Engine | Connection) -> dict[str, object]:
    inspector = inspect(engine)
    tables = sorted(name for name in inspector.get_table_names() if name != "alembic_version")
    return {
        "tables": tables,
        "columns": {
            table: {
                column["name"]: (
                    str(column["type"]).casefold(),
                    column["nullable"],
                    _normalize_default(column["default"]),
                    bool(column.get("primary_key")),
                )
                for column in inspector.get_columns(table)
            }
            for table in tables
        },
        "primary_keys": {
            table: tuple(inspector.get_pk_constraint(table).get("constrained_columns") or ())
            for table in tables
        },
        "foreign_keys": {
            table: sorted(
                (
                    tuple(foreign_key["constrained_columns"]),
                    foreign_key["referred_table"],
                    tuple(foreign_key["referred_columns"]),
                    tuple(sorted((foreign_key.get("options") or {}).items())),
                )
                for foreign_key in inspector.get_foreign_keys(table)
            )
            for table in tables
        },
        "unique_constraints": {
            table: sorted(
                tuple(constraint["column_names"])
                for constraint in inspector.get_unique_constraints(table)
            )
            for table in tables
        },
        "indexes": {
            table: sorted(
                (index["name"], tuple(index["column_names"]), index["unique"])
                for index in inspector.get_indexes(table)
            )
            for table in tables
        },
        "check_constraints": {
            table: sorted(
                (constraint["name"], _normalize_sql(constraint["sqltext"]))
                for constraint in inspector.get_check_constraints(table)
            )
            for table in tables
        },
    }

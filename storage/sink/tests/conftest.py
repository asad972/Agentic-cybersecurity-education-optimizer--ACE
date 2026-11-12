"""Shared test helpers: messages are checked against their topic schema, rows against schema.cql."""

import re
import uuid
from datetime import date, datetime
from pathlib import Path

import pytest

from ace import contracts
from ace_sink import rows as sink_rows

SCHEMA_CQL = Path(__file__).resolve().parents[2] / "cql" / "schema.cql"

# Python types the Cassandra driver takes for each CQL type in schema.cql.
CQL_TYPES = {
    "text": str,
    "int": int,
    "bigint": int,
    "double": float,
    "boolean": bool,
    "date": date,
    "timestamp": datetime,
    "uuid": uuid.UUID,
    "set<text>": set,
    "list<text>": list,
}


def parse_tables(text):
    """table -> (column -> CQL type, primary-key columns) for every CREATE TABLE in text.

    The key is either a table-level PRIMARY KEY (...) line or one column
    declared inline as `name type PRIMARY KEY`.
    """
    tables = {}
    for name, body in re.findall(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\)", text, re.S):
        columns = dict(re.findall(r"^\s+(\w+)\s+([\w<>]+)", body, re.M))
        columns.pop("PRIMARY", None)
        table_key = re.search(r"PRIMARY KEY \((.*)\)", body)
        if table_key:
            key = re.findall(r"\w+", table_key.group(1))
        else:
            key = re.findall(r"^\s+(\w+)\s+[\w<>]+\s+PRIMARY KEY", body, re.M)
        if not key:
            raise ValueError(f"{name}: no primary key in schema.cql")
        tables[name] = columns, key
    return tables


TABLES = parse_tables(SCHEMA_CQL.read_text(encoding="utf-8"))

INT_RANGES = {"int": 2**31, "bigint": 2**63}


@pytest.fixture
def parse_cql():
    """The schema.cql reader, so its own tests can feed it small tables."""
    return parse_tables


@pytest.fixture
def schema_tables():
    """Every table in schema.cql, as the row checks see them."""
    return TABLES


@pytest.fixture
def stored():
    """Check a message against its topic schema, then return its rows, each checked against schema.cql."""

    def check(topic, message, received_at):
        contracts.validate(topic, message)
        rows = sink_rows.rows(topic, message, received_at)
        for row in rows:
            assert row.table in TABLES, f"{row.table} is not in schema.cql"
            columns, key = TABLES[row.table]
            assert set(row.values) <= set(columns), f"{row.table}: unknown {set(row.values) - set(columns)}"
            for column in key:
                assert row.values.get(column) is not None, f"{row.table}: primary key {column} missing"
            for column, value in row.values.items():
                expected = CQL_TYPES[columns[column]]
                assert value is None or isinstance(value, expected), f"{row.table}.{column}: {value!r}"
                if value is None:
                    continue
                if columns[column] in INT_RANGES:
                    limit = INT_RANGES[columns[column]]
                    assert not isinstance(value, bool) and -limit <= value < limit, f"{row.table}.{column}: {value!r}"
                if columns[column] == "date":
                    assert not isinstance(value, datetime), f"{row.table}.{column} needs a date"
                if columns[column] == "timestamp":
                    assert value.tzinfo is not None, f"{row.table}.{column} needs a time zone"
        return rows

    return check

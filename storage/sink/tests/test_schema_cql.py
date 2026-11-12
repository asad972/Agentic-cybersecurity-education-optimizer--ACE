"""The test helper that reads schema.cql, so row checks keep working when tables are added."""

import pytest


def test_table_level_primary_key(parse_cql):
    tables = parse_cql("""
CREATE TABLE IF NOT EXISTS top10 (
  country text,
  day     date,
  kind    text,
  rank    int,
  item    text,
  PRIMARY KEY ((country, day, kind), rank)
);
""")
    columns, key = tables["top10"]
    assert key == ["country", "day", "kind", "rank"]
    assert columns == {"country": "text", "day": "date", "kind": "text", "rank": "int", "item": "text"}


def test_inline_primary_key(parse_cql):
    tables = parse_cql("""
CREATE TABLE IF NOT EXISTS llm_daily_budget (
  day          date PRIMARY KEY,
  budget_eur   decimal,
  spent_eur    decimal
);
""")
    columns, key = tables["llm_daily_budget"]
    assert key == ["day"]
    assert columns == {"day": "date", "budget_eur": "decimal", "spent_eur": "decimal"}


def test_table_without_a_primary_key_is_an_error(parse_cql):
    with pytest.raises(ValueError, match="broken: no primary key"):
        parse_cql("""
CREATE TABLE IF NOT EXISTS broken (
  day date
);
""")


def test_every_table_in_schema_cql_has_a_key(schema_tables):
    assert schema_tables["llm_daily_budget"][1] == ["day"]
    assert all(key for _, key in schema_tables.values())

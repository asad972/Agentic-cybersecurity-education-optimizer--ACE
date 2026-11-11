from pathlib import Path

import pytest

from ace_gap.config import GapConfig, SKILLS_SCOPE


def test_fixed_scope_has_32_iso_countries():
    assert len(SKILLS_SCOPE) == len(set(SKILLS_SCOPE)) == 32
    assert {"GR", "GB", "CH", "IS", "LI", "NO"} <= set(SKILLS_SCOPE)
    assert "UK" not in SKILLS_SCOPE


def test_settings_are_loaded_from_environment():
    config = GapConfig.from_env({
        "CASSANDRA_HOSTS": "db-1,db-2",
        "CASSANDRA_PORT": "9142",
        "CASSANDRA_KEYSPACE": "ace_test",
        "CASSANDRA_LOCAL_DC": "dc1",
        "CASSANDRA_USERNAME": "ace",
        "CASSANDRA_PASSWORD": "secret",
        "GAP_MIN_SUPPLY": "30",
        "GAP_SUPPLY_LOOKBACK_DAYS": "120",
        "ACE_TAXONOMY_DIR": "/data/taxonomy",
    })
    assert config.cassandra_hosts == ("db-1", "db-2")
    assert config.min_supply == 30
    assert config.supply_lookback_days == 120
    assert config.taxonomy_dir == Path("/data/taxonomy")


@pytest.mark.parametrize(
    "environment",
    [
        {"CASSANDRA_HOSTS": " , "},
        {"CASSANDRA_PORT": "0"},
        {"CASSANDRA_KEYSPACE": "bad-name"},
        {"CASSANDRA_USERNAME": "ace"},
        {"GAP_MIN_SUPPLY": "0"},
        {"GAP_MIN_SUPPLY": "twenty"},
        {"GAP_SUPPLY_LOOKBACK_DAYS": "0"},
        {"GAP_SUPPLY_LOOKBACK_DAYS": "366"},
    ],
)
def test_invalid_environment_is_rejected(environment):
    with pytest.raises(ValueError):
        GapConfig.from_env(environment)

import pytest

from ace_forecast.config import ForecastConfig, SKILLS_SCOPE


def test_fixed_scope_has_expected_32_iso_codes():
    assert len(SKILLS_SCOPE) == 32
    assert len(set(SKILLS_SCOPE)) == 32
    assert {"DE", "GR", "GB", "CH", "IS", "LI", "NO"} <= set(SKILLS_SCOPE)
    assert "UK" not in SKILLS_SCOPE


def test_configuration_reads_cassandra_environment():
    config = ForecastConfig.from_env({
        "CASSANDRA_HOSTS": "db-1, db-2",
        "CASSANDRA_PORT": "9142",
        "CASSANDRA_KEYSPACE": "ace_test",
        "CASSANDRA_LOCAL_DC": "dc1",
        "CASSANDRA_USERNAME": "ace",
        "CASSANDRA_PASSWORD": "secret",
    })
    assert config.cassandra_hosts == ("db-1", "db-2")
    assert config.cassandra_port == 9142
    assert config.cassandra_keyspace == "ace_test"


@pytest.mark.parametrize(
    "values",
    [
        {"CASSANDRA_HOSTS": " , "},
        {"CASSANDRA_PORT": "0"},
        {"CASSANDRA_PORT": "not-a-number"},
        {"CASSANDRA_KEYSPACE": "bad-name"},
        {"CASSANDRA_USERNAME": "ace"},
    ],
)
def test_invalid_configuration_is_rejected(values):
    with pytest.raises(ValueError):
        ForecastConfig.from_env(values)

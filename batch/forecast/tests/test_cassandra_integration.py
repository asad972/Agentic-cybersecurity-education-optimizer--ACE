"""Optional smoke test against the Cassandra development instance."""

import os

import pytest

from ace_forecast.config import ForecastConfig
from ace_forecast.repository import CassandraForecastRepository


pytestmark = pytest.mark.cassandra


@pytest.mark.skipif(
    os.getenv("ACE_CASSANDRA_INTEGRATION") != "1",
    reason="set ACE_CASSANDRA_INTEGRATION=1 to run Cassandra integration tests",
)
def test_prepare_and_query_existing_forecast_contract():
    with CassandraForecastRepository.connect(ForecastConfig.from_env()) as repository:
        assert repository.read_daily("DE", "__ace_forecast_missing_test_skill__") == ()

"""Optional smoke test against the Cassandra development instance."""

from datetime import date
import os

import pytest

from ace_gap.config import GapConfig
from ace_gap.repository import CassandraGapRepository


pytestmark = pytest.mark.cassandra


@pytest.mark.skipif(
    os.getenv("ACE_CASSANDRA_INTEGRATION") != "1",
    reason="set ACE_CASSANDRA_INTEGRATION=1 to run Cassandra integration tests",
)
def test_existing_gap_input_and_output_contracts_prepare():
    with CassandraGapRepository.connect(GapConfig.from_env()) as repository:
        assert repository.read_demand_day("DE", date(1900, 1, 1)) == ()
        assert repository.latest_supply("DE", date(1900, 1, 1), 1) is None

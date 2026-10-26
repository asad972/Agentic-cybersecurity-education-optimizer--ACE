"""Optional real-Cassandra checks.

Run with ACE_CASSANDRA_INTEGRATION=1 after `docker compose up -d cassandra
cassandra-init`. Ordinary CI and unit tests skip this module.
"""

import os
import threading
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from ace.llm import CassandraLLMStore

pytestmark = pytest.mark.skipif(
    os.getenv("ACE_CASSANDRA_INTEGRATION") != "1",
    reason="set ACE_CASSANDRA_INTEGRATION=1 to run Cassandra integration tests",
)


def test_cassandra_lwt_budget_contention():
    stores = [CassandraLLMStore.from_env(), CassandraLLMStore.from_env()]
    test_day = date(2099, 1, 1)
    for store in stores:
        store.session.execute(
            "DELETE FROM llm_daily_budget WHERE day = %s", (test_day,)
        )
    barrier = threading.Barrier(3)
    results = []

    def reserve(store):
        barrier.wait()
        results.append(store.reserve_budget(test_day, Decimal(1), Decimal("0.6")))

    threads = [threading.Thread(target=reserve, args=(store,)) for store in stores]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()
    try:
        assert sorted(results) == [False, True]
        snapshot = stores[0].budget_snapshot(test_day)
        assert snapshot.reserved_eur == Decimal("0.6")
        assert snapshot.spent_eur == Decimal(0)
    finally:
        stores[0].session.execute(
            "DELETE FROM llm_daily_budget WHERE day = %s", (test_day,)
        )
        for store in stores:
            store.close()


def test_cassandra_lwt_claim_contention_and_expired_takeover():
    stores = [CassandraLLMStore.from_env(), CassandraLLMStore.from_env()]
    task = "integration_claim"
    digest = uuid.uuid4().hex
    now = datetime.now(UTC)
    stores[0].session.execute(
        "DELETE FROM llm_cache_claims WHERE task = %s AND input_hash = %s",
        (task, digest),
    )
    barrier = threading.Barrier(3)
    results = []

    def claim(store):
        token = uuid.uuid4()
        barrier.wait()
        results.append(
            (
                token,
                store.try_claim(task, digest, token, now, now + timedelta(seconds=30)),
            )
        )

    threads = [threading.Thread(target=claim, args=(store,)) for store in stores]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    try:
        assert sum(attempt.acquired for _, attempt in results) == 1
        winning_token = next(token for token, attempt in results if attempt.acquired)
        stores[0].release_claim(task, digest, winning_token)

        expired_token = uuid.uuid4()
        assert (
            stores[0]
            .try_claim(
                task,
                digest,
                expired_token,
                now - timedelta(seconds=2),
                now - timedelta(seconds=1),
            )
            .acquired
        )
        takeover = stores[1].try_claim(
            task,
            digest,
            uuid.uuid4(),
            now,
            now + timedelta(seconds=30),
        )
        assert takeover.acquired
    finally:
        stores[0].session.execute(
            "DELETE FROM llm_cache_claims WHERE task = %s AND input_hash = %s",
            (task, digest),
        )
        for store in stores:
            store.close()

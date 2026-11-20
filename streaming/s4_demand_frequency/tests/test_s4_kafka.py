"""S4 end to end on a real Kafka: fake job ads into skills.demand, Demand counts out of agg.demand_daily.

Opt-in, because it writes to the local Compose Kafka: start the stack
(docker compose up -d) and set ACE_KAFKA_TESTS=1. The ads use the made-up
country ZZ and a random day decades ahead, so they never mix with real data.
"""

import json
import os
import random
import uuid
from datetime import date, timedelta

import pytest

from ace import kafka
from ace.stage import flink_env
from s4_demand_frequency.stage import DemandFrequency

pytestmark = [
    pytest.mark.flink,
    pytest.mark.kafka,
    pytest.mark.skipif(os.environ.get("ACE_KAFKA_TESTS") != "1",
                       reason="set ACE_KAFKA_TESTS=1 with the local Kafka stack running"),
]

SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", kafka.DEFAULT_BOOTSTRAP_SERVERS)


def publish(topic, messages):
    """Write messages to a topic with the stage base's own Kafka sink."""
    from pyflink.common import Types

    env = flink_env()
    kafka.add_connector(env)
    rows = [(topic, message["country"], json.dumps(message)) for message in messages]
    kafka.write_topics(env.from_collection(rows, type_info=Types.TUPLE([Types.STRING()] * 3)), SERVERS)
    env.execute("test: publish to Kafka")


def read_all(topic):
    """Everything in a topic now, read with the stage base's own Kafka source."""
    env = flink_env()
    kafka.add_connector(env)
    stream = kafka.read_topics(env, [topic], SERVERS, group_id=f"test-{uuid.uuid4().hex}", bounded=True)
    with stream.execute_and_collect() as results:
        return [json.loads(text) for _, text in results]


def test_s4_counts_job_ads_read_from_kafka_and_publishes_to_kafka():
    day = (date(2090, 1, 1) + timedelta(days=random.randrange(3000))).isoformat()

    def ad(skills):
        return {"event_id": str(uuid.uuid4()), "source": "test", "country": "ZZ",
                "posted_date": day, "skills": skills}

    publish("skills.demand", [ad(["SIEM"]), ad(["SIEM", "Cloud security"]), ad(["Cloud security"])])
    kafka.run_on_kafka(DemandFrequency(), SERVERS, bounded=True)

    [daily] = [m for m in read_all("agg.demand_daily") if m["country"] == "ZZ" and m["day"] == day]
    counts = {(c["kind"], c["item"]): c["count"] for c in daily["counts"]}
    assert daily["job_ads"] == 3
    assert counts[("skill", "SIEM")] == 2
    assert counts[("skill", "Cloud security")] == 2

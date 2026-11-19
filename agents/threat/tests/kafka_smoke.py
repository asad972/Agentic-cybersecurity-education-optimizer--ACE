"""Run only against an EMPTY, disposable Kafka broker; no provider keys required.

This is an opt-in integration script, not part of the unit-test suite. It writes
synthetic records to the registered topics and verifies the actual broker output.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from ace import contracts
from confluent_kafka import Consumer, TopicPartition
from confluent_kafka.admin import AdminClient, NewTopic

from agents.threat.collector import CollectorStore, ThreatCollector
from agents.threat.fixtures import fixture_events
from agents.threat.runtime import EVENTS_TOPIC, TASKS_TOPIC, KafkaTransport, Publisher
from agents.threat.sources import AbuseIPDB, FeedHTTP, OTX
from agents.threat.utils import parse_time


def read_topic(bootstrap, topic, count):
    consumer = Consumer({'bootstrap.servers': bootstrap, 'group.id': 'threat-release-verification',
                         'enable.auto.commit': False})
    try:
        partition = TopicPartition(topic, 0)
        low, high = consumer.get_watermark_offsets(partition, timeout=15)
        assert low == 0 and high == count, (topic, low, high, count)
        consumer.assign([TopicPartition(topic, 0, 0)])
        rows = []
        deadline = time.monotonic() + 30
        while len(rows) < count and time.monotonic() < deadline:
            message = consumer.poll(1)
            if message is None:
                continue
            assert not message.error(), message.error()
            row = json.loads(message.value())
            contracts.validate(topic, row)
            assert message.key().decode() == contracts.key_of(topic, row)
            rows.append(row)
        assert len(rows) == count, (len(rows), count)
        return rows
    finally:
        consumer.close()


def main():
    bootstrap = os.environ['THREAT_SMOKE_KAFKA']
    # No reuse: refuse an existing topic rather than pollute an existing dataset.
    admin = AdminClient({'bootstrap.servers': bootstrap})
    topics = [EVENTS_TOPIC, TASKS_TOPIC, contracts.INVALID_TOPIC]
    existing = admin.list_topics(timeout=20).topics
    assert not any(name in existing for name in topics), 'Use an empty disposable broker'
    for future in admin.create_topics([NewTopic(name, 1, 1) for name in topics]).values():
        future.result(20)

    def provider(request):
        if request.url.host == 'api.abuseipdb.com':
            if request.url.path.endswith('/blacklist'):
                return httpx.Response(200, json={'data': [{'ipAddress': '192.0.2.1',
                    'lastReportedAt': '2026-10-19T12:00:00Z', 'abuseConfidenceScore': 99}]})
            return httpx.Response(200, json={'data': {'countryCode': 'HU'}})
        if '/indicators/' in request.url.path:
            return httpx.Response(200, json={'country_code': 'DE'})
        return httpx.Response(200, json={'results': [{'id': 'test-pulse', 'created': '2026-10-19T12:00:00',
            'tags': ['phishing'], 'indicators': [{'type': 'IPv4', 'indicator': '192.0.2.2'}]}], 'next': None})

    transport = KafkaTransport(bootstrap)
    publisher = Publisher(transport)
    feeds = [OTX(FeedHTTP(httpx.Client(transport=httpx.MockTransport(provider))), 'test-only'),
             AbuseIPDB(FeedHTTP(httpx.Client(transport=httpx.MockTransport(provider))), 'test-only')]
    with tempfile.TemporaryDirectory() as directory:
        state = Path(directory) / 'feeds.sqlite'
        store = CollectorStore(state)
        try:
            collector = ThreatCollector(feeds, store, publisher, transport.flush)
            assert collector.collect(now=parse_time('2026-10-20T00:00:00Z')).sent == 2
        finally:
            store.close()
        # Reopening the durable store and replaying the same feed sends nothing.
        store = CollectorStore(state)
        try:
            assert ThreatCollector(feeds, store, publisher, transport.flush).collect(
                now=parse_time('2026-10-20T00:00:00Z')).sent == 0
        finally:
            store.close()
            for feed in feeds:
                feed.http.close()
        env = {**os.environ, 'KAFKA_BOOTSTRAP_SERVERS': bootstrap}
        command = [sys.executable, '-m', 'agents.threat', '--fixture', '--fixture-hours', '1',
                   '--publish', '--state', str(Path(directory) / 'fixtures.sqlite'),
                   '--output', str(Path(directory) / 'fixtures.jsonl')]
        expected = fixture_events(1)
        for count in (len(expected), 0):
            result = subprocess.run(command, env=env, capture_output=True, text=True, check=True, timeout=90)
            assert f'Published {count} fixture observations' in result.stdout, result.stdout
            print(result.stdout.strip())
        assert not publisher.publish(EVENTS_TOPIC, {'country': 'invalid'})
        transport.flush()
        records = read_topic(bootstrap, EVENTS_TOPIC, len(expected) + 2)
        assert len({r['event_id'] for r in records}) == len(records)
        assert {r['source'] for r in records} == {'otx', 'abuseipdb', 'fixture'}
        assert {r['country'] for r in records if r['source'] != 'fixture'} == {'HU', 'DE'}
        assert read_topic(bootstrap, contracts.INVALID_TOPIC, 1)[0]['topic'] == EVENTS_TOPIC
        print(f'PASS: {len(records)} valid country-keyed Kafka events; both mocked feeds, CLI, restart deduplication, errors.invalid')


if __name__ == '__main__':
    main()

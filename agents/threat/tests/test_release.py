import json
from types import SimpleNamespace

import httpx
import pytest
from ace import contracts

from agents.threat.__main__ import main, process_task
from agents.threat.collector import CollectorStore, ThreatCollector
from agents.threat.fixtures import fixture_events
from agents.threat.runtime import EVENTS_TOPIC, KafkaTransport, Publisher, validate_task
from agents.threat.sources import AbuseIPDB, FetchResult, FeedHTTP, OTX, source_time
from agents.threat.utils import parse_time

START = parse_time('2026-10-19T00:00:00Z')
END = parse_time('2026-10-20T00:00:00Z')


def task(**changes):
    value = {'task_id': 'af9925af-917b-4182-bfe5-1fa41161ab81', 'agent': 'threat-agent',
             'action': 'refresh_threats', 'params': {}, 'reason': 'test', 'decided_by': 'rules',
             'created_at': '2026-10-20T00:00:00Z'}
    value.update(changes)
    return value


def test_standard_abuse_blacklist_resolves_missing_country_and_preserves_report_time():
    calls = []
    def respond(request):
        calls.append(request)
        if request.url.path.endswith('/blacklist'):
            assert request.url.params['limit'] == '2'
            return httpx.Response(200, json={'data': [
                {'ipAddress': '192.0.2.1', 'lastReportedAt': '2026-10-19T12:00:00Z'},
                {'ipAddress': '192.0.2.2', 'lastReportedAt': '2026-09-19T12:00:00Z'}]})
        assert request.url.path.endswith('/check')
        assert request.url.params['ipAddress'] == '192.0.2.1'
        return httpx.Response(200, json={'data': {'countryCode': 'HU', 'lastReportedAt': '2026-10-20T12:00:00Z'}})
    source = AbuseIPDB(FeedHTTP(httpx.Client(transport=httpx.MockTransport(respond))), 'test-only', limit=2)
    result = source.fetch(START, END, country='HU')
    assert len(calls) == 2  # Do not spend quota resolving old/out-of-window rows.
    assert all(r.method == 'GET' and r.headers['Key'] == 'test-only' for r in calls)
    assert len(result.events) == 1
    assert result.events[0]['country'] == 'HU'
    assert result.events[0]['observed_at'] == '2026-10-19T12:00:00Z'
    contracts.validate(EVENTS_TOPIC, result.events[0])


@pytest.mark.parametrize('status', [401, 403])
def test_authentication_failure_is_not_retried(status):
    attempts, delays = [], []
    def respond(request):
        attempts.append(request)
        return httpx.Response(status)
    http = FeedHTTP(httpx.Client(transport=httpx.MockTransport(respond)), sleep=delays.append)
    with pytest.raises(httpx.HTTPStatusError):
        http.get('https://api.abuseipdb.com/api/v2/blacklist', headers={})
    assert len(attempts) == 1 and delays == []


def test_otx_rejects_offsite_paging_and_does_not_follow_redirects():
    seen = []
    def respond(request):
        seen.append(request.url.host)
        return httpx.Response(302, headers={'Location': 'https://untrusted.example/'})
    source = OTX(FeedHTTP(httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=False)), 'test-only')
    result = source.fetch(START, END)
    assert not result.complete and isinstance(result.retry_error, httpx.HTTPStatusError)
    assert seen == ['otx.alienvault.com']
    for url in ('http://otx.alienvault.com/api/v1/pulses/subscribed',
                'https://otx.alienvault.com.evil.example/api/v1/pulses/subscribed',
                'https://otx.alienvault.com/api/v1/other'):
        with pytest.raises(ValueError, match='pagination'):
            source.fetch(START, END, cursor={'next_url': url})
    assert len(seen) == 1


def test_otx_skips_malformed_or_missing_indicators_but_keeps_valid_ipv6():
    def respond(request):
        if '/indicators/IPv4/' in request.url.path:
            return httpx.Response(404)
        if '/indicators/IPv6/' in request.url.path:
            return httpx.Response(200, json={'country_code': 'DE'})
        return httpx.Response(200, json={'results': [None, {'indicators': None},
            {'id': 'pulse', 'created': '2026-10-19T12:00:00', 'tags': ['ransomware'],
             'indicators': [None, {'type': 'URL', 'indicator': 'https://untrusted.example'},
                            {'type': 'IPv4', 'indicator': '192.0.2.1'},
                            {'type': 'IPv6', 'indicator': '2001:db8::1'}]}], 'next': None})
    result = OTX(FeedHTTP(httpx.Client(transport=httpx.MockTransport(respond))), 'test-only').fetch(START, END)
    assert result.skipped == 5 and len(result.events) == 1
    assert result.events[0]['country'] == 'DE' and result.events[0]['threat_type'] == 'malware'
    contracts.validate(EVENTS_TOPIC, result.events[0])


def test_invalid_normalized_record_routes_through_shared_error_contract():
    output = []
    publisher = Publisher(lambda *args: output.append(args))
    assert not publisher.publish(EVENTS_TOPIC, {'country': 'Germany'})
    topic, key, data = output[0]
    assert topic == contracts.INVALID_TOPIC and key == EVENTS_TOPIC
    contracts.validate(topic, json.loads(data))


def test_outbox_validation_is_atomic_with_cursor(tmp_path):
    store = CollectorStore(tmp_path / 'state.sqlite')
    try:
        with pytest.raises(contracts.InvalidMessage):
            store.save([fixture_events(1)[0], {'country': 'Germany'}], 'test', {'page': 2})
        assert store.cursor('test') == {}
        assert store.drain(Publisher(lambda *args: pytest.fail('must not send')), lambda: None) == 0
    finally:
        store.close()


def test_provider_failure_does_not_block_pending_or_other_feed_delivery(tmp_path):
    output = []
    events = fixture_events(1)[:2]
    store = CollectorStore(tmp_path / 'state.sqlite')
    publisher = Publisher(lambda *args: output.append(args))
    store.save([events[0]])
    def fail(*args, **kwargs):
        assert len(output) == 1  # Pending delivery happens before new network requests.
        raise httpx.ConnectError('provider unavailable')
    def succeed(*args, **kwargs):
        return FetchResult([events[1]], {'fetched': True})
    collector = ThreatCollector([SimpleNamespace(name='bad', fetch=fail),
                                 SimpleNamespace(name='good', fetch=succeed)], store, publisher, lambda: None)
    try:
        collector.handle_task(task())
        assert len(output) == 2
        assert 'since' in store.cursor('bad:*:' + task()['task_id'])
        assert store.feed_state('bad')[1] > 0
        assert store.pending_tasks() == [task()]
        assert not store.task_done(task()['task_id'])
    finally:
        store.close()


@pytest.mark.parametrize('message,key', [(task(), b'job-agent'),
    (task(params={'country': 'ZZ'}), b'threat-agent'),
    (task(action='fetch_job_ads'), b'threat-agent')])
def test_task_semantics_and_kafka_key_are_validated(message, key):
    with pytest.raises(contracts.InvalidMessage):
        validate_task(message, key)


def test_invalid_task_goes_to_error_topic_before_ack():
    records, flushed = [], []
    process_task(SimpleNamespace(value=lambda: b'{broken', key=lambda: b'threat-agent'),
                 None, Publisher(lambda *args: records.append(args)), lambda: flushed.append(True))
    assert flushed == [True]
    assert records[0][0] == contracts.INVALID_TOPIC
    contracts.validate(records[0][0], json.loads(records[0][2]))


def test_task_enqueue_failure_propagates_for_redelivery():
    def fail(*args):
        raise RuntimeError('not acknowledged')
    message = SimpleNamespace(value=lambda: json.dumps(task()).encode(), key=lambda: b'threat-agent')
    with pytest.raises(RuntimeError, match='not acknowledged'):
        process_task(message, SimpleNamespace(accept_task=fail), None, None)


@pytest.mark.parametrize('pending,error', [(1, None), (0, 'broker rejected message')])
def test_failed_kafka_ack_does_not_mark_outbox_sent(tmp_path, pending, error):
    class Producer:
        def produce(self, topic, **kwargs):
            assert topic == EVENTS_TOPIC
            kwargs['on_delivery'](error, None)
        def poll(self, timeout):
            pass
        def flush(self, timeout):
            return pending
    transport = KafkaTransport('unused', producer=Producer())
    store = CollectorStore(tmp_path / 'state.sqlite')
    try:
        store.save(fixture_events(1)[:1])
        with pytest.raises(RuntimeError, match='Kafka delivery failed'):
            store.drain(Publisher(transport), transport.flush)
        sent = []
        assert store.drain(Publisher(lambda *args: sent.append(args)), lambda: None) == 1
        assert len(sent) == 1
    finally:
        store.close()


def test_fixture_cli_needs_no_credentials_or_broker(tmp_path, monkeypatch):
    monkeypatch.delenv('OTX_API_KEY', raising=False)
    monkeypatch.delenv('ABUSEIPDB_API_KEY', raising=False)
    output = tmp_path / 'fixture.jsonl'
    main(['--fixture', '--fixture-hours', '1', '--output', str(output)])
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert rows and all(row['source'] == 'fixture' for row in rows)
    for row in rows:
        contracts.validate(EVENTS_TOPIC, row)


def test_missing_key_and_invalid_country_fail_before_connecting(monkeypatch):
    monkeypatch.delenv('OTX_API_KEY', raising=False)
    for args in (['--source', 'otx', '--once'], ['--country', 'ZZ']):
        with pytest.raises(SystemExit) as error:
            main(args)
        assert error.value.code == 2


def test_only_otx_accepts_naive_timestamps_as_utc():
    with pytest.raises(ValueError, match='timezone'):
        source_time('2026-10-19T12:00:00')
    assert source_time('2026-10-19T12:00:00', naive_utc=True).hour == 12
    assert source_time('2026-10-19T14:00:00+02:00').hour == 12

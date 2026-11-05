"""Reviewer scenarios: retries must preserve work and keep the service running."""
import json
import sqlite3
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from ace import contracts

from agents.threat.__main__ import poll_task, process_task, run_loop
from agents.threat.collector import CollectorStore, ThreatCollector, retry_delay
from agents.threat.fixtures import fixture_events
from agents.threat.runtime import EVENTS_TOPIC, FatalKafkaError, KafkaDeliveryError, Publisher
from agents.threat.sources import AbuseIPDB, FetchResult, FeedHTTP, OTX
from agents.threat.utils import parse_time, utc_text

NOW = parse_time('2026-11-04T12:00:00Z')
OBSERVED = '2026-11-04T11:00:00Z'


def rate_limit(seconds='3600'):
    response = httpx.Response(429, headers={'Retry-After': seconds}, request=httpx.Request('GET', 'https://provider.example'))
    return httpx.HTTPStatusError('rate limited', request=response.request, response=response)


def task():
    return {'task_id': 'af9925af-917b-4182-bfe5-1fa41161ab81', 'agent': 'threat-agent',
            'action': 'refresh_threats', 'params': {}, 'reason': 'test', 'decided_by': 'rules',
            'created_at': utc_text(NOW)}


def test_cooldown_survives_restart_while_other_feed_keeps_polling(tmp_path):
    counts = {'otx': 0, 'abuseipdb': 0}
    failing = [True]
    def otx(*args, **kwargs):
        counts['otx'] += 1
        return FetchResult([], {})
    def abuse(*args, **kwargs):
        counts['abuseipdb'] += 1
        if failing[0]:
            raise rate_limit()
        return FetchResult([], {})
    feeds = [SimpleNamespace(name='otx', fetch=otx), SimpleNamespace(name='abuseipdb', fetch=abuse)]
    path = tmp_path / 'state.sqlite'
    for when in (NOW, NOW + timedelta(seconds=61)):
        store = CollectorStore(path)
        try:
            collector = ThreatCollector(feeds, store, Publisher(lambda *a: None), lambda: None)
            collector.collect(now=when, poll_seconds=60)
        finally:
            store.close()
    assert counts == {'otx': 2, 'abuseipdb': 1}
    failing[0] = False
    store = CollectorStore(path)
    try:
        ThreatCollector(feeds, store, Publisher(lambda *a: None), lambda: None).collect(now=NOW + timedelta(hours=1, seconds=1))
        assert counts['abuseipdb'] == 2
        assert store.feed_state('abuseipdb') == (0, 0)
    finally:
        store.close()


@pytest.mark.parametrize('provider_class', [AbuseIPDB, OTX])
def test_lookup_rate_limit_keeps_events_and_resumes_exact_row_after_restart(tmp_path, provider_class):
    lookups, feed_calls = [], []
    failed = [False]
    def respond(request):
        if request.url.path.endswith('/blacklist'):
            feed_calls.append('blacklist')
            return httpx.Response(200, json={'data': [
                {'ipAddress': f'192.0.2.{i}', 'lastReportedAt': OBSERVED} for i in (1, 2, 3)]})
        if request.url.path.endswith('/subscribed'):
            feed_calls.append('pulses')
            return httpx.Response(200, json={'results': [{'id': 'pulse', 'created': OBSERVED,
                'indicators': [{'type': 'IPv4', 'indicator': f'192.0.2.{i}'} for i in (1, 2, 3)]}], 'next': None})
        ip = request.url.params.get('ipAddress') or request.url.path.split('/')[-2]
        lookups.append(ip)
        if ip.endswith('.2') and not failed[0]:
            failed[0] = True
            return httpx.Response(429, headers={'Retry-After': '120'})
        return httpx.Response(200, json={'data': {'countryCode': 'HU'}} if '/check' in request.url.path else {'country_code': 'HU'})
    output, path = [], tmp_path / 'state.sqlite'
    for when, expected in ((NOW, 1), (NOW + timedelta(seconds=121), 3)):
        store = CollectorStore(path)
        http = FeedHTTP(httpx.Client(transport=httpx.MockTransport(respond)), sleep=lambda _: None)
        source = provider_class(http, 'test-only')
        try:
            collector = ThreatCollector([source], store, Publisher(lambda *a: output.append(a)), lambda: None)
            collector.collect(now=when)
            assert len(output) == expected
        finally:
            source.http.close()
            store.close()
    assert len(feed_calls) == 1  # Resume saved rows, never re-fetch the snapshot/page.
    assert lookups == ['192.0.2.1', '192.0.2.2', '192.0.2.2', '192.0.2.3']
    assert len({json.loads(item[2])['event_id'] for item in output}) == 3


def test_otx_later_page_failure_does_not_discard_first_page():
    requests, failing = [], [True]
    def respond(request):
        requests.append(str(request.url))
        if '/indicators/' in request.url.path:
            return httpx.Response(200, json={'country_code': 'HU'})
        second = request.url.params.get('page') == '2'
        if second and failing[0]:
            return httpx.Response(503)
        return httpx.Response(200, json={'results': [{'id': 'second' if second else 'first',
            'created': OBSERVED, 'indicators': [{'type': 'IPv4', 'indicator': '192.0.2.2' if second else '192.0.2.1'}]}],
            'next': None if second else OTX.endpoint + '?page=2'})
    source = OTX(FeedHTTP(httpx.Client(transport=httpx.MockTransport(respond)), sleep=lambda _: None), 'test-only')
    first = source.fetch(NOW - timedelta(hours=24), NOW, max_pages=2)
    assert len(first.events) == 1 and not first.complete and first.retry_error
    failing[0] = False
    second = source.fetch(NOW - timedelta(hours=24), NOW, cursor=first.cursor)
    assert len(second.events) == 1 and second.complete
    assert sum('/subscribed' in url and 'page=2' not in url for url in requests) == 1


@pytest.mark.parametrize('gap', [timedelta(hours=6), timedelta(days=4)])
def test_saved_high_watermark_prevents_skipping_time(tmp_path, gap):
    windows = []
    def fetch(since, until, **kwargs):
        windows.append((since, until))
        return FetchResult([], {})
    store = CollectorStore(tmp_path / 'state.sqlite')
    try:
        collector = ThreatCollector([SimpleNamespace(name='otx', fetch=fetch)], store, Publisher(lambda *a: None), lambda: None)
        collector.collect(now=NOW, hours=1, poll_seconds=21600)
        collector.collect(now=NOW + gap, hours=1, poll_seconds=21600)
        assert windows[1][0] == windows[0][1] == NOW
        assert windows[1][1] == min(NOW + gap, NOW + timedelta(hours=24))
        if gap.days:
            collector.collect(now=NOW + gap + timedelta(seconds=61), hours=1, poll_seconds=21600)
            assert windows[2][0] == windows[1][1]
    finally:
        store.close()


def test_stale_outbox_row_is_retired_only_after_reject_delivery(tmp_path):
    store = CollectorStore(tmp_path / 'state.sqlite')
    output = []
    publisher = Publisher(lambda *a: output.append(a))
    try:
        with store.db:
            store.db.execute('INSERT INTO observations(event_id,payload) VALUES (?,?)', ('stale', '{"country":"wrong"}'))
        store.save(fixture_events(1)[:1])
        def fail():
            raise KafkaDeliveryError('not acknowledged')
        with pytest.raises(KafkaDeliveryError):
            store.drain(publisher, fail)
        assert store.db.execute('SELECT COUNT(*) FROM observations WHERE sent=0').fetchone()[0] == 2
        assert store.drain(publisher, lambda: None) == 2
        assert store.drain(publisher, lambda: None) == 0
        assert [row[0] for row in output] == [contracts.INVALID_TOPIC, EVENTS_TOPIC] * 2
    finally:
        store.close()


def test_task_is_durable_before_ack_and_partial_pages_finish_after_restart(tmp_path):
    calls, output = [], []
    def fetch(since, until, **kwargs):
        calls.append(kwargs['cursor'])
        if len(calls) == 1:
            return FetchResult(fixture_events(1)[:1], {'next_url': OTX.endpoint}, complete=False)
        return FetchResult(fixture_events(1)[1:2], {}, complete=True)
    path = tmp_path / 'state.sqlite'
    store = CollectorStore(path)
    collector = ThreatCollector([SimpleNamespace(name='otx', fetch=fetch)], store, Publisher(lambda *a: output.append(a)), lambda: None)
    message = SimpleNamespace(value=lambda: json.dumps(task()).encode(), key=lambda: b'threat-agent')
    process_task(message, collector, None, None)
    assert calls == [] and store.pending_tasks() == [task()]
    collector.run_pending_tasks(now=NOW)
    assert not store.task_done(task()['task_id']) and len(output) == 1
    store.close()
    store = CollectorStore(path)
    try:
        collector = ThreatCollector([SimpleNamespace(name='otx', fetch=fetch)], store, Publisher(lambda *a: output.append(a)), lambda: None)
        collector.run_pending_tasks(now=NOW + timedelta(seconds=61))
        assert len(output) == 2 and store.task_done(task()['task_id'])
        assert store.pending_tasks() == []
        assert store.db.execute('SELECT COUNT(*) FROM cursors').fetchone()[0] == 0
    finally:
        store.close()


def test_completed_task_feed_is_not_refetched_while_another_is_partial(tmp_path):
    calls = {'good': 0, 'slow': 0}
    def good(*args, **kwargs):
        calls['good'] += 1
        return FetchResult([], {})
    def slow(*args, **kwargs):
        calls['slow'] += 1
        return FetchResult([], {}, complete=calls['slow'] > 1)
    store = CollectorStore(tmp_path / 'state.sqlite')
    try:
        collector = ThreatCollector([SimpleNamespace(name='good', fetch=good), SimpleNamespace(name='slow', fetch=slow)], store, Publisher(lambda *a: None), lambda: None)
        collector.accept_task(task())
        collector.run_pending_tasks(now=NOW)
        collector.run_pending_tasks(now=NOW + timedelta(seconds=61))
        assert calls == {'good': 1, 'slow': 2} and store.task_done(task()['task_id'])
    finally:
        store.close()


def test_pruning_preserves_unsent_rows_and_pending_tasks_and_migrates_old_state(tmp_path):
    path = tmp_path / 'state.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE observations(event_id TEXT PRIMARY KEY, payload TEXT, sent INTEGER DEFAULT 0)')
        db.execute('CREATE TABLE tasks(task_id TEXT PRIMARY KEY)')
        db.execute("INSERT INTO tasks VALUES ('old')")
    store = CollectorStore(path)
    try:
        store.save(fixture_events(1)[:3])
        ids = [r['event_id'] for r in fixture_events(1)[:3]]
        with store.db:
            store.db.execute('UPDATE observations SET sent=1, handled_at=? WHERE event_id=?', (NOW.timestamp()-40*86400, ids[0]))
            store.db.execute('UPDATE observations SET sent=1, handled_at=? WHERE event_id=?', (NOW.timestamp(), ids[1]))
            store.db.execute('UPDATE tasks SET completed_at=?', (NOW.timestamp()-40*86400,))
        store.enqueue_task(task())
        store.prune(30, NOW)
        assert {r[0] for r in store.db.execute('SELECT event_id FROM observations')} == set(ids[1:])
        assert not store.task_done('old') and store.pending_tasks() == [task()]
    finally:
        store.close()


@pytest.mark.parametrize('fatal', [False, True])
def test_only_fatal_consumer_errors_stop_polling(fatal, caplog):
    error = SimpleNamespace(fatal=lambda: fatal)
    consumer = SimpleNamespace(poll=lambda _: SimpleNamespace(error=lambda: error))
    if fatal:
        with pytest.raises(FatalKafkaError):
            poll_task(consumer)
    else:
        assert poll_task(consumer) is None
        assert 'Recoverable Kafka consumer error' in caplog.text


def test_run_loop_retries_kafka_hiccup_instead_of_exiting():
    calls, sleeps = [], []
    def collect(**kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise KafkaDeliveryError('temporary broker loss')
        raise KeyboardInterrupt
    collector = SimpleNamespace(collect=collect)
    with pytest.raises(KeyboardInterrupt):
        run_loop(collector, None, None, None, hours=24, max_pages=1, country=None,
                 poll_seconds=21600, sleep=sleeps.append)
    assert len(calls) == 2 and sleeps == [5]


def test_rate_limit_honors_long_retry_after_and_http_date():
    assert retry_delay(rate_limit('99999'), 1, NOW) == 99999
    assert retry_delay(rate_limit('Wed, 04 Nov 2026 14:00:00 GMT'), 1, NOW) == 7200


def test_retry_deadline_starts_when_slow_request_finishes(tmp_path, monkeypatch):
    from datetime import datetime
    clock = [NOW]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]
    monkeypatch.setattr('agents.threat.collector.datetime', Clock)
    def fetch(*args, **kwargs):
        clock[0] += timedelta(minutes=10)
        raise rate_limit('120')
    store = CollectorStore(tmp_path / 'state.sqlite')
    try:
        ThreatCollector([SimpleNamespace(name='slow', fetch=fetch)], store,
                        Publisher(lambda *a: None), lambda: None).collect()
        assert store.feed_state('slow')[1] == (NOW + timedelta(minutes=12)).timestamp()
    finally:
        store.close()

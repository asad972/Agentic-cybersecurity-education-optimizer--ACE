"""November review: permanent failures, bounded task work, and consumer progress."""
import json
import sqlite3
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from confluent_kafka import KafkaError, KafkaException

from agents.threat.__main__ import run_loop
from agents.threat.collector import CollectorStore, ThreatCollector
from agents.threat.runtime import Publisher
from agents.threat.sources import AbuseIPDB, FetchResult, FeedHTTP, OTX
from agents.threat.tests.test_review_regressions import NOW, OBSERVED, rate_limit, task
from agents.threat.utils import utc_text


def provider(klass, status, count=3, next_url=None):
    calls = []
    def respond(request):
        calls.append(str(request.url))
        if request.url.path.endswith('/blacklist'):
            return httpx.Response(200, json={'data': [
                {'ipAddress': f'192.0.2.{i}', 'lastReportedAt': OBSERVED} for i in range(1, count + 1)]})
        if request.url.path.endswith('/subscribed'):
            return httpx.Response(200, json={'results': [{'id': 'pulse', 'created': OBSERVED,
                'indicators': [{'type': 'IPv4', 'indicator': f'192.0.2.{i}'} for i in range(1, count + 1)]}], 'next': next_url})
        ip = request.url.params.get('ipAddress') or request.url.path.split('/')[-2]
        if ip.endswith('.2') and status:
            return httpx.Response(status)
        return httpx.Response(200, json={'data': {'countryCode': 'HU'}} if '/check' in request.url.path else {'country_code': 'HU'})
    return klass(FeedHTTP(httpx.Client(transport=httpx.MockTransport(respond)), sleep=lambda _: None), 'test-only'), calls


@pytest.mark.parametrize('klass', [AbuseIPDB, OTX])
@pytest.mark.parametrize('status', [400, 401, 403, 404, 422])
def test_permanent_lookup_errors_skip_only_bad_row(klass, status, caplog):
    source, calls = provider(klass, status)
    try:
        result = source.fetch(NOW - timedelta(hours=24), NOW)
        assert result.complete and result.retry_error is None
        assert result.skipped == 1 and len(result.events) == 2
        assert any('192.0.2.3' in url for url in calls)
        assert f'HTTP {status}' in caplog.text
    finally:
        source.http.close()


@pytest.mark.parametrize('klass', [AbuseIPDB, OTX])
@pytest.mark.parametrize('status', [408, 429, 503])
def test_retryable_lookup_errors_preserve_partial_progress(klass, status):
    source, calls = provider(klass, status)
    try:
        result = source.fetch(NOW - timedelta(hours=24), NOW)
        assert not result.complete and result.retry_error.response.status_code == status
        assert len(result.events) == 1
        assert not any('192.0.2.3' in url for url in calls)
    finally:
        source.http.close()


@pytest.mark.parametrize('bad', ['https://evil.example/steal', 'http://otx.alienvault.com/api/v1/pulses/subscribed', {'wrong': 'type'}])
def test_bad_next_url_closes_window_and_never_enters_cursor(bad):
    source, calls = provider(OTX, None, next_url=bad)
    try:
        result = source.fetch(NOW - timedelta(hours=24), NOW)
        assert result.complete and len(result.events) == 3
        assert result.cursor == {'fetched_through': utc_text(NOW)}
        assert all(url.startswith('https://otx.alienvault.com/') for url in calls)
        # Old poisoned cursors also close rather than being retried forever.
        before = len(calls)
        result = source.fetch(NOW - timedelta(hours=24), NOW, cursor={'next_url': bad, 'pending_items': []})
        assert result.complete and len(calls) == before
    finally:
        source.http.close()


def test_otx_partial_cursor_size_does_not_grow_with_pulse():
    # Indicator strings are intentionally synthetic; only the first is read.
    source, _ = provider(OTX, None, count=2000)
    source.max_indicators = 1
    try:
        result = source.fetch(NOW - timedelta(hours=24), NOW)
        assert not result.complete and result.cursor['indicator_offset'] == 1
        assert 'pending_items' not in result.cursor
        assert len(json.dumps(result.cursor)) < 400
    finally:
        source.http.close()


def test_legacy_otx_snapshot_cursor_is_drained_without_refetch():
    source, calls = provider(OTX, None)
    old = {'pending_items': [[{'id': 'pulse', 'created': OBSERVED},
                             {'type': 'IPv4', 'indicator': '192.0.2.1'}]], 'next_url': None}
    try:
        result = source.fetch(NOW - timedelta(hours=24), NOW, cursor=old)
        assert result.complete and len(result.events) == 1
        assert len(calls) == 1 and '/indicators/' in calls[0]
    finally:
        source.http.close()


def test_abuse_restart_gap_fetches_current_snapshot_once(tmp_path):
    times, calls = [], []
    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={'data': [{'ipAddress': '192.0.2.1',
            'countryCode': 'HU', 'lastReportedAt': utc_text(NOW + timedelta(days=4, hours=-1))}]})
    source = AbuseIPDB(FeedHTTP(httpx.Client(transport=httpx.MockTransport(respond))), 'test-only')
    original = source.fetch
    def capture(since, until, **kwargs):
        times.append((since, until))
        return original(since, until, **kwargs)
    source.fetch = capture
    store = CollectorStore(tmp_path / 'state.sqlite')
    try:
        store.save([], 'abuseipdb:*:scheduled', {'fetched_through': utc_text(NOW)})
        collector = ThreatCollector([source], store, Publisher(lambda *a: None), lambda: None)
        result = collector.collect(now=NOW + timedelta(days=4), poll_seconds=21600)
        assert result.sent == 1 and result.complete
        collector.collect(now=NOW + timedelta(days=4, seconds=61), poll_seconds=21600)
        assert len(calls) == 1
        assert times == [(NOW + timedelta(days=3), NOW + timedelta(days=4))]
        assert store.cursor('abuseipdb:*:scheduled')['fetched_through'] == utc_text(NOW + timedelta(days=4))
    finally:
        store.close()
        source.http.close()


def test_once_ignores_schedule_and_saved_feed_cooldown(tmp_path):
    calls = []
    def fetch(*args, **kwargs):
        calls.append(True)
        return FetchResult([], {})
    store = CollectorStore(tmp_path / 'state.sqlite')
    collector = ThreatCollector([SimpleNamespace(name='feed', fetch=fetch)], store, Publisher(lambda *a: None), lambda: None)
    try:
        for _ in range(2):
            # Deliberately install a far-future wait each time.
            store.db.execute("INSERT OR REPLACE INTO feed_state VALUES ('feed',1,99999999999)")
            run_loop(collector, None, None, None, hours=24, max_pages=1, country=None, poll_seconds=21600, once=True)
        assert len(calls) == 2
    finally:
        store.close()


def test_once_reports_provider_failure_as_nonzero_exit(tmp_path):
    def fail(*args, **kwargs):
        raise rate_limit()
    store = CollectorStore(tmp_path / 'state.sqlite')
    try:
        collector = ThreatCollector([SimpleNamespace(name='feed', fetch=fail)], store, Publisher(lambda *a: None), lambda: None)
        with pytest.raises(SystemExit) as error:
            run_loop(collector, None, None, None, hours=24, max_pages=1, country=None, poll_seconds=21600, once=True)
        assert error.value.code == 1
    finally:
        store.close()


def test_failed_task_is_bounded_and_retains_failure_receipt_across_restart(tmp_path):
    calls = []
    def fail(*args, **kwargs):
        calls.append(True)
        response = httpx.Response(401, request=httpx.Request('GET', OTX.endpoint))
        response.raise_for_status()
    path = tmp_path / 'state.sqlite'
    for attempt in range(3):
        store = CollectorStore(path)
        try:
            collector = ThreatCollector([SimpleNamespace(name='otx', fetch=fail)], store, Publisher(lambda *a: None), lambda: None)
            collector.accept_task(task())
            collector.run_pending_tasks(now=NOW + timedelta(hours=attempt))
            if attempt < 2:
                # Cooldown work is filtered in SQL, not parsed every loop tick.
                assert store.pending_tasks(NOW + timedelta(hours=attempt, seconds=1)) == []
        finally:
            store.close()
    store = CollectorStore(path)
    try:
        assert len(calls) == 3 and store.pending_tasks() == []
        status, error = store.db.execute('SELECT status,error FROM tasks').fetchone()
        assert status == 'failed' and 'HTTP 401' in error
        assert store.db.execute('SELECT COUNT(*) FROM cursors').fetchone()[0] == 0
    finally:
        store.close()


def test_task_deadline_expires_even_during_a_long_global_cooldown(tmp_path):
    store = CollectorStore(tmp_path / 'state.sqlite')
    try:
        source = SimpleNamespace(name='otx', fetch=lambda *a, **k: pytest.fail('provider is deferred'))
        collector = ThreatCollector([source], store, Publisher(lambda *a: None), lambda: None)
        collector.accept_task(task())
        with store.db:
            store.db.execute('UPDATE pending_tasks SET expires_at=?', ((NOW + timedelta(hours=24)).timestamp(),))
        store.defer('otx', rate_limit('999999'), NOW)
        collector.run_pending_tasks(now=NOW)
        assert store.pending_tasks(NOW + timedelta(hours=1)) == []
        collector.run_pending_tasks(now=NOW + timedelta(hours=24, seconds=1))
        assert store.pending_tasks() == []
        assert store.db.execute('SELECT status FROM tasks').fetchone()[0] == 'failed'
    finally:
        store.close()


def test_old_pending_queue_migrates_without_losing_work(tmp_path):
    path = tmp_path / 'old.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE pending_tasks (task_id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
        db.execute('INSERT INTO pending_tasks VALUES (?,?)', (task()['task_id'], json.dumps(task())))
    store = CollectorStore(path)
    try:
        assert store.pending_tasks() == [task()]
        attempts, expiry, error = store.task_progress(task()['task_id'])
        assert attempts == 0 and expiry > 0 and error is None
        collector = ThreatCollector([SimpleNamespace(name='feed', fetch=lambda *a, **k: FetchResult([], {}))],
                                    store, Publisher(lambda *a: None), lambda: None)
        collector.run_pending_tasks()
        assert store.task_done(task()['task_id']) and store.pending_tasks() == []
        assert store.db.execute('SELECT status,error FROM tasks').fetchone() == ('complete', None)
    finally:
        store.close()


@pytest.mark.parametrize('code', [KafkaError.REBALANCE_IN_PROGRESS, KafkaError.ILLEGAL_GENERATION, KafkaError._TRANSPORT])
def test_commit_failure_keeps_polling_and_drains_backlog_before_collection(tmp_path, code):
    store = CollectorStore(tmp_path / 'state.sqlite')
    collector = ThreatCollector([], store, Publisher(lambda *a: None), lambda: None)
    messages = [SimpleNamespace(value=lambda payload=json.dumps({**task(), 'task_id': str(uuid4())}).encode(): payload,
                                key=lambda: b'threat-agent', error=lambda: None) for _ in range(20)]
    polled, committed = [], []
    class Consumer:
        def poll(self, timeout):
            polled.append(True)
            return messages.pop(0) if messages else None
        def commit(self, **kwargs):
            committed.append(True)
            if len(committed) == 1:
                raise KafkaException(KafkaError(code))
    def collect(**kwargs):
        assert len(store.pending_tasks()) == 20
        assert len(polled) == 21 and len(committed) == 20
        raise KeyboardInterrupt
    collector.collect = collect
    try:
        with pytest.raises(KeyboardInterrupt):
            run_loop(collector, Consumer(), None, None, hours=24, max_pages=1, country=None,
                     poll_seconds=21600, sleep=lambda _: pytest.fail('no per-message sleep'))
    finally:
        store.close()

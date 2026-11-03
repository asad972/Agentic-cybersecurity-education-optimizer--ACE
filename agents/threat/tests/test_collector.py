import json
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest

from ace.contracts import InvalidMessage
from agents.threat.collector import CollectorStore, ThreatCollector
from agents.threat.fixtures import fixture_events
from agents.threat.runtime import Publisher
from agents.threat.sources import AbuseIPDB, FetchResult, FeedHTTP, OTX

START = datetime(2026, 10, 19, tzinfo=timezone.utc)
END = datetime(2026, 10, 20, tzinfo=timezone.utc)


def test_abuse_snapshot_is_stable_and_does_not_claim_attacks_or_severity():
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"data": [
            {"ipAddress": "192.0.2.1", "countryCode": "HU", "lastReportedAt": "2026-10-19T12:00:00Z", "abuseConfidenceScore": 99},
            {"ipAddress": "192.0.2.2", "countryCode": "DE", "lastReportedAt": "2026-10-19T12:00:00Z", "abuseConfidenceScore": 90},
            {"ipAddress": "192.0.2.3", "countryCode": "ZZ", "lastReportedAt": "2026-10-19T12:00:00Z"},
        ]})
    source = AbuseIPDB(FeedHTTP(httpx.Client(transport=httpx.MockTransport(respond))), api_key="test-only")
    first = source.fetch(START, END, country="HU")
    assert first.events == source.fetch(START, END, country="HU").events
    assert len(first.events) == 1 and first.skipped == 1
    assert first.events[0]["threat_type"] == "abuse_report"
    assert "severity" not in first.events[0]
    assert all(request.method == "GET" for request in calls)


def test_otx_country_is_indicator_location_and_budget_cursor_resumes():
    def respond(request):
        if "/indicators/" in request.url.path:
            return httpx.Response(200, json={"country_code": "HU"})
        return httpx.Response(200, json={"results": [{"id": "pulse", "created": "2026-10-19T10:00:00",
            "targeted_countries": ["DE"], "tags": ["phishing"], "indicators": [
                {"id": "one", "type": "IPv4", "indicator": "192.0.2.1", "created": "2026-10-19T11:00:00"},
                {"id": "two", "type": "IPv4", "indicator": "192.0.2.2", "created": "2026-10-19T12:00:00"},
            ]}], "next": None})
    source = OTX(FeedHTTP(httpx.Client(transport=httpx.MockTransport(respond))), api_key="test-only", max_indicators=1)
    first = source.fetch(START, END, country="HU")
    assert not first.complete
    second = source.fetch(START, END, country="HU", cursor=first.cursor)
    assert second.complete
    assert first.events[0]["event_id"] != second.events[0]["event_id"]
    assert all(row["country"] == "HU" for row in first.events + second.events)
    with pytest.raises(ValueError, match="pagination"):
        source.fetch(START, END, cursor={"next_url": "https://evil.example/steal"})


def test_retry_is_bounded_and_does_not_retry_bad_credentials():
    attempts, delays = [], []
    def respond(request):
        attempts.append(request)
        return httpx.Response(429, headers={"Retry-After": "99999"})
    http = FeedHTTP(httpx.Client(transport=httpx.MockTransport(respond)), sleep=delays.append)
    with pytest.raises(httpx.HTTPStatusError):
        http.get("https://api.abuseipdb.com/api/v2/blacklist", headers={})
    assert len(attempts) == 4 and delays == [30, 30, 30]


def test_outbox_survives_ack_gap_and_deduplicates_after_restart(tmp_path):
    path = tmp_path / "collector.sqlite"
    store = CollectorStore(path)
    record = fixture_events(1)[0]
    store.save([record, record], "source", {"page": 2})
    sent = []
    publisher = Publisher(lambda *args: sent.append(args), "test")
    def fail():
        raise RuntimeError("lost acknowledgement")
    with pytest.raises(RuntimeError):
        store.drain(publisher, fail)
    store.close()
    reopened = CollectorStore(path)
    assert reopened.cursor("source") == {"page": 2}
    assert reopened.drain(publisher, lambda: None) == 1
    assert len(sent) == 2  # Same ID can be resent; S7 removes this replay.
    assert json.loads(sent[0][2])["event_id"] == json.loads(sent[1][2])["event_id"]
    reopened.save([record])
    assert reopened.drain(publisher, lambda: None) == 0
    reopened.close()


def test_tasks_filter_validate_bound_and_remember_completion(tmp_path):
    calls = []
    class FakeSource:
        name = "test"
        def fetch(self, since, until, **kwargs):
            calls.append((since, until, kwargs))
            return FetchResult([], {})
    store = CollectorStore(tmp_path / "state.sqlite")
    collector = ThreatCollector([FakeSource()], store, Publisher(lambda *args: None, "test"), lambda: None)
    task = {"task_id": str(uuid4()), "agent": "job-agent", "action": "fetch_job_ads", "params": {},
            "reason": "test", "decided_by": "rules", "created_at": "2026-10-20T00:00:00Z"}
    assert collector.handle_task(task) == 0 and calls == []
    task.update(agent="threat-agent", action="refresh_threats", params={"hours": 24, "max_pages": 2, "country": "HU"})
    collector.handle_task(task)
    collector.handle_task(task)
    assert len(calls) == 1
    assert (calls[0][1] - calls[0][0]).total_seconds() == 86400
    assert calls[0][2]["country"] == "HU" and calls[0][2]["max_pages"] == 2
    with pytest.raises(InvalidMessage):
        collector.handle_task({**task, "params": {"hours": 25}})
    with pytest.raises(InvalidMessage):
        collector.handle_task({**task, "params": {"country": "ZZ"}})
    store.close()

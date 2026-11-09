"""Bounded, read-only provider adapters with resumable partial batches."""
from __future__ import annotations

import ipaddress
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlencode, urlparse
from uuid import NAMESPACE_URL, uuid5

import httpx
from .utils import country_code, parse_time, utc_text

LOG = logging.getLogger(__name__)


def permanent_lookup_error(error: Exception) -> bool:
    """A bad lookup row must not block later rows in the same feed batch."""
    return (isinstance(error, httpx.HTTPStatusError)
            and 400 <= error.response.status_code < 500
            and error.response.status_code not in {408, 429})


@dataclass
class FetchResult:
    events: list[dict]
    cursor: dict
    skipped: int = 0
    complete: bool = True
    retry_error: Exception | None = None


class FeedHTTP:
    def __init__(self, client: httpx.Client | None = None, sleep: Callable = time.sleep):
        self.client = client or httpx.Client(timeout=httpx.Timeout(20, connect=5), follow_redirects=False)
        self.sleep = sleep

    def get(self, url: str, *, headers: dict, params: dict | None = None) -> dict:
        for attempt in range(4):
            try:
                response = self.client.get(url, headers=headers, params=params)
                # 429 is handled by the persistent per-feed scheduler, not a hot retry.
                if 500 <= response.status_code <= 599 and attempt < 3:
                    self.sleep(min(2 ** attempt, 8))
                    continue
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("feed response must be a JSON object")
                return payload
            except httpx.TransportError:
                if attempt == 3:
                    raise
                self.sleep(min(2 ** attempt, 8))

    def close(self):
        self.client.close()


def source_time(value: str, *, naive_utc: bool = False) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None and naive_utc:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parse_time(utc_text(parsed))


def event(source: str, identity: str, country: str, observed: datetime, kind: str) -> dict:
    return {"event_id": str(uuid5(NAMESPACE_URL, f"ace:{source}:{identity}")), "source": source,
            "country": country_code(country), "observed_at": utc_text(observed), "threat_type": kind}


class AbuseIPDB:
    name = "abuseipdb"
    snapshot_only = True  # The blacklist cannot replay historical windows.

    def __init__(self, http: FeedHTTP | None = None, api_key: str | None = None, limit: int = 100):
        self.http = http or FeedHTTP()
        self.api_key = api_key or os.environ.get("ABUSEIPDB_API_KEY")
        if not self.api_key:
            raise ValueError("ABUSEIPDB_API_KEY is required for this live source")
        if not 1 <= limit <= 1000:
            raise ValueError("AbuseIPDB limit must be between 1 and 1000")
        self.limit = limit

    def fetch(self, since: datetime, until: datetime, *, country: str | None = None,
              max_pages: int = 1, cursor: dict | None = None) -> FetchResult:
        if not 1 <= max_pages <= 10:
            raise ValueError("max_pages must be between 1 and 10")
        headers = {"Key": self.api_key, "Accept": "application/json"}
        cursor = cursor or {}
        if "pending_rows" in cursor:
            rows = cursor["pending_rows"]  # Keep the original snapshot; don't spend another blacklist call.
        else:
            payload = self.http.get("https://api.abuseipdb.com/api/v2/blacklist",
                headers=headers, params={"confidenceMinimum": 90, "limit": self.limit})
            rows = payload.get("data")
            if not isinstance(rows, list):
                raise ValueError("AbuseIPDB blacklist omitted data")
            rows = [{key: row[key] for key in ("ipAddress", "lastReportedAt", "countryCode") if key in row}
                    if isinstance(row, dict) else {} for row in rows[:self.limit]]
        output, skipped, geolocation = [], 0, {}
        for index, row in enumerate(rows):
            try:
                ip = str(ipaddress.ip_address(row["ipAddress"]))
                observed = source_time(row["lastReportedAt"])
                if not since <= observed <= until:
                    continue
                code = row.get("countryCode")
                if not code:
                    if ip not in geolocation:
                        details = self.http.get("https://api.abuseipdb.com/api/v2/check",
                            headers=headers, params={"ipAddress": ip, "maxAgeInDays": 1})
                        geolocation[ip] = country_code(details["data"]["countryCode"])
                    code = geolocation[ip]
                code = country_code(code)
                if country and country != code:
                    continue
                output.append(event(self.name, f"{ip}:{utc_text(observed)}", code, observed, "abuse_report"))
            except httpx.HTTPError as error:
                if permanent_lookup_error(error):
                    LOG.warning("feed=%s skipped lookup row after HTTP %s", self.name, error.response.status_code)
                    skipped += 1
                    continue
                return FetchResult(output, {"pending_rows": rows[index:],
                    "since": utc_text(since), "until": utc_text(until)}, skipped, False, error)
            except (KeyError, TypeError, ValueError):
                skipped += 1
        return FetchResult(output, {"fetched_through": utc_text(until)}, skipped, True)


class OTX:
    name = "otx"
    endpoint = "https://otx.alienvault.com/api/v1/pulses/subscribed"

    def __init__(self, http: FeedHTTP | None = None, api_key: str | None = None,
                 max_indicators: int = 100):
        self.http = http or FeedHTTP()
        self.api_key = api_key or os.environ.get("OTX_API_KEY")
        if not self.api_key:
            raise ValueError("OTX_API_KEY is required for this live source")
        if not 1 <= max_indicators <= 1000:
            raise ValueError("max_indicators must be between 1 and 1000")
        self.max_indicators = max_indicators

    @staticmethod
    def _safe_page(url: str):
        if not isinstance(url, str):
            raise ValueError("OTX returned a non-string pagination URL")
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc != "otx.alienvault.com" or parsed.path.rstrip("/") != "/api/v1/pulses/subscribed":
            raise ValueError("OTX returned an unexpected pagination URL")
        return url

    @staticmethod
    def _kind(pulse: dict):
        tags = " ".join(str(tag).lower() for tag in pulse.get("tags", []))
        for kind in ("phishing", "ransomware", "malware", "ddos", "scanning", "spam"):
            if kind in tags:
                return "malware" if kind == "ransomware" else kind
        return "indicator"

    def fetch(self, since: datetime, until: datetime, *, country: str | None = None,
              max_pages: int = 1, cursor: dict | None = None) -> FetchResult:
        if not 1 <= max_pages <= 10:
            raise ValueError("max_pages must be between 1 and 10")
        cursor = cursor or {}
        window = {"since": utc_text(since), "until": utc_text(until)}
        output, skipped, processed, geolocation = [], 0, 0, {}

        def closed_bad_page():
            LOG.error("feed=otx rejected pagination URL; closing this window without following it")
            return FetchResult(output, {"fetched_through": utc_text(until)}, skipped + 1)

        try:
            saved_url = cursor.get("page_url") or cursor.get("next_url")
            url = self._safe_page(saved_url or self.endpoint)
        except ValueError:
            return closed_bad_page()  # Also repairs previously persisted unsafe cursors.
        offset = max(0, int(cursor.get("indicator_offset", 0)))
        params = None if saved_url else {"modified_since": utc_text(since), "limit": 20}
        headers = {"X-OTX-API-KEY": self.api_key, "Accept": "application/json"}
        for page_index in range(max_pages):
            legacy = page_index == 0 and "pending_items" in cursor
            request_url = url + ("?" + urlencode(params) if params else "")
            if legacy:
                # Finish old snapshot cursors without losing their saved progress.
                flattened, next_url = cursor["pending_items"], cursor.get("next_url")
                offset = 0
            else:
                try:
                    page = self.http.get(url, headers=headers, params=params)
                    pulses = page.get("results")
                    if not isinstance(pulses, list):
                        raise ValueError("OTX response omitted results")
                except (httpx.HTTPError, ValueError) as error:
                    return FetchResult(output, {**window, "page_url": request_url,
                        "indicator_offset": offset}, skipped, False, error)
                flattened = []
                for pulse in pulses:
                    if not isinstance(pulse, dict) or not isinstance(pulse.get("indicators"), list):
                        skipped += 1
                        continue
                    metadata = {key: pulse[key] for key in ("id", "created", "tags") if key in pulse}
                    flattened.extend((metadata, row) for row in pulse["indicators"])
                next_url = page.get("next")
            # Validate before a URL can enter any persisted partial cursor.
            bad_next = False
            if next_url:
                try:
                    next_url = self._safe_page(next_url)
                except ValueError:
                    bad_next, next_url = True, None
            for index in range(offset, len(flattened)):
                pulse, row = flattened[index]
                pending = {**window, "page_url": request_url, "indicator_offset": index}
                if legacy:
                    pending = {**window, "pending_items": flattened[index:], "next_url": next_url}
                if processed >= self.max_indicators:
                    return closed_bad_page() if bad_next else FetchResult(output, pending, skipped, False)
                processed += 1
                try:
                    if not isinstance(row, dict):
                        raise ValueError("indicator must be an object")
                    if row.get("type") not in {"IPv4", "IPv6"}:
                        skipped += 1
                        continue
                    ip = str(ipaddress.ip_address(row["indicator"]))
                    observed = source_time(row.get("created") or pulse["created"], naive_utc=True)
                    if not since <= observed <= until:
                        continue
                    if ip not in geolocation:
                        family = "IPv4" if ipaddress.ip_address(ip).version == 4 else "IPv6"
                        details = self.http.get(f"https://otx.alienvault.com/api/v1/indicators/{family}/{ip}/general",
                                                headers=headers)
                        geolocation[ip] = country_code(details.get("country_code"))
                    code = geolocation[ip]
                    if country and country != code:
                        continue
                    identity = f"{pulse['id']}:{row.get('id', ip)}:{utc_text(observed)}"
                    output.append(event(self.name, identity, code, observed, self._kind(pulse)))
                except httpx.HTTPError as error:
                    if permanent_lookup_error(error):
                        LOG.warning("feed=%s skipped lookup row after HTTP %s", self.name, error.response.status_code)
                        skipped += 1
                        continue
                    return closed_bad_page() if bad_next else FetchResult(output, pending, skipped, False, error)
                except (KeyError, TypeError, ValueError):
                    skipped += 1
            if bad_next:
                return closed_bad_page()
            if not next_url:
                return FetchResult(output, {"fetched_through": utc_text(until)}, skipped, True)
            url, params, offset = next_url, None, 0
        return FetchResult(output, {**window, "page_url": url, "indicator_offset": 0}, skipped, False)

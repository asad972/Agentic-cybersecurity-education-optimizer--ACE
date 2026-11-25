"""Data models and contract constants for the ACE planning orchestrator."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

MAX_TASKS_PER_RUN = 5
DEFAULT_LOOKBACK_DAYS = 7

# European Skills Scope: EU27, EEA (IS, LI, NO), UK (GB), and CH.
SKILLS_SCOPE_COUNTRIES: frozenset[str] = frozenset(
    {
        "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR",
        "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL",
        "PL", "PT", "RO", "SK", "SI", "ES", "SE",
        "IS", "LI", "NO",
        "GB", "CH",
    }
)

# Live job-ad API countries configured in docs/ARCHITECTURE.md (Adzuna + Bundesagentur).
LIVE_JOB_COUNTRIES: tuple[str, ...] = (
    "AT", "BE", "CH", "DE", "ES", "FR", "GB", "IT", "NL", "PL",
)

# Priority countries checked for education programme coverage gaps.
DEFAULT_PROGRAMME_COUNTRIES: tuple[str, ...] = (
    "DE", "FR", "GB", "ES", "IT", "NL", "PL", "HU", "AT", "CH",
)

ALLOWED_AGENT_ACTIONS: dict[str, str] = {
    "job-agent": "fetch_job_ads",
    "education-agent": "refresh_programmes",
    "threat-agent": "refresh_threats",
    "platform-agent": "rerun_platform_analysis",
}

AgentName = Literal["job-agent", "education-agent", "threat-agent", "platform-agent"]
ActionName = Literal[
    "fetch_job_ads",
    "refresh_programmes",
    "refresh_threats",
    "rerun_platform_analysis",
]


class ProposedTask(BaseModel):
    """Structured task candidate returned by the LLM or rule planner."""

    agent: str = Field(description="Target live collection agent.")
    action: str = Field(description="Action from the fixed collect_task allow-list.")
    country: str | None = Field(
        default=None,
        description="Optional ISO 3166-1 alpha-2 country code (e.g. PL, DE).",
    )
    max_pages: int | None = Field(
        default=None,
        description="Number of job-ad API pages to fetch (1..10, job-agent only).",
    )
    hours: int | None = Field(
        default=None,
        description="Lookback window in hours (1..24, threat-agent only).",
    )
    reason: str = Field(
        description="One concise sentence explaining why this collection task is needed."
    )


class OrchestratorPlan(BaseModel):
    """Structured plan returned by LLMClient.generate."""

    tasks: list[ProposedTask] = Field(
        default_factory=list,
        description="Up to 5 prioritized collection tasks.",
    )


def _parse_iso_day(raw_value: Any) -> date | None:
    if raw_value is None:
        return None
    text = str(raw_value).strip()
    if len(text) < 10:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _is_within_window(raw_day: Any, cutoff_date: date | None) -> bool:
    if cutoff_date is None:
        return True
    parsed = _parse_iso_day(raw_day)
    if parsed is None:
        return True
    return parsed >= cutoff_date


@dataclass
class CoverageSnapshot:
    """Aggregated pipeline coverage read from Kafka summary and raw topics."""

    job_ads_by_country: dict[str, int] = field(default_factory=dict)
    programmes_by_country: dict[str, int] = field(default_factory=dict)
    threats_by_country: dict[str, int] = field(default_factory=dict)
    total_threats: int = 0
    invalid_by_topic: dict[str, int] = field(default_factory=dict)
    platform_analyses_count: int = 0

    @classmethod
    def from_messages(
        cls,
        records: Iterable[tuple[str, Mapping[str, Any]]],
        *,
        window_days: int = DEFAULT_LOOKBACK_DAYS,
        reference_date: date | None = None,
    ) -> CoverageSnapshot:
        """Build a CoverageSnapshot from (topic, payload) pairs within a recent day window."""
        snapshot = cls()
        ref = reference_date or datetime.now(UTC).date()
        cutoff_date = ref - timedelta(days=window_days) if window_days > 0 else None

        for topic, payload in records:
            if not isinstance(payload, Mapping):
                continue
            if topic == "agg.demand_daily":
                if not _is_within_window(payload.get("day"), cutoff_date):
                    continue
                country = str(payload.get("country") or "").strip().upper()
                if len(country) == 2 and country in SKILLS_SCOPE_COUNTRIES:
                    count = _extract_count(payload, default=1)
                    snapshot.job_ads_by_country[country] = (
                        snapshot.job_ads_by_country.get(country, 0) + count
                    )
            elif topic == "raw.programmes":
                country = str(payload.get("country") or "").strip().upper()
                if len(country) == 2 and country in SKILLS_SCOPE_COUNTRIES:
                    snapshot.programmes_by_country[country] = (
                        snapshot.programmes_by_country.get(country, 0) + 1
                    )
            elif topic == "agg.threats_daily":
                if not _is_within_window(payload.get("day"), cutoff_date):
                    continue
                country = str(payload.get("country") or "").strip().upper()
                count = _extract_threat_count(payload, default=1)
                snapshot.total_threats += count
                if len(country) == 2:
                    snapshot.threats_by_country[country] = (
                        snapshot.threats_by_country.get(country, 0) + count
                    )
            elif topic == "errors.invalid":
                if not _is_within_window(payload.get("detected_at"), cutoff_date):
                    continue
                src_topic = str(
                    payload.get("topic") or payload.get("source_topic") or "unknown"
                ).strip()
                snapshot.invalid_by_topic[src_topic] = (
                    snapshot.invalid_by_topic.get(src_topic, 0) + 1
                )
            elif topic == "analysis.platforms":
                if not _is_within_window(payload.get("analysed_at"), cutoff_date):
                    continue
                snapshot.platform_analyses_count += 1
        return snapshot

    def to_prompt_context(self) -> dict[str, Any]:
        """Return a compact, deterministic dictionary for LLM prompts."""
        return {
            "live_job_countries": list(LIVE_JOB_COUNTRIES),
            "job_ads_by_country": {
                c: self.job_ads_by_country.get(c, 0) for c in LIVE_JOB_COUNTRIES
            },
            "programmes_by_country": {
                c: self.programmes_by_country.get(c, 0)
                for c in DEFAULT_PROGRAMME_COUNTRIES
            },
            "total_threats": self.total_threats,
            "threats_by_country": dict(sorted(self.threats_by_country.items())),
            "invalid_by_topic": dict(sorted(self.invalid_by_topic.items())),
            "platform_analyses_count": self.platform_analyses_count,
        }


def _extract_threat_count(payload: Mapping[str, Any], *, default: int = 1) -> int:
    counts = payload.get("counts")
    if isinstance(counts, Mapping):
        total = 0
        for val in counts.values():
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                total += max(0, int(val))
        return total
    return _extract_count(payload, default=default)


def _extract_count(payload: Mapping[str, Any], *, default: int = 1) -> int:
    for key in ("job_ads", "count", "value", "total", "events"):
        val = payload.get(key)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return max(0, int(val))
    return default

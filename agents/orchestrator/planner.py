"""LLM-backed and deterministic rule-based task planner for ACE."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ace import contracts
from ace.llm import LLMClient, LLMError
from orchestrator.models import (
    ALLOWED_AGENT_ACTIONS,
    DEFAULT_PROGRAMME_COUNTRIES,
    LIVE_JOB_COUNTRIES,
    MAX_TASKS_PER_RUN,
    SKILLS_SCOPE_COUNTRIES,
    CoverageSnapshot,
    OrchestratorPlan,
    ProposedTask,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the ACE collection orchestrator for European cybersecurity labor-market intelligence.
Given current coverage counts across Kafka topics, choose up to 5 high-impact collection tasks for live data agents.
Rules:
1. Only use these exact (agent, action) pairs:
   - ("job-agent", "fetch_job_ads") with country in live_job_countries and max_pages between 1 and 10.
   - ("education-agent", "refresh_programmes") with country in the European Skills Scope (EU27, EEA, GB, CH).
   - ("threat-agent", "refresh_threats") with hours between 1 and 24 (and optional 2-letter country).
   - ("platform-agent", "rerun_platform_analysis") with no country, max_pages, or hours.
2. Never request static replay datasets (do not schedule profile-agent).
3. Prioritize countries with zero or very low coverage first.
4. Provide a single clear sentence (max 300 characters) in 'reason' for every task."""


def _utc_iso_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _clean_reason(raw_reason: str, fallback: str) -> str:
    text = " ".join(str(raw_reason or "").strip().split())
    if not text:
        text = fallback
    if len(text) > 300:
        text = text[:297].rstrip() + "..."
    return text


def sanitize_proposed_task(
    proposal: ProposedTask,
    *,
    decided_by: str,
    created_at: str,
    task_id_factory: Callable[[], str] | None = None,
) -> dict[str, Any] | None:
    """Validate and normalize a ProposedTask into a strict collect_task message.

    Returns None if the agent/action pair or parameters violate the contract.
    """
    agent = str(proposal.agent or "").strip()
    action = str(proposal.action or "").strip()

    expected_action = ALLOWED_AGENT_ACTIONS.get(agent)
    if expected_action is None or action != expected_action:
        logger.warning("Refusing disallowed task proposal: agent=%r action=%r", agent, action)
        return None

    params: dict[str, Any] = {}
    if agent == "job-agent":
        country = str(proposal.country or "").strip().upper()
        if country not in LIVE_JOB_COUNTRIES:
            logger.warning("Refusing job-agent task for unsupported country=%r", country)
            return None
        raw_pages = 3 if proposal.max_pages is None else int(proposal.max_pages)
        max_pages = max(1, min(10, raw_pages))
        params = {"country": country, "max_pages": max_pages}
        fallback_reason = f"Job ad coverage for {country} is below target; fetching up to {max_pages} pages."

    elif agent == "education-agent":
        country = str(proposal.country or "").strip().upper()
        if country not in SKILLS_SCOPE_COUNTRIES:
            logger.warning("Refusing education-agent task for unsupported country=%r", country)
            return None
        params = {"country": country}
        fallback_reason = f"Education programme coverage for {country} needs a refresh."

    elif agent == "threat-agent":
        raw_hours = 6 if proposal.hours is None else int(proposal.hours)
        hours = max(1, min(24, raw_hours))
        params = {"hours": hours}
        if proposal.country is not None and str(proposal.country).strip():
            country = str(proposal.country).strip().upper()
            if len(country) != 2 or not country.isalpha():
                logger.warning("Refusing threat-agent task with invalid country=%r", country)
                return None
            params["country"] = country
        fallback_reason = f"Threat telemetry coverage is low; refreshing last {hours} hours."

    elif agent == "platform-agent":
        params = {}
        fallback_reason = "Comparative platform analysis is stale or missing; rerunning analysis."

    else:
        return None

    task_id = task_id_factory() if task_id_factory is not None else str(uuid.uuid4())
    message: dict[str, Any] = {
        "task_id": task_id,
        "agent": agent,
        "action": action,
        "params": params,
        "reason": _clean_reason(proposal.reason, fallback_reason),
        "decided_by": decided_by,
        "created_at": created_at,
    }

    if contracts.check("tasks.collect", message) is not None:
        logger.warning("Refusing task that failed collect_task schema check: %r", message)
        return None
    return message


@dataclass
class OrchestratorPlanner:
    """Plans up to 5 collection tasks using ace.llm with deterministic rules fallback."""

    llm_client: LLMClient | None = None
    use_llm: bool = True
    max_tasks: int = MAX_TASKS_PER_RUN
    min_job_ads_per_country: int = 10
    min_programmes_per_country: int = 1
    min_daily_threats: int = 5
    prompt_version: str = "v1"
    clock: Callable[[], str] = field(default=_utc_iso_now)
    task_id_factory: Callable[[], str] = field(default=lambda: str(uuid.uuid4()))

    def plan_with_rules(self, snapshot: CoverageSnapshot) -> list[dict[str, Any]]:
        """Deterministically identify coverage gaps and build up to max_tasks tasks."""
        limit = max(1, min(MAX_TASKS_PER_RUN, self.max_tasks))
        now_iso = self.clock()
        proposals: list[ProposedTask] = []

        # 1. Prioritize live job-ad countries with lowest coverage first.
        ranked_job_countries = sorted(
            LIVE_JOB_COUNTRIES,
            key=lambda c: (snapshot.job_ads_by_country.get(c, 0), c),
        )
        for country in ranked_job_countries:
            count = snapshot.job_ads_by_country.get(country, 0)
            if count < self.min_job_ads_per_country:
                pages = 5 if count == 0 else 3
                proposals.append(
                    ProposedTask(
                        agent="job-agent",
                        action="fetch_job_ads",
                        country=country,
                        max_pages=pages,
                        reason=(
                            f"Country {country} has only {count} recorded job ads, "
                            f"below the target threshold of {self.min_job_ads_per_country}."
                        ),
                    )
                )

        # 2. Check daily threat telemetry coverage.
        if snapshot.total_threats < self.min_daily_threats:
            proposals.append(
                ProposedTask(
                    agent="threat-agent",
                    action="refresh_threats",
                    hours=6,
                    reason=(
                        f"Daily threat telemetry has {snapshot.total_threats} events, "
                        f"below the minimum threshold of {self.min_daily_threats}."
                    ),
                )
            )

        # 3. Check education programme coverage across priority European countries.
        ranked_prog_countries = sorted(
            DEFAULT_PROGRAMME_COUNTRIES,
            key=lambda c: (snapshot.programmes_by_country.get(c, 0), c),
        )
        for country in ranked_prog_countries:
            count = snapshot.programmes_by_country.get(country, 0)
            if count < self.min_programmes_per_country:
                proposals.append(
                    ProposedTask(
                        agent="education-agent",
                        action="refresh_programmes",
                        country=country,
                        reason=(
                            f"Country {country} has {count} cybersecurity programmes indexed "
                            f"in raw.programmes."
                        ),
                    )
                )

        # 4. Ensure at least one comparative platform analysis exists.
        if snapshot.platform_analyses_count == 0:
            proposals.append(
                ProposedTask(
                    agent="platform-agent",
                    action="rerun_platform_analysis",
                    reason="No comparative platform analysis record exists yet; triggering baseline run.",
                )
            )

        return self._finalize_proposals(proposals, decided_by="rules", created_at=now_iso, limit=limit)

    def plan(self, snapshot: CoverageSnapshot) -> list[dict[str, Any]]:
        """Plan tasks with LLMClient if enabled, falling back to deterministic rules on failure."""
        limit = max(1, min(MAX_TASKS_PER_RUN, self.max_tasks))
        if self.use_llm and self.llm_client is not None:
            try:
                prompt = (
                    "Current ACE pipeline coverage snapshot (JSON):\n"
                    + json.dumps(snapshot.to_prompt_context(), indent=2, sort_keys=True)
                    + f"\n\nSelect up to {limit} prioritized collection tasks."
                )
                result = self.llm_client.generate(
                    task="orchestrator_plan",
                    prompt_version=self.prompt_version,
                    system_prompt=SYSTEM_PROMPT,
                    prompt=prompt,
                    response_model=OrchestratorPlan,
                )
                now_iso = self.clock()
                validated = self._finalize_proposals(
                    result.value.tasks,
                    decided_by="llm",
                    created_at=now_iso,
                    limit=limit,
                )
                if validated or not result.value.tasks:
                    return validated
                logger.warning(
                    "LLM returned %d task(s) but none passed contract validation; falling back to rules.",
                    len(result.value.tasks),
                )
            except LLMError as exc:
                logger.warning("LLM planning unavailable (%s); falling back to fixed rules.", exc)

        return self.plan_with_rules(snapshot)

    def _finalize_proposals(
        self,
        proposals: list[ProposedTask],
        *,
        decided_by: str,
        created_at: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []
        seen_keys: set[tuple[str, str, str]] = set()
        for proposal in proposals:
            if len(tasks) >= limit:
                break
            task = sanitize_proposed_task(
                proposal,
                decided_by=decided_by,
                created_at=created_at,
                task_id_factory=self.task_id_factory,
            )
            if task is None:
                continue
            dedup_key = (
                task["agent"],
                task["action"],
                str(task["params"].get("country") or ""),
            )
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)
            tasks.append(task)
        return tasks

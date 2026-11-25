"""S2: Skill Extraction stage mapping jobs.clean and raw.profiles to the Skill Taxonomy."""

from __future__ import annotations

from collections.abc import Callable, Iterator
import logging
from pathlib import Path
from typing import Any

from ace import contracts
from ace.llm import LLMClient
from ace.stage import RecordStage
from ace.taxonomy import ExtractionResult, Taxonomy, load_taxonomy

from .llm_extractor import LLMSkillExtractor, llm_enabled_from_env

log = logging.getLogger(__name__)

JOBS_CLEAN = contracts.topic("jobs.clean").name
RAW_PROFILES = contracts.topic("raw.profiles").name
SKILLS_DEMAND = contracts.topic("skills.demand").name
SKILLS_SUPPLY = contracts.topic("skills.supply").name

UNMAPPED_METRIC = "unmapped_skills_total"
FALLBACK_METRIC = "llm_fallbacks_total"

ExtractorFn = Callable[[str, dict[str, Any], Taxonomy], ExtractionResult | None]


class S2SkillExtractionStage(RecordStage):
    """Extract canonical Skills, Certifications, and ECSF role from job ads and profiles.

    Consumes ``jobs.clean`` -> ``skills.demand`` and ``raw.profiles`` -> ``skills.supply``.
    Combines deterministic boundary matching against ``ace.taxonomy`` with LLM-assisted
    extraction via ``ace.llm`` (enabled by default; disable with ``ACE_S2_USE_LLM=0`` or
    ``use_llm=False``) to surface emerging unmapped terms for S9.
    """

    name = "s2-skill-extraction"
    inputs = (JOBS_CLEAN, RAW_PROFILES)

    def __init__(
        self,
        taxonomy: Taxonomy | Path | str | None = None,
        extractor: ExtractorFn | None = None,
        *,
        llm_client: LLMClient | None = None,
        use_llm: bool | None = None,
    ) -> None:
        if isinstance(taxonomy, Taxonomy):
            self.taxonomy = taxonomy
        else:
            self.taxonomy = load_taxonomy(taxonomy)

        self._use_llm = llm_enabled_from_env() if use_llm is None else bool(use_llm)

        if extractor is not None:
            self.extractor: ExtractorFn | None = extractor
            self._injected_extractor = True
        elif llm_client is not None:
            self.extractor = LLMSkillExtractor(llm_client)
            self._injected_extractor = True
        else:
            # Deferred to open() so the stage stays picklable when Flink ships the job graph.
            self.extractor = None
            self._injected_extractor = False

        self._unmapped_counter: Any = None
        self._fallback_counter: Any = None

    def __getstate__(self) -> dict[str, Any]:
        state = self.__dict__.copy()
        state["_unmapped_counter"] = None
        state["_fallback_counter"] = None
        # Env-built extractors hold live Cassandra/HTTP connections; rebuild them in open().
        if not self._injected_extractor:
            state["extractor"] = None
        return state

    def _record_unmapped(self, count: int) -> None:
        if count > 0 and self._unmapped_counter is not None:
            self._unmapped_counter.inc(count)

    def _record_fallback(self) -> None:
        if self._fallback_counter is not None:
            self._fallback_counter.inc(1)

    def _ensure_extractor(self) -> None:
        """Lazily build LLMSkillExtractor from env, raising visibly on setup errors."""
        if self.extractor is not None or not self._use_llm:
            return
        self.extractor = LLMSkillExtractor.from_env()

    def open(self, runtime_context: Any) -> None:
        """Initialize runtime metrics and env-configured LLM client inside each Flink task."""
        if runtime_context is not None:
            metrics = runtime_context.get_metrics_group()
            self._unmapped_counter = metrics.counter(UNMAPPED_METRIC)
            self._fallback_counter = metrics.counter(FALLBACK_METRIC)
        self._ensure_extractor()

    def close(self) -> None:
        """Close any underlying LLM client resources."""
        closer = getattr(self.extractor, "close", None)
        if callable(closer):
            closer()

    def _extract(self, topic: str, record: dict[str, Any]) -> ExtractionResult:
        self._ensure_extractor()

        if self.extractor is not None:
            custom = self.extractor(topic, record, self.taxonomy)
            if getattr(self.extractor, "last_call_fell_back", False):
                self._record_fallback()
            if custom is not None:
                return custom

        if topic == JOBS_CLEAN:
            title = str(record.get("job_title") or "")
            text = str(record.get("description") or "")
        else:
            title = str(record.get("headline") or "")
            text = str(record.get("text") or "")

        return self.taxonomy.extract_text(text, title=title)

    def process(
        self, topic: str, record: dict[str, Any], memory: dict[str, Any]
    ) -> Iterator[tuple[str, dict[str, Any]]]:
        """Extract taxonomy items and emit a skills.demand or skills.supply message."""
        extraction = self._extract(topic, record)
        self._record_unmapped(len(extraction.unmapped))

        # Shared contracts (skills_demand / skills_supply) currently require skills minItems >= 1.
        if not extraction.skills:
            return

        if topic == JOBS_CLEAN:
            out: dict[str, Any] = {
                "event_id": record["event_id"],
                "source": record["source"],
                "country": record["country"],
                "posted_date": record["posted_date"],
                "skills": list(extraction.skills),
                "certifications": list(extraction.certifications),
                "unmapped": list(extraction.unmapped),
            }
            job_title = record.get("job_title")
            if isinstance(job_title, str) and job_title.strip():
                out["job_title"] = job_title
            if extraction.ecsf_role:
                out["ecsf_role"] = extraction.ecsf_role
            yield (SKILLS_DEMAND, out)
            return

        if topic == RAW_PROFILES:
            supply_out: dict[str, Any] = {
                "event_id": record["event_id"],
                "source": record["source"],
                "country": record["country"],
                "profile_date": record["profile_date"],
                "skills": list(extraction.skills),
                "certifications": list(extraction.certifications),
                "unmapped": list(extraction.unmapped),
            }
            if extraction.ecsf_role:
                supply_out["ecsf_role"] = extraction.ecsf_role
            yield (SKILLS_SUPPLY, supply_out)

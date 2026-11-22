"""S2: Skill Extraction stage mapping jobs.clean and raw.profiles to the Skill Taxonomy."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from ace import contracts
from ace.llm import LLMClient
from ace.stage import RecordStage
from ace.taxonomy import ExtractionResult, Taxonomy, load_taxonomy

from .llm_extractor import LLMSkillExtractor, llm_enabled_from_env

JOBS_CLEAN = contracts.topic("jobs.clean").name
RAW_PROFILES = contracts.topic("raw.profiles").name
SKILLS_DEMAND = contracts.topic("skills.demand").name
SKILLS_SUPPLY = contracts.topic("skills.supply").name

UNMAPPED_METRIC = "unmapped_skills_total"

ExtractorFn = Callable[[str, dict[str, Any], Taxonomy], ExtractionResult | None]


class S2SkillExtractionStage(RecordStage):
    """Extract canonical Skills, Certifications, and ECSF role from job ads and profiles.

    Consumes ``jobs.clean`` -> ``skills.demand`` and ``raw.profiles`` -> ``skills.supply``.
    Supports deterministic boundary matching against ``ace.taxonomy`` as well as
    LLM-assisted extraction via ``ace.llm`` (enabled via ``llm_client``, ``use_llm=True``,
    or ``ACE_S2_USE_LLM=1``) to surface emerging unmapped terms for S9.
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
        elif llm_client is not None:
            self.extractor = LLMSkillExtractor(llm_client)
        else:
            # Deferred to open() so the stage stays picklable when Flink ships the job graph.
            self.extractor = None

        self._unmapped_counter: Any = None

    def open(self, runtime_context: Any) -> None:
        """Initialize runtime metrics and env-configured LLM client inside each Flink task."""
        if runtime_context is not None:
            self._unmapped_counter = runtime_context.get_metrics_group().counter(
                UNMAPPED_METRIC
            )
        if self.extractor is None and self._use_llm:
            self.extractor = LLMSkillExtractor.from_env()

    def close(self) -> None:
        """Close any underlying LLM client resources."""
        closer = getattr(self.extractor, "close", None)
        if callable(closer):
            closer()

    def _record_unmapped(self, count: int) -> None:
        if count > 0 and self._unmapped_counter is not None:
            self._unmapped_counter.inc(count)

    def _extract(self, topic: str, record: dict[str, Any]) -> ExtractionResult:
        # Support run_local() where Flink's open() lifecycle hook is not called.
        if self.extractor is None and self._use_llm:
            self.extractor = LLMSkillExtractor.from_env()

        if self.extractor is not None:
            custom = self.extractor(topic, record, self.taxonomy)
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

        # Both skills.demand and skills.supply require skills minItems >= 1
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
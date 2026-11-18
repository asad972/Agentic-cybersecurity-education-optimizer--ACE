"""S2: Skill Extraction stage mapping jobs.clean and raw.profiles to the Skill Taxonomy."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

from ace import contracts
from ace.stage import RecordStage
from ace.taxonomy import ExtractionResult, Taxonomy, load_taxonomy

JOBS_CLEAN = contracts.topic("jobs.clean").name
RAW_PROFILES = contracts.topic("raw.profiles").name
SKILLS_DEMAND = contracts.topic("skills.demand").name
SKILLS_SUPPLY = contracts.topic("skills.supply").name

UNMAPPED_METRIC = "unmapped_skills_total"

ExtractorFn = Callable[[str, dict[str, Any], Taxonomy], ExtractionResult | None]


class S2SkillExtractionStage(RecordStage):
    """Extract canonical Skills, Certifications, and ECSF role from job ads and profiles.

    Consumes ``jobs.clean`` -> ``skills.demand`` and ``raw.profiles`` -> ``skills.supply``.
    Matches mentions by exact name and alias against ``ace.taxonomy`` (with an optional
    extractor hook for LLM-assisted candidate extraction). Records with no matched
    taxonomy skills are skipped because downstream schemas require at least one skill.
    """

    name = "s2-skill-extraction"
    inputs = (JOBS_CLEAN, RAW_PROFILES)

    def __init__(
        self,
        taxonomy: Taxonomy | Path | str | None = None,
        extractor: ExtractorFn | None = None,
    ) -> None:
        if isinstance(taxonomy, Taxonomy):
            self.taxonomy = taxonomy
        else:
            self.taxonomy = load_taxonomy(taxonomy)
        self.extractor = extractor
        self.unmapped_count = 0
        self._unmapped_counter: Any = None

    def open(self, runtime_context: Any) -> None:
        """Register Flink metric counter for unmapped skill mentions."""
        self._unmapped_counter = runtime_context.get_metrics_group().counter(
            UNMAPPED_METRIC
        )

    def _record_unmapped(self, count: int, memory: dict[str, Any]) -> None:
        if count <= 0:
            return
        memory["unmapped_count"] = int(memory.get("unmapped_count", 0)) + count
        self.unmapped_count += count
        if self._unmapped_counter is not None:
            for _ in range(count):
                self._unmapped_counter.inc()

    def _extract(self, topic: str, record: dict[str, Any]) -> ExtractionResult:
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
        self._record_unmapped(len(extraction.unmapped), memory)

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
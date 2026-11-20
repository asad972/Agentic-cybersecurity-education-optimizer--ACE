"""LLM-backed skill, certification, and emerging-term extractor for S2 (#12)."""

from __future__ import annotations

from collections.abc import Mapping
import logging
import os
from typing import Any

from pydantic import BaseModel, Field

from ace.llm import LLMClient, LLMError, LLMStore, Provider
from ace.taxonomy import ExtractionResult, Taxonomy

log = logging.getLogger(__name__)

TASK_NAME = "skill_extraction"
PROMPT_VERSION = "v1"
DEFAULT_MAX_TOKENS = 512

SYSTEM_PROMPT = (
    "You extract cybersecurity skills, tools, standards, frameworks, and certifications "
    "from job advertisements and practitioner profiles. Return concise, canonical-style "
    "phrases explicitly supported by the text (do not invent skills) and identify the "
    "closest ENISA ECSF role profile when clear."
)


class ExtractedMentions(BaseModel):
    """Structured output returned by ace.llm for S2 skill extraction."""

    mentions: list[str] = Field(
        default_factory=list,
        description=(
            "Cybersecurity skills, technologies, frameworks, standards, and "
            "certifications mentioned in the record."
        ),
    )
    ecsf_role: str | None = Field(
        default=None,
        description="Closest ENISA ECSF role profile if identifiable from the text.",
    )


def build_extraction_prompt(record_kind: str, *, title: str, text: str) -> str:
    """Build a deterministic prompt for S2 skill extraction."""
    clean_title = title.strip() or "(none)"
    clean_text = text.strip() or "(none)"
    return (
        f"Record type: {record_kind}\n"
        f"Title: {clean_title}\n"
        f"Text:\n{clean_text}"
    )


def llm_enabled_from_env(environ: Mapping[str, str] | None = None) -> bool:
    """Return True when ACE_S2_USE_LLM enables the shared ace.llm extractor."""
    env = os.environ if environ is None else environ
    raw = env.get("ACE_S2_USE_LLM", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


class LLMSkillExtractor:
    """Extract canonical taxonomy items and emerging unmapped mentions via ace.llm."""

    def __init__(
        self,
        client: LLMClient,
        *,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        fallback_to_taxonomy: bool = True,
    ) -> None:
        self.client = client
        self.max_tokens = max_tokens
        self.fallback_to_taxonomy = fallback_to_taxonomy

    @classmethod
    def from_env(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        store: LLMStore | None = None,
        provider: Provider | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        fallback_to_taxonomy: bool = True,
    ) -> LLMSkillExtractor:
        client = LLMClient.from_env(environ=environ, store=store, provider=provider)
        return cls(
            client,
            max_tokens=max_tokens,
            fallback_to_taxonomy=fallback_to_taxonomy,
        )

    def extract(
        self,
        topic: str,
        record: dict[str, Any],
        taxonomy: Taxonomy,
    ) -> ExtractionResult:
        if topic == "jobs.clean":
            record_kind = "job_ad"
            title = str(record.get("job_title") or "")
            text = str(record.get("description") or "")
        else:
            record_kind = "profile"
            title = str(record.get("headline") or "")
            text = str(record.get("text") or "")

        baseline = taxonomy.extract_text(text, title=title)
        if not title.strip() and not text.strip():
            return baseline

        prompt = build_extraction_prompt(record_kind, title=title, text=text)
        try:
            result = self.client.generate(
                task=TASK_NAME,
                prompt_version=PROMPT_VERSION,
                system_prompt=SYSTEM_PROMPT,
                prompt=prompt,
                response_model=ExtractedMentions,
                max_tokens=self.max_tokens,
            )
        except LLMError as error:
            if not self.fallback_to_taxonomy:
                raise
            log.warning(
                "S2 LLM extraction failed (%s); falling back to taxonomy matcher",
                type(error).__name__,
            )
            return baseline

        llm_classified = taxonomy.classify_mentions(
            result.value.mentions,
            title=title,
        )

        skills = sorted(set(baseline.skills) | set(llm_classified.skills))
        certifications = sorted(
            set(baseline.certifications) | set(llm_classified.certifications)
        )

        unmapped: list[str] = []
        seen_unmapped: set[str] = set()
        for candidate in llm_classified.unmapped:
            key = candidate.casefold()
            if key in seen_unmapped:
                continue
            if taxonomy.match(candidate) is not None:
                continue
            seen_unmapped.add(key)
            unmapped.append(candidate)

        ecsf_role = baseline.ecsf_role or llm_classified.ecsf_role
        if ecsf_role is None and result.value.ecsf_role:
            role_match = taxonomy.match(result.value.ecsf_role)
            if role_match is not None and role_match.kind == "role":
                ecsf_role = role_match.canonical

        return ExtractionResult(
            skills=skills,
            certifications=certifications,
            ecsf_role=ecsf_role,
            unmapped=unmapped,
        )

    def __call__(
        self,
        topic: str,
        record: dict[str, Any],
        taxonomy: Taxonomy,
    ) -> ExtractionResult:
        return self.extract(topic, record, taxonomy)

    def close(self) -> None:
        self.client.close()
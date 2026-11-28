"""Recover the model's canonical Skill choices before S2's baseline union."""

from typing import Protocol

from ace.llm import CacheRecord, request_hash
from ace.taxonomy import Taxonomy
from s2_skill_extraction.llm_extractor import (
    DEFAULT_MAX_TOKENS,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    TASK_NAME,
    ExtractedMentions,
    _filter_mentions,
    build_extraction_prompt,
)

from .benchmark import Ad


class AnswerCache(Protocol):
    def get_cache(self, task: str, input_hash: str) -> CacheRecord | None: ...


def read_model_skills(
    ad: Ad, taxonomy: Taxonomy, cache: AnswerCache, *, mode: str, model: str
) -> list[str]:
    prompt = build_extraction_prompt(
        "job_ad", title=ad.title, text=ad.description, roles=taxonomy.roles
    )
    digest = request_hash(
        task=TASK_NAME,
        prompt_version=PROMPT_VERSION,
        provider=mode,
        model=model,
        system_prompt=SYSTEM_PROMPT,
        prompt=prompt,
        response_model=ExtractedMentions,
        max_tokens=DEFAULT_MAX_TOKENS,
    )
    cached = cache.get_cache(TASK_NAME, digest)
    if cached is None:
        raise ValueError(f"{mode} {ad.id}: no cached answer")
    if (cached.provider, cached.model) != (mode, model):
        raise ValueError(f"{mode} {ad.id}: cached provider or model differs")

    try:
        answer = ExtractedMentions.model_validate_json(cached.answer)
    except ValueError:
        raise ValueError(f"{mode} {ad.id}: cached answer does not match S2's schema") from None
    baseline = taxonomy.extract_text(ad.description, title=ad.title)
    source = f"{ad.title}\n{ad.description}".strip()
    mentions = _filter_mentions(answer.mentions, source, taxonomy, baseline)
    model_labels = taxonomy.classify_mentions(
        mentions, explicit_role=answer.ecsf_role, title=ad.title
    )
    return sorted(set(model_labels.skills))

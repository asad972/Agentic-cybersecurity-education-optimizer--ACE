"""Corpus validation and paired, ad-level extraction metrics."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
from random import Random
from typing import Any

from ace.taxonomy import Taxonomy


LANGUAGES = {"en": 20, "de": 15, "fr": 15}
FIELDS = ("skills", "certifications", "unmapped")
MODES = ("baseline", "local", "hosted")


@dataclass(frozen=True)
class Ad:
    id: str
    language: str
    title: str
    description: str
    expected: dict[str, Any]


def normalize(phrase: str) -> str:
    return " ".join(phrase.split()).casefold()


def validate_labels(labels: Any, taxonomy: Taxonomy, *, gold: bool = False) -> None:
    if not isinstance(labels, dict) or labels.keys() != {*FIELDS, "role"}:
        raise ValueError("expected Skills, Certifications, unmapped phrases, and role")
    for field in FIELDS:
        values = labels[field]
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not normalize(value) for value in values
        ):
            raise ValueError(f"{field} must be a list of nonblank strings")
        if gold and len({normalize(value) for value in values}) != len(values):
            raise ValueError(f"duplicate gold labels in {field}")
    if set(labels["skills"]) - set(taxonomy.skills):
        raise ValueError("noncanonical Skill label")
    if set(labels["certifications"]) - set(taxonomy.certifications):
        raise ValueError("noncanonical Certification label")
    if labels["role"] is not None and labels["role"] not in taxonomy.roles:
        raise ValueError("noncanonical ECSF role label")
    if gold and any(taxonomy.match(phrase) is not None for phrase in labels["unmapped"]):
        raise ValueError("gold unmapped phrase resolves in the taxonomy")


def read_corpus(path: Path, taxonomy: Taxonomy) -> list[Ad]:
    ads = []
    ids: set[str] = set()
    texts: set[tuple[str, str]] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            ad = Ad(**json.loads(line))
            if any(
                not isinstance(value, str) or not value.strip()
                for value in (ad.id, ad.language, ad.title, ad.description)
            ):
                raise ValueError("ad fields must be nonblank strings")
            if ad.language not in LANGUAGES or not ad.id.startswith(ad.language + "-"):
                raise ValueError("invalid language or ID")
            text = (normalize(ad.title), normalize(ad.description))
            if ad.id in ids or text in texts:
                raise ValueError("duplicate ID or ad text")
            validate_labels(ad.expected, taxonomy, gold=True)
            normalized_text = normalize(f"{ad.title} {ad.description}")
            if any(normalize(phrase) not in normalized_text for phrase in ad.expected["unmapped"]):
                raise ValueError("gold unmapped phrase must appear in the ad")
        except (ValueError, TypeError) as error:
            raise ValueError(f"invalid corpus row {line_number}: {error}") from error
        ads.append(ad)
        ids.add(ad.id)
        texts.add(text)
    if Counter(ad.language for ad in ads) != LANGUAGES:
        raise ValueError("expected 20 English, 15 German and 15 French ads")
    for language in LANGUAGES:
        if sum(ad.language == language and not ad.expected["skills"] for ad in ads) < 2:
            raise ValueError(f"need at least two ads with no canonical Skills in {language}")
    return ads


Counts = tuple[int, int, int]


def label_counts(ad: Ad, prediction: dict[str, Any], field: str) -> Counts:
    gold = set(ad.expected[field])
    found = set(prediction[field])
    if field == "unmapped":
        gold = {normalize(phrase) for phrase in gold}
        found = {normalize(phrase) for phrase in found}
    return len(gold & found), len(found - gold), len(gold - found)


def combine(counts: list[Counts]) -> Counts:
    return (
        sum(tp for tp, _, _ in counts),
        sum(fp for _, fp, _ in counts),
        sum(fn for _, _, fn in counts),
    )


def ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def metrics(counts: Counts) -> dict[str, Any]:
    tp, fp, fn = counts
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": ratio(tp, tp + fp),
        "recall": ratio(tp, tp + fn),
        "f1": ratio(2 * tp, 2 * tp + fp + fn),
    }


def score(ads: list[Ad], predictions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    result = {
        field: metrics(combine([label_counts(ad, predictions[ad.id], field) for ad in ads]))
        for field in FIELDS
    }
    correct_roles = sum(ad.expected["role"] == predictions[ad.id]["role"] for ad in ads)
    return {
        "ads": len(ads),
        **result,
        "role": {"correct": correct_roles, "accuracy": ratio(correct_roles, len(ads))},
    }


def paired_interval(
    ads: list[Ad],
    local: dict[str, dict[str, Any]],
    hosted: dict[str, dict[str, Any]],
    *,
    iterations: int = 10_000,
) -> dict[str, Any]:
    strata = [
        [
            (label_counts(ad, local[ad.id], "skills"), label_counts(ad, hosted[ad.id], "skills"))
            for ad in ads if ad.language == language
        ]
        for language in LANGUAGES
    ]
    rng = Random(30)
    differences = []
    for _ in range(iterations):
        paired = [pair for stratum in strata for pair in rng.choices(stratum, k=len(stratum))]
        local_counts = combine([pair[0] for pair in paired])
        hosted_counts = combine([pair[1] for pair in paired])
        local_f1 = metrics(local_counts)["f1"]
        hosted_f1 = metrics(hosted_counts)["f1"]
        if local_f1 is not None and hosted_f1 is not None:
            differences.append(hosted_f1 - local_f1)
    differences.sort()
    lower, upper = None, None
    if differences:
        lower = differences[int(0.025 * (len(differences) - 1))]
        upper = differences[int(0.975 * (len(differences) - 1))]
    return {"lower": lower, "upper": upper, "valid_draws": len(differences), "seed": 30}


def recommend(gain: float, lower: float | None, language_gains: dict[str, float]) -> str:
    if round(gain, 12) >= 0.05 and lower is not None and lower > 0 and all(
        round(language_gains[language], 12) >= -0.05 for language in ("de", "fr")
    ):
        return "hosted"
    return "local"

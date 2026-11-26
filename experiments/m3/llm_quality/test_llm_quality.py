from dataclasses import replace
from decimal import Decimal
import json
from unittest.mock import Mock

import pytest

from ace.llm import BudgetExceededError, LLMProviderError, LLMResult
from ace.taxonomy import load_taxonomy
from s2_skill_extraction.llm_extractor import ExtractedMentions

from experiments.m3.llm_quality.__main__ import CORPUS, ROOT, evaluate, summarize, write_json
from experiments.m3.llm_quality.benchmark import (
    LANGUAGES, label_counts, metrics, paired_interval, read_corpus, recommend,
)


@pytest.fixture
def taxonomy():
    return load_taxonomy(ROOT / "shared/taxonomy")


@pytest.fixture
def ads(taxonomy):
    return read_corpus(CORPUS, taxonomy)


def checkpoint(mode):
    return {"study": {"revision": "fixture"}, "mode": mode, "model": None, "rows": {}}


def answer():
    return LLMResult(ExtractedMentions(), "local", "qwen3:4b", False, 120, 8, Decimal(0))


def test_corpus_has_required_languages_and_negative_ads(ads):
    assert {lang: sum(ad.language == lang for ad in ads) for lang in LANGUAGES} == LANGUAGES
    for lang in LANGUAGES:
        assert sum(ad.language == lang and not ad.expected["skills"] for ad in ads) >= 2


@pytest.mark.parametrize("invalid", ["noncanonical Skill", "duplicate ID", "must appear"])
def test_corpus_rejects_bad_gold(tmp_path, taxonomy, invalid):
    rows = [json.loads(line) for line in CORPUS.read_text().splitlines()]
    if invalid == "noncanonical Skill":
        rows[0]["expected"]["skills"] = ["not a canonical Skill"]
    elif invalid == "duplicate ID":
        rows[1]["id"] = rows[0]["id"]
    else:
        rows[0]["expected"]["unmapped"] = ["phrase absent from the article"]
    path = tmp_path / "invalid.jsonl"
    path.write_text("\n".join(map(json.dumps, rows)))
    with pytest.raises(ValueError, match=invalid):
        read_corpus(path, taxonomy)


def test_ad_counts_deduplicate_predictions_and_normalize_novel_phrases(ads):
    ad = ads[0]
    predicted = {**ad.expected, "skills": ["Splunk", "Splunk", "Linux"]}
    predicted["unmapped"] = ["  ALERT   deduplication   "]
    assert label_counts(ad, predicted, "skills") == (1, 1, 2)
    assert label_counts(ad, predicted, "unmapped") == (1, 0, 0)
    assert metrics((1, 1, 2))["f1"] == pytest.approx(0.4)
    assert metrics((0, 0, 1))["precision"] is None
    assert metrics((0, 0, 0))["f1"] is None


def test_paired_interval_is_reproducible_and_uses_ad_pairs(ads):
    local = {ad.id: {**ad.expected, "skills": []} for ad in ads}
    hosted = {ad.id: ad.expected for ad in ads}
    interval = paired_interval(ads, local, hosted, iterations=50)
    assert interval == paired_interval(ads, local, hosted, iterations=50)
    assert interval == {"lower": 1.0, "upper": 1.0, "valid_draws": 50, "seed": 30}
    assert recommend(0.05, 0.01, {"de": -0.05, "fr": 0}) == "hosted"
    assert recommend(0.05, 0.01, {"de": 0, "fr": -0.06}) == "local"
    assert recommend(0.08, 0, {"de": 0, "fr": 0}) == "local"


def test_live_failure_is_not_scored_and_resume_skips_successes(ads, taxonomy, tmp_path):
    checkpoint_data = checkpoint("local")
    path = tmp_path / "local.json"
    client = Mock()
    client.generate.side_effect = [LLMProviderError("provider unavailable"), answer()]
    persist = lambda value: write_json(path, value)
    assert not evaluate(ads[:2], taxonomy, checkpoint_data, persist, client)
    resumed = json.loads(path.read_text())
    assert resumed["rows"][ads[0].id] == {
        "attempts": 1, "status": "error", "errors": ["LLMProviderError"],
    }
    client.generate.reset_mock(side_effect=True)
    client.generate.return_value = replace(answer(), cached=True)
    assert evaluate(ads[:2], taxonomy, resumed, persist, client)
    client.generate.assert_called_once()
    assert resumed["rows"][ads[0].id]["usage"]["cached"]
    assert resumed["rows"][ads[0].id]["errors"] == ["LLMProviderError"]


def test_hosted_budget_failure_stops_the_run(ads, taxonomy):
    client = Mock()
    client.store.budget_snapshot.return_value = None
    client.generate.side_effect = BudgetExceededError("daily cap reached")
    data = checkpoint("hosted")
    assert not evaluate(ads[:2], taxonomy, data, Mock(), client)
    client.generate.assert_called_once()
    assert data["rows"][ads[0].id]["errors"] == ["BudgetExceededError"]
    assert ads[1].id not in data["rows"]


def test_partial_summary_scores_only_common_ads_and_makes_no_recommendation(ads, taxonomy):
    baseline = checkpoint("baseline")
    assert evaluate(ads, taxonomy, baseline, Mock())
    local = checkpoint("local")
    ad = ads[0]
    local["rows"][ad.id] = {"status": "ok", "labels": ad.expected, "errors": []}
    result = summarize(ads, {"baseline": baseline, "local": local})
    assert result["paired_ads"] == 1
    assert result["scores_on_common_ads"]["baseline"]["overall"]["ads"] == 1
    assert result["coverage"]["baseline"]["completed"] == 50
    assert result["coverage"]["hosted"] is None
    assert result["final_comparison"] is None

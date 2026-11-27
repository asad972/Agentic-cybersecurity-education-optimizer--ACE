"""Tests for the ACE Profile replay agent (agents/profiles)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from ace import contracts
from ace.replay import InMemoryPositionStore
from profiles.__main__ import main as cli_main
from profiles.agent import RAW_PROFILES_TOPIC, DryRunProducer, ProfileAgent
from profiles.normalizer import normalize_country, normalize_profile_row

REPO_ROOT = Path(__file__).resolve().parents[3]
SAMPLE_PROFILES = REPO_ROOT / "shared" / "testdata" / "profiles_sample.jsonl"


def test_normalize_country_european_skills_scope() -> None:
    assert normalize_country("Germany") == "DE"
    assert normalize_country("United Kingdom of Great Britain and Northern Ireland") == "GB"
    assert normalize_country("UK") == "GB"
    assert normalize_country("EL") == "GR"
    assert normalize_country("Switzerland") == "CH"
    assert normalize_country("Norway") == "NO"
    assert normalize_country("United States of America") is None
    assert normalize_country("India") is None
    assert normalize_country("NA") is None


def test_normalize_stackoverflow_row_strips_pii_and_conforms_to_schema() -> None:
    raw_so_row = {
        "ResponseId": "1042",
        "Country": "United Kingdom of Great Britain and Northern Ireland",
        "DevType": "Security professional;Developer, back-end",
        "YearsCodePro": "7",
        "LanguageHaveWorkedWith": "Python;Bash/Shell;SQL",
        "PlatformHaveWorkedWith": "Amazon Web Services (AWS);Linux",
        "ToolsTechHaveWorkedWith": "Docker;Kubernetes;Terraform",
        "Name": "Alice Example",
        "Email": "alice@example.com",
        "ConvertedCompYearly": "125000",
    }
    msg = normalize_profile_row(raw_so_row, default_profile_date="2025-06-01")
    assert msg is not None
    assert contracts.check("raw.profiles", msg) is None
    assert msg["country"] == "GB"
    assert msg["source"] == "stackoverflow"
    assert msg["profile_date"] == "2025-06-01"
    assert "Security professional" in msg["headline"]
    assert "Python" in msg["text"]
    assert "Docker" in msg["text"]
    assert "Alice" not in json.dumps(msg)
    assert "125000" not in json.dumps(msg)
    assert msg["event_id"] == normalize_profile_row(raw_so_row, default_profile_date="2025-06-01")["event_id"]


def test_normalize_stackoverflow_filters_non_security_and_non_europe() -> None:
    frontend_with_python = {
        "ResponseId": "1",
        "Country": "Germany",
        "DevType": "Developer, front-end",
        "LanguageHaveWorkedWith": "JavaScript;Python;HTML/CSS",
        "profile_date": "2024-06-01",
    }
    assert normalize_profile_row(frontend_with_python) is None

    backend_with_cloud = {
        "ResponseId": "2001",
        "Country": "Germany",
        "DevType": "Developer, back-end",
        "YearsCodePro": "5",
        "LanguageHaveWorkedWith": "Python;Bash/Shell",
        "PlatformHaveWorkedWith": "Amazon Web Services (AWS);Linux",
        "ToolsTechHaveWorkedWith": "Docker;Kubernetes;Terraform",
    }
    assert normalize_profile_row(backend_with_cloud, default_profile_date="2025-06-01") is None

    security_tooling_signal = {
        "ResponseId": "2003",
        "Country": "Germany",
        "DevType": "Developer, back-end",
        "ToolsTechHaveWorkedWith": "Burp Suite;OWASP;Docker",
    }
    matched_tool = normalize_profile_row(security_tooling_signal, default_profile_date="2025-06-01")
    assert matched_tool is not None
    assert contracts.check("raw.profiles", matched_tool) is None

    kept = normalize_profile_row(frontend_with_python, security_only=False)
    assert kept is not None
    assert contracts.check("raw.profiles", kept) is None

    non_europe = {
        "ResponseId": "2002",
        "Country": "United States of America",
        "DevType": "Security professional",
        "YearsCodePro": "9",
        "LanguageHaveWorkedWith": "Python",
    }
    assert normalize_profile_row(non_europe, default_profile_date="2025-06-01") is None


def test_event_id_is_distinct_across_survey_years() -> None:
    row_2023 = {
        "ResponseId": "1",
        "Country": "Germany",
        "DevType": "Security professional",
        "LanguageHaveWorkedWith": "Python",
        "profile_date": "2023-06-01",
    }
    row_2024 = {
        "ResponseId": "1",
        "Country": "Germany",
        "DevType": "Security professional",
        "LanguageHaveWorkedWith": "Python",
        "profile_date": "2024-06-01",
    }
    msg_2023 = normalize_profile_row(row_2023)
    msg_2024 = normalize_profile_row(row_2024)
    assert msg_2023 is not None and msg_2024 is not None
    assert msg_2023["event_id"] != msg_2024["event_id"]


def test_replay_sample_profiles_in_event_time_order() -> None:
    producer = DryRunProducer()
    store = InMemoryPositionStore()
    agent = ProfileAgent(seconds_per_day=0.0, position_store=store)

    last_pos = agent.replay_paths(SAMPLE_PROFILES, producer, dataset="sample_profiles")
    assert last_pos == 200
    assert agent.last_skipped_count == 0
    assert len(producer.sent) == 200
    assert all(topic == RAW_PROFILES_TOPIC for topic, _, _ in producer.sent)

    dates = [msg["profile_date"] for _, _, msg in producer.sent]
    assert dates == sorted(dates)
    for _, key, msg in producer.sent:
        assert key == msg["country"]
        assert contracts.check("raw.profiles", msg) is None


def test_replay_stackoverflow_csv_and_checkpoint_resume(tmp_path: Path) -> None:
    csv_path = tmp_path / "survey_results_public_2025.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "ResponseId",
                "Country",
                "DevType",
                "YearsCodePro",
                "LanguageHaveWorkedWith",
                "ToolsTechHaveWorkedWith",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "ResponseId": "1",
                "Country": "Germany",
                "DevType": "Security professional",
                "YearsCodePro": "5",
                "LanguageHaveWorkedWith": "Python",
                "ToolsTechHaveWorkedWith": "Docker;Kubernetes",
            }
        )
        writer.writerow(
            {
                "ResponseId": "2",
                "Country": "United States of America",
                "DevType": "Security professional",
                "YearsCodePro": "8",
                "LanguageHaveWorkedWith": "Python",
                "ToolsTechHaveWorkedWith": "Terraform",
            }
        )
        writer.writerow(
            {
                "ResponseId": "3",
                "Country": "France",
                "DevType": "DevSecOps engineer",
                "YearsCodePro": "4",
                "LanguageHaveWorkedWith": "Bash/Shell",
                "ToolsTechHaveWorkedWith": "Linux;Terraform",
            }
        )

    store = InMemoryPositionStore()
    producer = DryRunProducer()
    agent = ProfileAgent(seconds_per_day=0.0, checkpoint_interval=1, position_store=store)

    pos = agent.replay_paths(csv_path, producer, dataset="so_2025")
    assert pos == 2
    assert agent.last_skipped_count == 1
    assert len(producer.sent) == 2
    assert sorted(m["country"] for _, _, m in producer.sent) == ["DE", "FR"]
    assert all(m["profile_date"] == "2025-06-01" for _, _, m in producer.sent)

    producer_retry = DryRunProducer()
    agent.replay_paths(csv_path, producer_retry, dataset="so_2025")
    assert producer_retry.sent == []


def test_csv_without_year_requires_default_profile_date(tmp_path: Path) -> None:
    csv_path = tmp_path / "survey_results_public.csv"
    csv_path.write_text(
        "ResponseId,Country,DevType,LanguageHaveWorkedWith\n1,Germany,Security professional,Python\n",
        encoding="utf-8",
    )
    agent = ProfileAgent(seconds_per_day=0.0, position_store=InMemoryPositionStore())
    with pytest.raises(ValueError, match="Cannot infer survey year"):
        agent.replay_paths(csv_path, DryRunProducer(), dataset="no_year")


def test_invalid_profile_record_is_routed_to_errors_invalid(tmp_path: Path) -> None:
    bad_jsonl = tmp_path / "bad_profiles.jsonl"
    bad_jsonl.write_text(
        json.dumps(
            {
                "event_id": "11111111-1111-4111-8111-111111111111",
                "source": "stackoverflow",
                "country": "DE",
                "profile_date": "2026-09-01",
                "text": "",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    producer = DryRunProducer()
    agent = ProfileAgent(seconds_per_day=0.0, position_store=InMemoryPositionStore())
    agent.replay_paths(bad_jsonl, producer, dataset="bad_profiles")
    assert len(producer.sent) == 1
    assert producer.sent[0][0] == "errors.invalid"


def test_cli_dry_run_exits_zero(capsys) -> None:
    rc = cli_main(["--input", str(SAMPLE_PROFILES), "--seconds-per-day", "0", "--dry-run"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "Dry-run complete: 200 valid raw.profiles, 0 invalid, 0 skipped" in captured.out

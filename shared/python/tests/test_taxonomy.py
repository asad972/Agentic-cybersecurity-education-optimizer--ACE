"""Tests for the shared ace.taxonomy module."""

from __future__ import annotations

from pathlib import Path

import pytest

from ace.taxonomy import Match, TaxonomyError, _load_roles_file, key_set, load_taxonomy


def test_load_repository_taxonomy_and_match_by_name_and_alias() -> None:
    tax = load_taxonomy()
    assert len(tax.skills) > 0
    assert len(tax.certifications) > 0
    assert len(tax.roles) > 0

    # Exact canonical skill match (case-insensitive)
    assert tax.match("credential dumping") == Match("skill", "Credential Dumping")
    # Skill alias match (case-insensitive)
    assert tax.match("Pass-the-Hash") == Match("skill", "Credential Dumping")
    assert tax.match("suricata") == Match("skill", "Intrusion Detection")

    # Certification canonical and alias match
    assert tax.match("btl1") == Match("certification", "BTL1")
    assert tax.match("Blue Team Level 1") == Match("certification", "BTL1")

    # Role match
    assert tax.match("penetration tester") == Match("role", "Penetration Tester")

    # Unmapped mention returns None
    assert tax.match("Quantum Blockchain Wizardry") is None
    assert tax.match("   ") is None


def test_extract_text_word_boundaries_and_role_inference() -> None:
    tax = load_taxonomy()
    result = tax.extract_text(
        "We are hiring at Siemens for an analyst experienced with Suricata, "
        "Pass-the-Hash defense, and Blue Team Level 1 (BTL1).",
        title="Cyber Incident Responder",
        candidate_mentions=["CustomInternalTool"],
    )
    assert "Intrusion Detection" in result.skills
    assert "Credential Dumping" in result.skills
    assert result.certifications == ["BTL1"]
    assert result.ecsf_role == "Cyber Incident Responder"
    assert result.unmapped == ["CustomInternalTool"]


def test_classify_mentions_separates_mapped_and_unmapped() -> None:
    tax = load_taxonomy()
    res = tax.classify_mentions(
        ["Snort", "Certified Red Team Operator", "UnknownSkillXYZ", "snort"]
    )
    assert res.skills == ["Intrusion Detection"]
    assert res.certifications == ["CRTO"]
    assert res.unmapped == ["UnknownSkillXYZ"]
    assert res.ecsf_role == "Cyber Incident Responder"


def test_key_set_and_roles_for() -> None:
    tax = load_taxonomy()
    keys = key_set()
    assert ("skill", "Intrusion Detection") in keys
    assert ("certification", "BTL1") in keys
    assert ("role", "Penetration Tester") in keys
    assert "Penetration Tester" in tax.roles_for("Credential Dumping")


def test_invalid_taxonomy_directory_raises(tmp_path: Path) -> None:
    with pytest.raises(TaxonomyError):
        load_taxonomy(tmp_path)

def test_load_taxonomy_populates_role_summaries_skill_meta_and_cert_meta(tmp_path: Path) -> None:
    tax = load_taxonomy()
    assert len(tax.skill_meta) == len(tax.skills)
    assert len(tax.cert_meta) == len(tax.certifications)

    skill_by_name = {entry.skill: entry for entry in tax.skill_meta}
    assert "Intrusion Detection" in skill_by_name
    assert "Cyber Incident Responder" in skill_by_name["Intrusion Detection"].ecsf_roles
    assert "Suricata" in skill_by_name["Intrusion Detection"].aliases

    cert_by_name = {entry.certification: entry for entry in tax.cert_meta}
    assert "BTL1" in cert_by_name
    assert cert_by_name["BTL1"].issuer != ""

    roles_csv = tmp_path / "ecsf_roles.csv"
    roles_csv.write_text(
        "role,summary,aliases\n"
        "Cyber Incident Responder,Analyses and responds to cybersecurity incidents.,Incident Responder\n",
        encoding="utf-8",
    )
    roles, aliases, summaries = _load_roles_file(roles_csv)
    assert roles == ("Cyber Incident Responder",)
    assert aliases["incident responder"] == "Cyber Incident Responder"
    assert summaries["Cyber Incident Responder"] == "Analyses and responds to cybersecurity incidents."

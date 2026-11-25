"""Tests for the shared ace.taxonomy module."""

from __future__ import annotations

from pathlib import Path

import pytest

from ace.taxonomy import Match, TaxonomyError, compile_phrase_pattern, key_set, load_taxonomy


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


def test_compile_phrase_pattern_unicode_and_symbols() -> None:
    iso_pat = compile_phrase_pattern("ISO")
    assert iso_pat.search("Aligned with ISO 27001 controls") is not None
    assert iso_pat.search("ISOé") is None
    assert iso_pat.search("éISO") is None

    sec_plus_pat = compile_phrase_pattern("Security+")
    assert sec_plus_pat.search("Holds CompTIA Security+ certification") is not None
    assert sec_plus_pat.search("Security+1") is None
    assert sec_plus_pat.search("CyberSecurity+") is None


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


def test_canonical_v1_files_exist_and_match_all_member_contributions() -> None:
    import csv
    from ace.ecsf import ECSF_ROLES

    tax_dir = Path(__file__).resolve().parents[2] / "taxonomy"
    assert (tax_dir / "skills.csv").is_file()
    assert (tax_dir / "certifications.csv").is_file()
    assert (tax_dir / "ecsf_roles.csv").is_file()

    tax = load_taxonomy()
    assert set(tax.roles) == set(ECSF_ROLES)
    assert len(tax.skills) == 83
    assert len(tax.certifications) == 55

    # Verify entries contributed by each team member (M1-M5) resolve canonically
    assert tax.match("Credential Dumping") == Match("skill", "Credential Dumping")  # M1
    assert tax.match("SIEM") == Match("skill", "SIEM")  # M2
    assert tax.match("AWS Security") == Match("skill", "AWS Security")  # M3
    assert tax.match("ATT&CK") == Match("skill", "MITRE ATT&CK")  # M4
    assert tax.match("Cybersecurity Training") == Match("skill", "Cybersecurity Training")  # M5
    assert tax.match("OSCP") == Match("certification", "OSCP")

    contrib_dir = tax_dir / "contributions"
    expected_skills = set()
    for p in contrib_dir.glob("*_skills.csv"):
        with p.open(encoding="utf-8", newline="") as f:
            expected_skills.update(row["skill"].strip() for row in csv.DictReader(f))
    assert set(tax.skills) == expected_skills

    expected_certs = set()
    for p in contrib_dir.glob("*_certifications.csv"):
        with p.open(encoding="utf-8", newline="") as f:
            expected_certs.update(row["certification"].strip() for row in csv.DictReader(f))
    assert set(tax.certifications) == expected_certs

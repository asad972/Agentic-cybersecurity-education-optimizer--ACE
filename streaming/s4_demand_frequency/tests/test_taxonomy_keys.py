"""S4's key set: read from the canonical taxonomy if it exists, else the proposals."""

import pytest

from s4_demand_frequency.taxonomy_keys import key_set


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_repo_taxonomy_has_every_kind_and_the_12_roles():
    keys = key_set()
    assert ("skill", "SIEM") in keys
    assert ("certification", "CISSP") in keys
    assert len({item for kind, item in keys if kind == "role"}) == 12
    assert 50 <= len(keys) <= 300


def test_proposals_when_there_is_no_canonical_taxonomy(tmp_path):
    write(tmp_path / "contributions" / "m1_skills.csv",
          "skill,ecsf_roles,nice_ids,aliases\n"
          "SIEM,Cyber Incident Responder|Cybersecurity Implementer,,Security Information and Event Management\n")
    write(tmp_path / "contributions" / "m2_skills.csv",
          "skill,ecsf_roles,nice_ids,aliases\nThreat hunting,Cyber Threat Intelligence Specialist,,\n")
    write(tmp_path / "contributions" / "m1_certifications.csv",
          "certification,issuer,aliases\nCISSP,ISC2,\n")

    assert key_set(tmp_path) == {
        ("skill", "SIEM"), ("skill", "Threat hunting"), ("certification", "CISSP"),
        ("role", "Cyber Incident Responder"), ("role", "Cybersecurity Implementer"),
        ("role", "Cyber Threat Intelligence Specialist"),
    }


def test_canonical_taxonomy_wins_over_proposals(tmp_path):
    write(tmp_path / "skills.csv", "skill,ecsf_roles,nice_ids,aliases\nSIEM,Cyber Incident Responder,,\n")
    write(tmp_path / "certifications.csv", "certification,issuer,aliases\nCISSP,ISC2,\n")
    write(tmp_path / "ecsf_roles.csv", "role,summary\nPenetration Tester,Tests systems\n")
    write(tmp_path / "contributions" / "m1_skills.csv",
          "skill,ecsf_roles,nice_ids,aliases\nOld proposal,Cybersecurity Auditor,,\n")

    assert key_set(tmp_path) == {
        ("skill", "SIEM"), ("certification", "CISSP"),
        ("role", "Cyber Incident Responder"), ("role", "Penetration Tester"),
    }


def test_folder_from_environment(tmp_path, monkeypatch):
    write(tmp_path / "skills.csv", "skill,ecsf_roles,nice_ids,aliases\nSIEM,Cyber Incident Responder,,\n")
    write(tmp_path / "certifications.csv", "certification,issuer,aliases\n")
    monkeypatch.setenv("ACE_TAXONOMY_DIR", str(tmp_path))
    assert key_set() == {("skill", "SIEM"), ("role", "Cyber Incident Responder")}


def test_no_taxonomy_fails_clearly(tmp_path):
    with pytest.raises(FileNotFoundError, match="ACE_TAXONOMY_DIR"):
        key_set(tmp_path)

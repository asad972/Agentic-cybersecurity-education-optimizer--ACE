from pathlib import Path

import pytest

from ace_forecast.taxonomy import TaxonomyError, load_skills


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_canonical_taxonomy_takes_precedence(tmp_path):
    write(tmp_path / "skills.csv", "skill,ecsf_roles,aliases\nLinux,Implementer,GNU/Linux\nCloud Security,Architect,Cloud Cybersecurity\n")
    write(tmp_path / "ecsf_roles.csv", "role\nArchitect\nImplementer\n")
    write(tmp_path / "contributions" / "m3_skills.csv", "skill,ecsf_roles,aliases\nTerraform,Architect,Hashicorp Terraform\n")
    assert load_skills(tmp_path) == ("Cloud Security", "Linux")


def test_contributions_are_fallback_and_sorted(tmp_path):
    write(tmp_path / "contributions" / "m3_skills.csv", "skill,ecsf_roles\nTerraform,Architect\nAzure,Architect\n")
    write(tmp_path / "contributions" / "m2_skills.csv", "skill,ecsf_roles\nPython,Implementer\n")
    assert load_skills(tmp_path) == ("Azure", "Python", "Terraform")


def test_case_insensitive_canonical_collision_is_rejected(tmp_path):
    write(tmp_path / "contributions" / "m2_skills.csv", "skill,ecsf_roles\nCloud Security,Architect\n")
    write(tmp_path / "contributions" / "m3_skills.csv", "skill,ecsf_roles\ncloud security,Architect\n")
    with pytest.raises(TaxonomyError, match="collision"):
        load_skills(tmp_path)


def test_missing_or_blank_taxonomy_is_rejected(tmp_path):
    with pytest.raises(TaxonomyError, match="No Skill"):
        load_skills(tmp_path)
    write(tmp_path / "skills.csv", "skill,ecsf_roles\n  ,Architect\n")
    write(tmp_path / "ecsf_roles.csv", "role\nArchitect\n")
    with pytest.raises(TaxonomyError, match="Blank"):
        load_skills(tmp_path)

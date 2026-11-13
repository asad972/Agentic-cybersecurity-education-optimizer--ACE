from pathlib import Path

import pytest

from ace_gap.taxonomy import TaxonomyError, load_taxonomy


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_canonical_taxonomy_loads_roles_and_mappings(tmp_path):
    write(tmp_path / "skills.csv", "skill,ecsf_roles\nLinux,Implementer\nCloud Security,Architect|Implementer\n")
    write(tmp_path / "ecsf_roles.csv", "role,summary\nImplementer,Builds\nArchitect,Designs\n")
    taxonomy = load_taxonomy(tmp_path)
    assert taxonomy.skills == ("Cloud Security", "Linux")
    assert taxonomy.roles == ("Architect", "Implementer")
    assert taxonomy.roles_for("Cloud Security") == ("Architect", "Implementer")


def test_contributions_are_fallback_and_roles_are_derived(tmp_path):
    write(tmp_path / "contributions" / "m3_skills.csv", "skill,ecsf_roles\nTerraform,Architect|Implementer\n")
    write(tmp_path / "contributions" / "m2_skills.csv", "skill,ecsf_roles\nPython,Implementer|Researcher\n")
    taxonomy = load_taxonomy(tmp_path)
    assert taxonomy.skills == ("Python", "Terraform")
    assert taxonomy.roles == ("Architect", "Implementer", "Researcher")


def test_collisions_unknown_roles_and_incomplete_canonical_files_fail(tmp_path):
    write(tmp_path / "contributions" / "m2_skills.csv", "skill,ecsf_roles\nLinux,Implementer\n")
    write(tmp_path / "contributions" / "m3_skills.csv", "skill,ecsf_roles\nlinux,Architect\n")
    with pytest.raises(TaxonomyError, match="collision"):
        load_taxonomy(tmp_path)

    other = tmp_path / "other"
    write(other / "skills.csv", "skill,ecsf_roles\nLinux,Unknown\n")
    write(other / "ecsf_roles.csv", "role\nImplementer\n")
    with pytest.raises(TaxonomyError, match="unknown"):
        load_taxonomy(other)

    incomplete = tmp_path / "incomplete"
    write(incomplete / "skills.csv", "skill,ecsf_roles\nLinux,Implementer\n")
    with pytest.raises(TaxonomyError, match="both"):
        load_taxonomy(incomplete)


def test_blank_or_missing_taxonomy_fails(tmp_path):
    with pytest.raises(TaxonomyError, match="No Skill"):
        load_taxonomy(tmp_path)
    write(tmp_path / "contributions" / "m3_skills.csv", "skill,ecsf_roles\n,Architect\n")
    with pytest.raises(TaxonomyError, match="Blank"):
        load_taxonomy(tmp_path)

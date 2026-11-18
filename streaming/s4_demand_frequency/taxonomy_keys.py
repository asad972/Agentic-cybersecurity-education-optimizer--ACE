"""The Skill Taxonomy as S4's key set: every (kind, item) the sketch is asked about.

Stop-gap until ace.taxonomy (M1) is merged; then key_set() becomes a few lines
on top of it and nothing else in S4 changes. Reads, from shared/taxonomy/ or
ACE_TAXONOMY_DIR inside a container:

- the canonical skills.csv, certifications.csv and ecsf_roles.csv if they exist,
- otherwise the members' proposals in contributions/*_skills.csv and
  *_certifications.csv, with the ECSF roles taken from their ecsf_roles column.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

Key = tuple[str, str]   # (kind, item), e.g. ("skill", "SIEM")


def taxonomy_dir() -> Path:
    configured = os.environ.get("ACE_TAXONOMY_DIR")
    return Path(configured) if configured else Path(__file__).resolve().parents[2] / "shared" / "taxonomy"


def key_set(folder: Path | None = None) -> frozenset[Key]:
    """Every Skill, Certification and ECSF role in the taxonomy."""
    folder = folder or taxonomy_dir()
    if (folder / "skills.csv").is_file():
        skill_files = [folder / "skills.csv"]
        certification_files = [folder / "certifications.csv"]
    else:
        skill_files = sorted((folder / "contributions").glob("*_skills.csv"))
        certification_files = sorted((folder / "contributions").glob("*_certifications.csv"))
    if not skill_files:
        raise FileNotFoundError(f"no Skill Taxonomy in {folder}; set ACE_TAXONOMY_DIR")

    keys: set[Key] = set()
    for row in _rows(skill_files):
        keys.add(("skill", row["skill"]))
        keys.update(("role", role) for role in _values(row["ecsf_roles"]))
    for row in _rows(certification_files):
        keys.add(("certification", row["certification"]))
    if (folder / "ecsf_roles.csv").is_file():
        keys.update(("role", row["role"]) for row in _rows([folder / "ecsf_roles.csv"]))
    return frozenset(keys)


def _rows(paths: list[Path]):
    for path in paths:
        with open(path, encoding="utf-8", newline="") as f:
            yield from csv.DictReader(f)


def _values(field: str) -> list[str]:
    """A multi-value field: values separated by |, empty means none."""
    return field.split("|") if field else []

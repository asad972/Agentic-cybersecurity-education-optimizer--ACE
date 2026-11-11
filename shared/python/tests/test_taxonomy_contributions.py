"""Checks every member's Skill Taxonomy proposal in shared/taxonomy/contributions/.

M1 merges the proposals into the canonical taxonomy; these checks catch the
problems that would break that merge or the skill matcher: wrong headers,
unknown ECSF roles, and a name or alias that points to two different entries.
"""

import csv
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CONTRIBUTIONS = ROOT / "shared" / "taxonomy" / "contributions"
TESTDATA = ROOT / "shared" / "testdata"

SKILL_HEADER = ["skill", "ecsf_roles", "nice_ids", "aliases"]
CERTIFICATION_HEADER = ["certification", "issuer", "aliases"]

# The 12 ENISA ECSF role profiles, spelled as published.
ECSF_ROLES = {
    "Chief Information Security Officer (CISO)",
    "Cyber Incident Responder",
    "Cyber Legal, Policy and Compliance Officer",
    "Cyber Threat Intelligence Specialist",
    "Cybersecurity Architect",
    "Cybersecurity Auditor",
    "Cybersecurity Educator",
    "Cybersecurity Implementer",
    "Cybersecurity Researcher",
    "Cybersecurity Risk Manager",
    "Digital Forensics Investigator",
    "Penetration Tester",
}

SKILL_FILES = sorted(CONTRIBUTIONS.glob("*_skills.csv"))
CERTIFICATION_FILES = sorted(CONTRIBUTIONS.glob("*_certifications.csv"))


def read(path):
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    return rows[0], [dict(zip(rows[0], row)) for row in rows[1:]]


def items(field):
    """A multi-value field: values separated by |, empty field means none."""
    return field.split("|") if field else []


def names():
    """Every name and alias, lower-cased, mapped to the entries it points to."""
    index = {}
    for paths, column in ((SKILL_FILES, "skill"), (CERTIFICATION_FILES, "certification")):
        for path in paths:
            for row in read(path)[1]:
                entry = f"{row[column]} ({path.name})"
                for name in [row[column], *items(row["aliases"])]:
                    index.setdefault(name.lower(), set()).add(entry)
    return index


def test_contributions_exist():
    assert SKILL_FILES and CERTIFICATION_FILES


@pytest.mark.parametrize("path", SKILL_FILES, ids=lambda p: p.name)
def test_skill_file(path):
    header, rows = read(path)
    assert header == SKILL_HEADER
    for row in rows:
        assert row["skill"] and row["skill"] == row["skill"].strip(), row
        roles = items(row["ecsf_roles"])
        assert roles, f"{row['skill']}: no ECSF role"
        assert set(roles) <= ECSF_ROLES, f"{row['skill']}: unknown role {set(roles) - ECSF_ROLES}"
        for column in ("nice_ids", "aliases"):
            assert "" not in items(row[column]), f"{row['skill']}: empty value in {column}"


@pytest.mark.parametrize("path", CERTIFICATION_FILES, ids=lambda p: p.name)
def test_certification_file(path):
    header, rows = read(path)
    assert header == CERTIFICATION_HEADER
    for row in rows:
        assert row["certification"] and row["issuer"], row
        assert "" not in items(row["aliases"]), f"{row['certification']}: empty alias"


def test_no_name_points_to_two_entries():
    clashes = {name: entries for name, entries in names().items() if len(entries) > 1}
    assert not clashes, "\n".join(f"{n!r} -> {sorted(e)}" for n, e in sorted(clashes.items()))


def test_every_test_data_skill_is_in_a_contribution():
    terms = set()
    for line in (TESTDATA / "jobs_sample.jsonl").read_text(encoding="utf-8").splitlines():
        found = re.search(r"Required skills and technologies: ([^.]+)\.", json.loads(line)["description"])
        terms.update(t.strip() for t in found.group(1).split(","))
    for line in (TESTDATA / "profiles_sample.jsonl").read_text(encoding="utf-8").splitlines():
        terms.update(json.loads(line)["skills"])
    index = names()
    missing = sorted(t for t in terms if t.lower() not in index)
    assert not missing, f"not in any contribution: {missing}"

"""Canonical Skills and ECSF mappings with a contribution fallback."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


class TaxonomyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Taxonomy:
    skills: tuple[str, ...]
    roles: tuple[str, ...]
    skill_roles: tuple[tuple[str, tuple[str, ...]], ...]

    def roles_for(self, skill: str) -> tuple[str, ...]:
        for name, roles in self.skill_roles:
            if name == skill:
                return roles
        raise KeyError(skill)

    @property
    def skill_lookup(self) -> dict[str, str]:
        return {skill.casefold(): skill for skill in self.skills}


def load_taxonomy(taxonomy_dir: Path) -> Taxonomy:
    # TODO(issue #89): replace this temporary loader with shared ace.taxonomy.
    canonical_skills = taxonomy_dir / "skills.csv"
    canonical_roles = taxonomy_dir / "ecsf_roles.csv"
    if canonical_skills.exists() or canonical_roles.exists():
        if not canonical_skills.is_file() or not canonical_roles.is_file():
            raise TaxonomyError("Canonical taxonomy requires both skills.csv and ecsf_roles.csv")
        sources = (canonical_skills,)
        roles = _load_roles(canonical_roles)
        validate_roles = True
    else:
        sources = tuple(sorted((taxonomy_dir / "contributions").glob("*_skills.csv")))
        if not sources:
            raise TaxonomyError(f"No Skill taxonomy files found under {taxonomy_dir}")
        roles = ()
        validate_roles = False

    mappings: dict[str, tuple[str, tuple[str, ...]]] = {}
    discovered_roles: set[str] = set()
    for source in sources:
        with source.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            required = {"skill", "ecsf_roles"}
            if not required <= set(reader.fieldnames or ()):
                raise TaxonomyError(f"Missing columns {sorted(required)} in {source}")
            for row_number, row in enumerate(reader, start=2):
                skill = (row.get("skill") or "").strip()
                if not skill:
                    raise TaxonomyError(f"Blank canonical Skill in {source}:{row_number}")
                identity = skill.casefold()
                if identity in mappings:
                    previous = mappings[identity][0]
                    raise TaxonomyError(f"Canonical Skill collision: {previous!r} and {skill!r}")
                mapped_roles = tuple(part.strip() for part in (row.get("ecsf_roles") or "").split("|") if part.strip())
                if not mapped_roles:
                    raise TaxonomyError(f"Skill {skill!r} has no ECSF role in {source}:{row_number}")
                if len(set(mapped_roles)) != len(mapped_roles):
                    raise TaxonomyError(f"Skill {skill!r} repeats an ECSF role")
                unknown = sorted(set(mapped_roles) - set(roles)) if validate_roles else []
                if unknown:
                    raise TaxonomyError(f"Skill {skill!r} uses unknown ECSF roles: {unknown}")
                discovered_roles.update(mapped_roles)
                mappings[identity] = (skill, mapped_roles)

    role_names = roles or tuple(sorted(discovered_roles, key=str.casefold))
    ordered = tuple(sorted(mappings.values(), key=lambda item: item[0].casefold()))
    return Taxonomy(
        skills=tuple(skill for skill, _ in ordered),
        roles=role_names,
        skill_roles=ordered,
    )


def _load_roles(path: Path) -> tuple[str, ...]:
    found: dict[str, str] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if "role" not in (reader.fieldnames or ()):
            raise TaxonomyError(f"Missing role column in {path}")
        for row_number, row in enumerate(reader, start=2):
            role = (row.get("role") or "").strip()
            if not role:
                raise TaxonomyError(f"Blank ECSF role in {path}:{row_number}")
            identity = role.casefold()
            if identity in found:
                raise TaxonomyError(f"ECSF role collision: {found[identity]!r} and {role!r}")
            found[identity] = role
    return tuple(sorted(found.values(), key=str.casefold))

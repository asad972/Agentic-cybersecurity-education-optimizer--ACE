"""Skill Taxonomy loader and case-insensitive mention matcher (ace.taxonomy).

Loads canonical Skills, Certifications, and ECSF roles from ``shared/taxonomy/``
(falling back to ``shared/taxonomy/contributions/`` when canonical CSVs are not
yet generated) and maps text mentions to taxonomy entries by exact name or
listed alias, ignoring case.
"""

from __future__ import annotations

import csv
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

Key = tuple[str, str]  # (kind, canonical_name), e.g. ("skill", "SIEM")


class TaxonomyError(ValueError):
    """Raised when taxonomy CSV files are missing or malformed."""


@dataclass(frozen=True, slots=True)
class Match:
    """A resolved taxonomy entry."""

    kind: str  # "skill", "certification", or "role"
    canonical: str


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """Normalized taxonomy extraction output for a job ad or profile."""

    skills: list[str]
    certifications: list[str]
    ecsf_role: str | None
    unmapped: list[str]


def _normalize(text: str) -> str:
    """Collapse internal whitespace and casefold for case-insensitive lookup."""
    return " ".join(text.strip().split()).casefold()


def _split_pipe(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.split("|") if part.strip())


def _compile_phrase_pattern(term: str) -> re.Pattern[str]:
    """Compile a case-insensitive boundary-aware regex for a taxonomy term."""
    parts = [re.escape(p) for p in term.strip().split() if p]
    body = r"\s+".join(parts)
    return re.compile(rf"(?<![A-Za-z0-9_]){body}(?![A-Za-z0-9_])", re.IGNORECASE)


def taxonomy_dir() -> Path:
    """Return the taxonomy directory from ACE_TAXONOMY_DIR or repo default."""
    configured = os.environ.get("ACE_TAXONOMY_DIR", "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "taxonomy"


@dataclass(frozen=True)
class Taxonomy:
    """In-memory Skill Taxonomy and mention matcher."""

    skills: tuple[str, ...]
    certifications: tuple[str, ...]
    roles: tuple[str, ...]
    skill_roles: tuple[tuple[str, tuple[str, ...]], ...]
    _skill_map: dict[str, str]
    _cert_map: dict[str, str]
    _role_map: dict[str, str]
    _roles_by_skill: dict[str, tuple[str, ...]]
    _skill_patterns: tuple[tuple[re.Pattern[str], str], ...]
    _cert_patterns: tuple[tuple[re.Pattern[str], str], ...]
    _role_patterns: tuple[tuple[re.Pattern[str], str], ...]

    @property
    def skill_lookup(self) -> dict[str, str]:
        """Map casefolded canonical skill names and aliases to canonical skill."""
        return dict(self._skill_map)

    @property
    def certification_lookup(self) -> dict[str, str]:
        """Map casefolded canonical certification names and aliases to canonical name."""
        return dict(self._cert_map)

    @property
    def role_lookup(self) -> dict[str, str]:
        """Map casefolded ECSF role names and aliases to canonical role."""
        return dict(self._role_map)

    def roles_for(self, skill: str) -> tuple[str, ...]:
        """Return the ECSF roles associated with a canonical skill."""
        if skill in self._roles_by_skill:
            return self._roles_by_skill[skill]
        canonical = self._skill_map.get(_normalize(skill))
        if canonical and canonical in self._roles_by_skill:
            return self._roles_by_skill[canonical]
        raise KeyError(skill)

    def skills_for_role(self, role: str) -> tuple[str, ...]:
        """Return all canonical skills mapped to the given ECSF role."""
        canonical_role = self._role_map.get(_normalize(role), role)
        return tuple(
            skill
            for skill, mapped_roles in self.skill_roles
            if canonical_role in mapped_roles
        )

    def key_set(self) -> frozenset[Key]:
        """Return every (kind, item) pair in the taxonomy (used by S4/S5)."""
        keys: set[Key] = set()
        keys.update(("skill", s) for s in self.skills)
        keys.update(("certification", c) for c in self.certifications)
        keys.update(("role", r) for r in self.roles)
        return frozenset(keys)

    def match_skill(self, mention: str) -> str | None:
        """Return canonical Skill if mention matches a skill or alias, else None."""
        key = _normalize(mention)
        return self._skill_map.get(key) if key else None

    def match_certification(self, mention: str) -> str | None:
        """Return canonical Certification if mention matches a cert or alias, else None."""
        key = _normalize(mention)
        return self._cert_map.get(key) if key else None

    def match_role(self, mention: str) -> str | None:
        """Return canonical ECSF Role if mention matches a role or alias, else None."""
        key = _normalize(mention)
        return self._role_map.get(key) if key else None

    def match(self, mention: str) -> Match | None:
        """Map a single mention to a Skill, Certification, or Role, or None if unmapped."""
        key = _normalize(mention)
        if not key:
            return None
        if key in self._skill_map:
            return Match("skill", self._skill_map[key])
        if key in self._cert_map:
            return Match("certification", self._cert_map[key])
        if key in self._role_map:
            return Match("role", self._role_map[key])
        return None

    def infer_role(
        self,
        skills: Iterable[str],
        title: str = "",
        text: str = "",
    ) -> str | None:
        """Infer the best ECSF role from job title, text, or matched skills."""
        if title:
            direct = self.match_role(title)
            if direct:
                return direct
            for pattern, canonical_role in self._role_patterns:
                if pattern.search(title):
                    return canonical_role

        if text:
            for pattern, canonical_role in self._role_patterns:
                if pattern.search(text):
                    return canonical_role

        role_counts: dict[str, int] = {}
        first_seen_order: dict[str, int] = {}
        for idx, skill in enumerate(skills):
            for role in self._roles_by_skill.get(skill, ()):
                role_counts[role] = role_counts.get(role, 0) + 1
                first_seen_order.setdefault(role, idx)

        if not role_counts:
            return None

        return min(
            role_counts,
            key=lambda r: (-role_counts[r], first_seen_order[r], r.casefold()),
        )

    def classify_mentions(
        self,
        mentions: Iterable[str],
        *,
        explicit_role: str | None = None,
        title: str = "",
    ) -> ExtractionResult:
        """Map candidate mentions (e.g. from an LLM or tag list) to taxonomy entries."""
        skills: list[str] = []
        seen_skills: set[str] = set()
        certs: list[str] = []
        seen_certs: set[str] = set()
        unmapped: list[str] = []
        seen_unmapped: set[str] = set()
        matched_role: str | None = (
            self.match_role(explicit_role) if explicit_role else None
        )

        for raw in mentions:
            cleaned = " ".join(str(raw).strip().split())
            if not cleaned:
                continue
            m = self.match(cleaned)
            if m is None:
                norm = cleaned.casefold()
                if norm not in seen_unmapped:
                    seen_unmapped.add(norm)
                    unmapped.append(cleaned)
            elif m.kind == "skill":
                if m.canonical not in seen_skills:
                    seen_skills.add(m.canonical)
                    skills.append(m.canonical)
            elif m.kind == "certification":
                if m.canonical not in seen_certs:
                    seen_certs.add(m.canonical)
                    certs.append(m.canonical)
            elif m.kind == "role" and matched_role is None:
                matched_role = m.canonical

        if matched_role is None:
            matched_role = self.infer_role(skills, title=title)

        return ExtractionResult(
            skills=skills,
            certifications=certs,
            ecsf_role=matched_role,
            unmapped=unmapped,
        )

    def extract_text(
        self,
        text: str,
        *,
        title: str = "",
        candidate_mentions: Iterable[str] = (),
    ) -> ExtractionResult:
        """Extract canonical Skills, Certifications, and ECSF role from free text."""
        combined = f"{title}\n{text}".strip() if title else text.strip()

        skills: list[str] = []
        seen_skills: set[str] = set()
        certs: list[str] = []
        seen_certs: set[str] = set()

        if combined:
            for pattern, canonical_skill in self._skill_patterns:
                if (
                    canonical_skill not in seen_skills
                    and pattern.search(combined)
                ):
                    seen_skills.add(canonical_skill)
                    skills.append(canonical_skill)

            for pattern, canonical_cert in self._cert_patterns:
                if (
                    canonical_cert not in seen_certs
                    and pattern.search(combined)
                ):
                    seen_certs.add(canonical_cert)
                    certs.append(canonical_cert)

        unmapped: list[str] = []
        seen_unmapped: set[str] = set()
        explicit_role: str | None = None

        for raw in candidate_mentions:
            cleaned = " ".join(str(raw).strip().split())
            if not cleaned:
                continue
            m = self.match(cleaned)
            if m is None:
                norm = cleaned.casefold()
                if norm not in seen_unmapped:
                    seen_unmapped.add(norm)
                    unmapped.append(cleaned)
            elif m.kind == "skill" and m.canonical not in seen_skills:
                seen_skills.add(m.canonical)
                skills.append(m.canonical)
            elif m.kind == "certification" and m.canonical not in seen_certs:
                seen_certs.add(m.canonical)
                certs.append(m.canonical)
            elif m.kind == "role" and explicit_role is None:
                explicit_role = m.canonical

        role = explicit_role or self.infer_role(skills, title=title, text=text)
        return ExtractionResult(
            skills=skills,
            certifications=certs,
            ecsf_role=role,
            unmapped=unmapped,
        )


@lru_cache(maxsize=8)
def _load_cached(resolved_dir: str) -> Taxonomy:
    folder = Path(resolved_dir)
    canonical_skills = folder / "skills.csv"
    canonical_certs = folder / "certifications.csv"
    canonical_roles = folder / "ecsf_roles.csv"

    if canonical_skills.exists() or canonical_roles.exists():
        if not canonical_skills.is_file() or not canonical_roles.is_file():
            raise TaxonomyError(
                "Canonical taxonomy requires both skills.csv and ecsf_roles.csv"
            )
        skill_files = (canonical_skills,)
        explicit_roles, role_aliases = _load_roles_file(canonical_roles)
        validate_roles = True
    else:
        skill_files = tuple(
            sorted((folder / "contributions").glob("*_skills.csv"))
        )
        if not skill_files:
            raise TaxonomyError(f"No Skill taxonomy files found under {folder}")
        explicit_roles, role_aliases = (), {}
        validate_roles = False

    if canonical_certs.is_file():
        cert_files = (canonical_certs,)
    else:
        cert_files = tuple(
            sorted((folder / "contributions").glob("*_certifications.csv"))
        )

    skill_mappings: dict[str, tuple[str, tuple[str, ...]]] = {}
    skill_alias_map: dict[str, str] = {}
    skill_terms: list[tuple[str, str]] = []
    discovered_roles: dict[str, str] = {r.casefold(): r for r in explicit_roles}

    for source in skill_files:
        with source.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            required = {"skill", "ecsf_roles"}
            if not required <= set(reader.fieldnames or ()):
                raise TaxonomyError(
                    f"Missing columns {sorted(required)} in {source}"
                )
            for row_number, row in enumerate(reader, start=2):
                skill = (row.get("skill") or "").strip()
                if not skill:
                    raise TaxonomyError(
                        f"Blank canonical Skill in {source}:{row_number}"
                    )
                identity = _normalize(skill)
                if identity in skill_mappings:
                    previous = skill_mappings[identity][0]
                    raise TaxonomyError(
                        f"Canonical Skill collision: {previous!r} and {skill!r}"
                    )
                mapped_roles = _split_pipe(row.get("ecsf_roles"))
                if not mapped_roles:
                    raise TaxonomyError(
                        f"Skill {skill!r} has no ECSF role in {source}:{row_number}"
                    )
                if len(set(mapped_roles)) != len(mapped_roles):
                    raise TaxonomyError(
                        f"Skill {skill!r} repeats an ECSF role"
                    )
                if validate_roles:
                    unknown = sorted(set(mapped_roles) - set(explicit_roles))
                    if unknown:
                        raise TaxonomyError(
                            f"Skill {skill!r} uses unknown ECSF roles: {unknown}"
                        )
                for r in mapped_roles:
                    discovered_roles.setdefault(r.casefold(), r)

                skill_mappings[identity] = (skill, mapped_roles)
                skill_terms.append((skill, skill))
                for alias in _split_pipe(row.get("aliases")):
                    alias_key = _normalize(alias)
                    if alias_key and alias_key not in skill_mappings:
                        skill_alias_map.setdefault(alias_key, skill)
                        skill_terms.append((alias, skill))

    skill_map: dict[str, str] = dict(skill_alias_map)
    for norm_key, (canonical_skill, _) in skill_mappings.items():
        skill_map[norm_key] = canonical_skill

    cert_canonical_map: dict[str, str] = {}
    cert_alias_map: dict[str, str] = {}
    cert_terms: list[tuple[str, str]] = []

    for source in cert_files:
        with source.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if "certification" not in (reader.fieldnames or ()):
                raise TaxonomyError(f"Missing certification column in {source}")
            for row_number, row in enumerate(reader, start=2):
                cert = (row.get("certification") or "").strip()
                if not cert:
                    raise TaxonomyError(
                        f"Blank canonical Certification in {source}:{row_number}"
                    )
                identity = _normalize(cert)
                if identity in cert_canonical_map:
                    previous = cert_canonical_map[identity]
                    raise TaxonomyError(
                        f"Canonical Certification collision: {previous!r} and {cert!r}"
                    )
                cert_canonical_map[identity] = cert
                cert_terms.append((cert, cert))
                for alias in _split_pipe(row.get("aliases")):
                    alias_key = _normalize(alias)
                    if alias_key and alias_key not in cert_canonical_map:
                        cert_alias_map.setdefault(alias_key, cert)
                        cert_terms.append((alias, cert))

    cert_map: dict[str, str] = dict(cert_alias_map)
    cert_map.update(cert_canonical_map)

    role_names = explicit_roles or tuple(
        sorted(discovered_roles.values(), key=str.casefold)
    )
    role_map: dict[str, str] = dict(role_aliases)
    role_terms: list[tuple[str, str]] = []
    for role in role_names:
        role_map[_normalize(role)] = role
        role_terms.append((role, role))
    for alias_norm, canonical_role in role_aliases.items():
        role_terms.append((alias_norm, canonical_role))

    ordered_skills = tuple(
        sorted(skill_mappings.values(), key=lambda item: item[0].casefold())
    )
    ordered_certs = tuple(sorted(cert_canonical_map.values(), key=str.casefold))
    roles_by_skill = {skill: roles for skill, roles in ordered_skills}

    # Sort patterns longest-first so specific multi-word phrases match first
    skill_patterns = tuple(
        (_compile_phrase_pattern(term), canonical)
        for term, canonical in sorted(
            skill_terms, key=lambda x: (-len(x[0]), x[0].casefold())
        )
    )
    cert_patterns = tuple(
        (_compile_phrase_pattern(term), canonical)
        for term, canonical in sorted(
            cert_terms, key=lambda x: (-len(x[0]), x[0].casefold())
        )
    )
    role_patterns = tuple(
        (_compile_phrase_pattern(term), canonical)
        for term, canonical in sorted(
            role_terms, key=lambda x: (-len(x[0]), x[0].casefold())
        )
    )

    return Taxonomy(
        skills=tuple(skill for skill, _ in ordered_skills),
        certifications=ordered_certs,
        roles=role_names,
        skill_roles=ordered_skills,
        _skill_map=skill_map,
        _cert_map=cert_map,
        _role_map=role_map,
        _roles_by_skill=roles_by_skill,
        _skill_patterns=skill_patterns,
        _cert_patterns=cert_patterns,
        _role_patterns=role_patterns,
    )


def _load_roles_file(path: Path) -> tuple[tuple[str, ...], dict[str, str]]:
    found: dict[str, str] = {}
    aliases: dict[str, str] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if "role" not in (reader.fieldnames or ()):
            raise TaxonomyError(f"Missing role column in {path}")
        for row_number, row in enumerate(reader, start=2):
            role = (row.get("role") or "").strip()
            if not role:
                raise TaxonomyError(f"Blank ECSF role in {path}:{row_number}")
            identity = _normalize(role)
            if identity in found:
                raise TaxonomyError(
                    f"ECSF role collision: {found[identity]!r} and {role!r}"
                )
            found[identity] = role
            for alias in _split_pipe(row.get("aliases")):
                alias_key = _normalize(alias)
                if alias_key:
                    aliases.setdefault(alias_key, role)
    return tuple(sorted(found.values(), key=str.casefold)), aliases


def load_taxonomy(folder: Path | str | None = None) -> Taxonomy:
    """Load the Skill Taxonomy from folder (defaults to ACE_TAXONOMY_DIR / shared/taxonomy)."""
    target = Path(folder) if folder is not None else taxonomy_dir()
    return _load_cached(str(target.resolve()))


def key_set(folder: Path | str | None = None) -> frozenset[Key]:
    """Return every (kind, item) pair in the taxonomy."""
    return load_taxonomy(folder).key_set()
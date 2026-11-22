"""ENISA ECSF role profiles, skill mappings, and certification catalog for Role Explorer (#14)."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
import logging
import os
from pathlib import Path
import re
import sys
from typing import Any

try:
    from ace.ecsf import ECSF_ROLES
except ModuleNotFoundError:
    _shared_python = Path(__file__).resolve().parents[2] / "shared" / "python"
    if _shared_python.is_dir() and str(_shared_python) not in sys.path:
        sys.path.insert(0, str(_shared_python))
    from ace.ecsf import ECSF_ROLES

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class EcsfRoleProfile:
    role: str
    slug: str
    summary: str | None = None


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")


def _split_pipe(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.split("|") if part.strip())


def _resolve_taxonomy_dir() -> Path:
    configured = os.environ.get("ACE_TAXONOMY_DIR", "").strip()
    if configured:
        candidate = Path(configured)
        if not candidate.is_dir():
            raise FileNotFoundError(
                f"ACE_TAXONOMY_DIR points to a missing directory: {candidate}"
            )
        return candidate

    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "shared" / "taxonomy"
        if candidate.is_dir():
            return candidate

    raise FileNotFoundError(
        "Skill taxonomy directory not found; set ACE_TAXONOMY_DIR or bundle shared/taxonomy"
    )


@lru_cache(maxsize=8)
def _load_role_catalog_from_dir(taxonomy_path: str) -> dict[str, Any]:
    """Load ECSF roles, skills, and certifications strictly from the taxonomy CSV files."""
    taxonomy_dir = Path(taxonomy_path)
    if not taxonomy_dir.is_dir():
        raise FileNotFoundError(f"Taxonomy directory does not exist: {taxonomy_dir}")

    valid_roles = set(ECSF_ROLES)
    summaries_by_role: dict[str, str] = {}

    canonical_roles = taxonomy_dir / "ecsf_roles.csv"
    if canonical_roles.is_file():
        with canonical_roles.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                role_name = (row.get("role") or "").strip()
                if not role_name:
                    continue
                if role_name not in valid_roles:
                    log.warning("Ignoring unknown ECSF role %r in %s", role_name, canonical_roles)
                    continue
                summary = (row.get("summary") or "").strip()
                if summary:
                    summaries_by_role[role_name] = summary

    profiles_by_name: dict[str, EcsfRoleProfile] = {
        role_name: EcsfRoleProfile(
            role=role_name,
            slug=_slugify(role_name),
            summary=summaries_by_role.get(role_name),
        )
        for role_name in valid_roles
    }

    skills_by_name: dict[str, dict[str, Any]] = {}
    canonical_skills = taxonomy_dir / "skills.csv"
    skill_sources = (
        (canonical_skills,)
        if canonical_skills.is_file()
        else tuple(sorted((taxonomy_dir / "contributions").glob("*_skills.csv")))
    )
    if not skill_sources:
        raise FileNotFoundError(f"No skill taxonomy CSV files found under {taxonomy_dir}")

    for source in skill_sources:
        with source.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                skill = (row.get("skill") or "").strip()
                raw_roles = _split_pipe(row.get("ecsf_roles"))
                if not skill or not raw_roles:
                    continue
                mapped_roles: list[str] = []
                for role_name in raw_roles:
                    if role_name not in valid_roles:
                        log.warning(
                            "Ignoring unknown ECSF role %r for skill %r in %s",
                            role_name,
                            skill,
                            source,
                        )
                        continue
                    mapped_roles.append(role_name)
                if not mapped_roles:
                    continue
                skills_by_name[skill.casefold()] = {
                    "skill": skill,
                    "ecsf_roles": mapped_roles,
                    "nice_ids": list(_split_pipe(row.get("nice_ids"))),
                    "aliases": list(_split_pipe(row.get("aliases"))),
                }

    if not skills_by_name:
        raise RuntimeError(f"No skill taxonomy entries loaded from {taxonomy_dir}")

    certs_by_name: dict[str, dict[str, Any]] = {}
    canonical_certs = taxonomy_dir / "certifications.csv"
    cert_sources = (
        (canonical_certs,)
        if canonical_certs.is_file()
        else tuple(sorted((taxonomy_dir / "contributions").glob("*_certifications.csv")))
    )
    for source in cert_sources:
        with source.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                cert = (row.get("certification") or "").strip()
                if not cert:
                    continue
                mapped_roles: list[str] = []
                for role_name in _split_pipe(row.get("ecsf_roles")):
                    if role_name not in valid_roles:
                        log.warning(
                            "Ignoring unknown ECSF role %r for certification %r in %s",
                            role_name,
                            cert,
                            source,
                        )
                        continue
                    mapped_roles.append(role_name)
                certs_by_name[cert.casefold()] = {
                    "certification": cert,
                    "issuer": (row.get("issuer") or "").strip(),
                    "aliases": list(_split_pipe(row.get("aliases"))),
                    "ecsf_roles": mapped_roles,
                }

    ordered_profiles = sorted(profiles_by_name.values(), key=lambda p: p.role.casefold())
    skills_for_role: dict[str, list[dict[str, Any]]] = {p.role: [] for p in ordered_profiles}

    for entry in sorted(skills_by_name.values(), key=lambda item: item["skill"].casefold()):
        for role_name in entry["ecsf_roles"]:
            skills_for_role[role_name].append(entry)

    certs_for_role: dict[str, list[dict[str, Any]]] = {p.role: [] for p in ordered_profiles}
    for entry in sorted(certs_by_name.values(), key=lambda item: item["certification"].casefold()):
        # Certifications without explicit ecsf_roles in the taxonomy are not assigned to any role.
        if not entry["ecsf_roles"]:
            continue
        cert_item = {
            "certification": entry["certification"],
            "issuer": entry["issuer"],
            "aliases": entry["aliases"],
        }
        for role_name in entry["ecsf_roles"]:
            certs_for_role[role_name].append(cert_item)

    lookup: dict[str, EcsfRoleProfile] = {}
    for profile in ordered_profiles:
        lookup[profile.role.casefold()] = profile
        lookup[profile.slug] = profile
        lookup[_slugify(profile.role)] = profile

    return {
        "profiles": ordered_profiles,
        "lookup": lookup,
        "skills_for_role": skills_for_role,
        "certs_for_role": certs_for_role,
    }


def load_role_catalog() -> dict[str, Any]:
    """Return the ECSF role profiles and taxonomy-sourced skills and certifications."""
    resolved = _resolve_taxonomy_dir()
    return _load_role_catalog_from_dir(str(resolved.resolve()))


def resolve_role(role_query: str) -> EcsfRoleProfile | None:
    """Resolve an ECSF role by canonical name (case-insensitive) or slug."""
    catalog = load_role_catalog()
    cleaned = " ".join(role_query.strip().split())
    if not cleaned:
        return None
    direct = catalog["lookup"].get(cleaned.casefold())
    if direct is not None:
        return direct
    return catalog["lookup"].get(_slugify(cleaned))


def taxonomy_related_roles(role_name: str) -> list[dict[str, Any]]:
    """Compute Jaccard overlap between ECSF roles based on shared canonical skills."""
    catalog = load_role_catalog()
    skills_for_role: dict[str, list[dict[str, Any]]] = catalog["skills_for_role"]
    target_skills = {item["skill"] for item in skills_for_role.get(role_name, ())}
    if not target_skills:
        return []

    related: list[dict[str, Any]] = []
    for profile in catalog["profiles"]:
        if profile.role == role_name:
            continue
        other_skills = {item["skill"] for item in skills_for_role.get(profile.role, ())}
        if not other_skills:
            continue
        shared = sorted(target_skills & other_skills, key=str.casefold)
        union_size = len(target_skills | other_skills)
        if not shared or union_size == 0:
            continue
        overlap = round(len(shared) / union_size, 4)
        related.append(
            {
                "role": profile.role,
                "slug": profile.slug,
                "overlap": overlap,
                "shared_skills": shared,
                "source": "taxonomy",
            }
        )

    related.sort(key=lambda item: (-item["overlap"], item["role"].casefold()))
    return related
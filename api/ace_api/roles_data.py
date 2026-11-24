"""ENISA ECSF role profiles, skill mappings, and certification catalog for Role Explorer (#14)."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from ace.ecsf import ECSF_ROLES
from ace.taxonomy import load_taxonomy


@dataclass(frozen=True)
class EcsfRoleProfile:
    role: str
    slug: str
    summary: str | None = None


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")


def load_role_catalog() -> dict[str, Any]:
    """Return the ECSF role profiles and taxonomy-sourced skills and certifications."""
    taxonomy = load_taxonomy()
    valid_roles = set(ECSF_ROLES) | set(taxonomy.roles)
    summaries_by_role = dict(taxonomy.role_summaries)

    ordered_profiles = sorted(
        (
            EcsfRoleProfile(
                role=role_name,
                slug=_slugify(role_name),
                summary=summaries_by_role.get(role_name),
            )
            for role_name in valid_roles
        ),
        key=lambda p: p.role.casefold(),
    )

    skills_for_role: dict[str, list[dict[str, Any]]] = {p.role: [] for p in ordered_profiles}
    for skill_entry in taxonomy.skill_meta:
        mapped_roles = [r for r in skill_entry.ecsf_roles if r in valid_roles]
        if not mapped_roles:
            continue
        payload = {
            "skill": skill_entry.skill,
            "ecsf_roles": mapped_roles,
            "nice_ids": list(skill_entry.nice_ids),
            "aliases": list(skill_entry.aliases),
        }
        for role_name in mapped_roles:
            skills_for_role[role_name].append(payload)

    certs_for_role: dict[str, list[dict[str, Any]]] = {p.role: [] for p in ordered_profiles}
    for cert_entry in taxonomy.cert_meta:
        mapped_roles = [r for r in cert_entry.ecsf_roles if r in valid_roles]
        if not mapped_roles:
            continue
        cert_item = {
            "certification": cert_entry.certification,
            "issuer": cert_entry.issuer,
            "aliases": list(cert_entry.aliases),
        }
        for role_name in mapped_roles:
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

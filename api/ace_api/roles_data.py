"""ENISA ECSF role profiles, skill mappings, and certification catalog for Role Explorer (#14)."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
import os
from pathlib import Path
import re
from typing import Any


@dataclass(frozen=True)
class EcsfRoleProfile:
    role: str
    slug: str
    focus_area: str
    summary: str


ECSF_ROLE_PROFILES: tuple[EcsfRoleProfile, ...] = (
    EcsfRoleProfile(
        role="Chief Information Security Officer (CISO)",
        slug="chief-information-security-officer-ciso",
        focus_area="Governance & Leadership",
        summary=(
            "Leads enterprise cybersecurity strategy, governance, policy, regulatory alignment, "
            "and executive risk reporting across the organization."
        ),
    ),
    EcsfRoleProfile(
        role="Cyber Incident Responder",
        slug="cyber-incident-responder",
        focus_area="Defense & Operations",
        summary=(
            "Monitors security telemetry, detects and triages active intrusions, coordinates "
            "containment and eradication, and leads post-incident recovery."
        ),
    ),
    EcsfRoleProfile(
        role="Cyber Legal, Policy and Compliance Officer",
        slug="cyber-legal-policy-and-compliance-officer",
        focus_area="Governance & Leadership",
        summary=(
            "Aligns organizational operations with cybersecurity legislation, privacy regulations "
            "(GDPR, NIS2), contractual obligations, and internal security policies."
        ),
    ),
    EcsfRoleProfile(
        role="Cyber Threat Intelligence Specialist",
        slug="cyber-threat-intelligence-specialist",
        focus_area="Offensive & Intelligence",
        summary=(
            "Collects, correlates, and analyses adversary TTPs, indicators of compromise, and "
            "threat feeds to produce actionable defensive intelligence."
        ),
    ),
    EcsfRoleProfile(
        role="Cybersecurity Architect",
        slug="cybersecurity-architect",
        focus_area="Architecture & Engineering",
        summary=(
            "Designs resilient, zero-trust, and defense-in-depth architectures across cloud, "
            "network, identity, and operational technology environments."
        ),
    ),
    EcsfRoleProfile(
        role="Cybersecurity Auditor",
        slug="cybersecurity-auditor",
        focus_area="Governance & Leadership",
        summary=(
            "Performs independent assessments of technical controls, management systems (ISO 27001), "
            "and compliance posture against regulatory and industry standards."
        ),
    ),
    EcsfRoleProfile(
        role="Cybersecurity Educator",
        slug="cybersecurity-educator",
        focus_area="Education & Research",
        summary=(
            "Develops and delivers cybersecurity curricula, workforce training programmes, "
            "and phishing/security awareness initiatives."
        ),
    ),
    EcsfRoleProfile(
        role="Cybersecurity Implementer",
        slug="cybersecurity-implementer",
        focus_area="Architecture & Engineering",
        summary=(
            "Deploys, configures, and maintains security controls, DevSecOps pipelines, "
            "cloud/container hardening, and secure software engineering practices."
        ),
    ),
    EcsfRoleProfile(
        role="Cybersecurity Researcher",
        slug="cybersecurity-researcher",
        focus_area="Education & Research",
        summary=(
            "Investigates emerging vulnerabilities, cryptographic protocols, malware internals, "
            "and novel defensive or detection mechanisms."
        ),
    ),
    EcsfRoleProfile(
        role="Cybersecurity Risk Manager",
        slug="cybersecurity-risk-manager",
        focus_area="Governance & Leadership",
        summary=(
            "Identifies, quantifies, and prioritizes cyber and third-party supplier risks, "
            "driving treatment plans and business continuity readiness."
        ),
    ),
    EcsfRoleProfile(
        role="Digital Forensics Investigator",
        slug="digital-forensics-investigator",
        focus_area="Defense & Operations",
        summary=(
            "Acquires, preserves, and analyses volatile memory, disk artifacts, and network captures "
            "to reconstruct security incidents and support legal proceedings."
        ),
    ),
    EcsfRoleProfile(
        role="Penetration Tester",
        slug="penetration-tester",
        focus_area="Offensive & Intelligence",
        summary=(
            "Simulates real-world cyberattacks against applications, networks, and human processes "
            "to uncover exploitable weaknesses before adversaries do."
        ),
    ),
)


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")


def _split_pipe(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.split("|") if part.strip())


def _resolve_taxonomy_dir() -> Path | None:
    configured = os.environ.get("ACE_TAXONOMY_DIR", "").strip()
    if configured:
        candidate = Path(configured)
        return candidate if candidate.is_dir() else None
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "shared" / "taxonomy"
        if candidate.is_dir():
            return candidate
    return None


@lru_cache(maxsize=8)
def _load_role_catalog_from_dir(taxonomy_path: str | None) -> dict[str, Any]:
    """Load ECSF roles, skills, and certifications strictly from the taxonomy CSV files."""
    profiles_by_name: dict[str, EcsfRoleProfile] = {
        profile.role: profile for profile in ECSF_ROLE_PROFILES
    }
    skills_by_name: dict[str, dict[str, Any]] = {}
    certs_by_name: dict[str, dict[str, Any]] = {}

    if taxonomy_path is not None:
        taxonomy_dir = Path(taxonomy_path)

        canonical_roles = taxonomy_dir / "ecsf_roles.csv"
        if canonical_roles.is_file():
            try:
                with canonical_roles.open(newline="", encoding="utf-8") as handle:
                    for row in csv.DictReader(handle):
                        role_name = (row.get("role") or "").strip()
                        if not role_name:
                            continue
                        existing = profiles_by_name.get(role_name)
                        summary = (row.get("summary") or "").strip() or (
                            existing.summary
                            if existing
                            else f"ENISA European Cybersecurity Skills Framework profile: {role_name}."
                        )
                        profiles_by_name[role_name] = EcsfRoleProfile(
                            role=role_name,
                            slug=existing.slug if existing else _slugify(role_name),
                            focus_area=existing.focus_area if existing else "Cybersecurity Practice",
                            summary=summary,
                        )
            except OSError:
                pass

        canonical_skills = taxonomy_dir / "skills.csv"
        skill_sources = (
            (canonical_skills,)
            if canonical_skills.is_file()
            else tuple(sorted((taxonomy_dir / "contributions").glob("*_skills.csv")))
        )
        for source in skill_sources:
            try:
                with source.open(newline="", encoding="utf-8") as handle:
                    for row in csv.DictReader(handle):
                        skill = (row.get("skill") or "").strip()
                        roles = _split_pipe(row.get("ecsf_roles"))
                        if not skill or not roles:
                            continue
                        skills_by_name[skill.casefold()] = {
                            "skill": skill,
                            "ecsf_roles": list(roles),
                            "nice_ids": list(_split_pipe(row.get("nice_ids"))),
                            "aliases": list(_split_pipe(row.get("aliases"))),
                        }
                        for role_name in roles:
                            if role_name not in profiles_by_name:
                                profiles_by_name[role_name] = EcsfRoleProfile(
                                    role=role_name,
                                    slug=_slugify(role_name),
                                    focus_area="Cybersecurity Practice",
                                    summary=f"ENISA European Cybersecurity Skills Framework profile: {role_name}.",
                                )
            except OSError:
                continue

        canonical_certs = taxonomy_dir / "certifications.csv"
        cert_sources = (
            (canonical_certs,)
            if canonical_certs.is_file()
            else tuple(sorted((taxonomy_dir / "contributions").glob("*_certifications.csv")))
        )
        for source in cert_sources:
            try:
                with source.open(newline="", encoding="utf-8") as handle:
                    for row in csv.DictReader(handle):
                        cert = (row.get("certification") or "").strip()
                        if not cert:
                            continue
                        certs_by_name[cert.casefold()] = {
                            "certification": cert,
                            "issuer": (row.get("issuer") or "").strip(),
                            "aliases": list(_split_pipe(row.get("aliases"))),
                            "ecsf_roles": list(_split_pipe(row.get("ecsf_roles"))),
                        }
            except OSError:
                continue

    ordered_profiles = sorted(profiles_by_name.values(), key=lambda p: p.role.casefold())
    skills_for_role: dict[str, list[dict[str, Any]]] = {p.role: [] for p in ordered_profiles}

    for entry in sorted(skills_by_name.values(), key=lambda item: item["skill"].casefold()):
        for role_name in entry["ecsf_roles"]:
            if role_name in skills_for_role:
                skills_for_role[role_name].append(entry)

    certs_for_role: dict[str, list[dict[str, Any]]] = {p.role: [] for p in ordered_profiles}
    for entry in sorted(certs_by_name.values(), key=lambda item: item["certification"].casefold()):
        cert_item = {
            "certification": entry["certification"],
            "issuer": entry["issuer"],
            "aliases": entry["aliases"],
        }
        mapped_roles = entry["ecsf_roles"] or list(certs_for_role.keys())
        for role_name in mapped_roles:
            if role_name in certs_for_role:
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
    return _load_role_catalog_from_dir(str(resolved.resolve()) if resolved is not None else None)


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
                "focus_area": profile.focus_area,
                "overlap": overlap,
                "shared_skills": shared,
                "source": "taxonomy",
            }
        )

    related.sort(key=lambda item: (-item["overlap"], item["role"].casefold()))
    return related
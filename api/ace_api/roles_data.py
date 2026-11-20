"""ENISA ECSF role profiles, skill mappings, and certification catalog for Role Explorer (#14)."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
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

# Built-in baseline from shared/taxonomy/contributions so api/Dockerfile works standalone.
_BUILTIN_SKILLS: tuple[tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]], ...] = (
    ("AWS Security", ("Cybersecurity Architect", "Cybersecurity Implementer"), ("NF-COM-004", "K0863", "K0898"), ("Amazon Web Services Security", "AWS Cloud Security")),
    ("Azure", ("Cybersecurity Architect", "Cybersecurity Implementer"), ("NF-COM-004", "K0863", "K0898"), ("Microsoft Azure", "Azure Security", "Azure Cloud Security")),
    ("Burp Suite", ("Penetration Tester",), (), ("Burp", "Burp Suite Professional")),
    ("Business continuity", ("Chief Information Security Officer (CISO)", "Cybersecurity Risk Manager", "Cyber Incident Responder"), (), ("Business continuity management", "BCM")),
    ("Cloud Security", ("Cybersecurity Architect", "Cybersecurity Implementer"), ("NF-COM-004", "K0863", "K0898"), ("Cloud Cybersecurity", "Cloud Infrastructure Security")),
    ("Compliance", ("Cyber Legal, Policy and Compliance Officer", "Cybersecurity Auditor"), (), ("Regulatory compliance", "Security compliance", "IT compliance")),
    ("Container security", ("Cybersecurity Implementer", "Cybersecurity Architect"), (), ("Container image security",)),
    ("Credential Dumping", ("Penetration Tester",), (), ("Credential Harvesting", "Pass-the-Hash")),
    ("Cryptography", ("Cybersecurity Architect", "Cybersecurity Implementer", "Cybersecurity Researcher"), (), ("Encryption", "PKI", "Public key infrastructure")),
    ("Data privacy", ("Cyber Legal, Policy and Compliance Officer", "Chief Information Security Officer (CISO)"), (), ("Data protection",)),
    ("Detection engineering", ("Cyber Incident Responder", "Cybersecurity Implementer"), (), ("Security detection engineering", "Detection rule engineering")),
    ("DevSecOps", ("Cybersecurity Implementer", "Cybersecurity Architect"), (), ("Secure DevOps",)),
    ("Digital forensics", ("Digital Forensics Investigator", "Cyber Incident Responder"), (), ("Computer forensics", "Forensic analysis", "DFIR")),
    ("Docker", ("Cybersecurity Implementer",), ("K1373",), ("Docker Security", "Docker Containers")),
    ("Endpoint security", ("Cybersecurity Implementer",), (), ("Endpoint protection", "EDR", "Endpoint detection and response")),
    ("Firewalls", ("Cybersecurity Architect", "Cybersecurity Implementer"), ("K0877", "K0878", "S0618", "S0950"), ("Firewall", "Firewall Administration", "Network Firewall")),
    ("GDPR", ("Cyber Legal, Policy and Compliance Officer", "Cybersecurity Auditor", "Chief Information Security Officer (CISO)"), (), ("General Data Protection Regulation", "DSGVO", "RGPD")),
    ("Identity and access management", ("Cybersecurity Architect", "Cybersecurity Implementer"), (), ("IAM", "Identity management", "Access management")),
    ("Incident response", ("Cyber Incident Responder",), (), ("Incident handling", "Security incident response")),
    ("Indicator of compromise analysis", ("Cyber Threat Intelligence Specialist", "Cyber Incident Responder"), (), ("IOC analysis", "Indicators of compromise analysis")),
    ("Infrastructure as code", ("Cybersecurity Implementer",), (), ("IaC",)),
    ("Intelligence requirements management", ("Cyber Threat Intelligence Specialist",), (), ("Threat intelligence requirements", "Priority intelligence requirements")),
    ("Intrusion Detection", ("Cyber Incident Responder",), (), ("Intrusion Detection Systems", "Snort", "Suricata")),
    ("Intrusion Prevention", ("Cyber Incident Responder",), (), ("Intrusion Prevention Systems",)),
    ("ISO 27001", ("Cybersecurity Auditor", "Cybersecurity Risk Manager", "Cyber Legal, Policy and Compliance Officer", "Chief Information Security Officer (CISO)"), (), ("ISO/IEC 27001", "ISO27001", "ISO 27001:2022")),
    ("Kali Linux", ("Penetration Tester",), (), ("Kali",)),
    ("Kubernetes", ("Cybersecurity Architect", "Cybersecurity Implementer"), ("K1373",), ("K8s", "Kubernetes Security", "K8s Security")),
    ("Lateral Movement", ("Penetration Tester",), (), ("Internal Pivoting", "Network Pivoting")),
    ("Linux", ("Cybersecurity Implementer",), ("NF-COM-009", "K0744", "S0606", "S0678"), ("GNU/Linux", "Linux Security", "Linux Administration")),
    ("Malware analysis", ("Cyber Threat Intelligence Specialist", "Digital Forensics Investigator", "Cybersecurity Researcher"), (), ("Malware reverse engineering",)),
    ("Malware Triage", ("Digital Forensics Investigator",), (), ("Artifact Analysis",)),
    ("Memory Forensics", ("Digital Forensics Investigator",), (), ("Volatile Memory Analysis", "RAM Analysis", "Volatility")),
    ("Metasploit", ("Penetration Tester",), (), ("Metasploit Framework",)),
    ("MISP", ("Cyber Threat Intelligence Specialist", "Cyber Incident Responder"), (), ("MISP threat intelligence platform", "Malware Information Sharing Platform")),
    ("MITRE ATT&CK", ("Cyber Threat Intelligence Specialist", "Cyber Incident Responder", "Penetration Tester"), (), ("ATT&CK", "MITRE ATTACK", "ATT&CK framework")),
    ("Network Security", ("Cybersecurity Architect", "Cybersecurity Implementer"), ("K1014", "S0077", "S0418", "S0583"), ("Network Cybersecurity", "Secure Network Architecture")),
    ("Network Security Monitoring", ("Cyber Incident Responder",), (), ("Zeek",)),
    ("Network traffic analysis", ("Cyber Incident Responder", "Digital Forensics Investigator"), (), ("Packet analysis", "Traffic analysis")),
    ("NIS2", ("Cyber Legal, Policy and Compliance Officer", "Chief Information Security Officer (CISO)", "Cybersecurity Risk Manager"), (), ("NIS 2", "NIS-2", "NIS2 Directive")),
    ("NIST Cybersecurity Framework", ("Cybersecurity Risk Manager", "Cybersecurity Auditor", "Chief Information Security Officer (CISO)"), (), ("NIST", "NIST CSF", "NIST 800-53")),
    ("Nmap", ("Penetration Tester",), (), ("Network Mapper",)),
    ("Open-source intelligence", ("Cyber Threat Intelligence Specialist",), (), ("OSINT", "Open source intelligence")),
    ("OT security", ("Cybersecurity Implementer", "Cybersecurity Architect"), (), ("Operational technology security", "ICS security", "SCADA security")),
    ("Penetration testing", ("Penetration Tester",), (), ("Pentesting", "Pen testing", "Pentest", "Ethical hacking")),
    ("Post-Exploitation", ("Penetration Tester",), (), ("Post Exploitation",)),
    ("Privilege Escalation", ("Penetration Tester",), (), ("PrivEsc", "Local Privilege Escalation")),
    ("Python", ("Cybersecurity Implementer", "Cybersecurity Researcher", "Penetration Tester", "Cyber Threat Intelligence Specialist"), (), ("Python scripting", "Python programming")),
    ("Red teaming", ("Penetration Tester",), (), ("Red team", "Adversary emulation")),
    ("Risk assessment", ("Cybersecurity Risk Manager", "Cybersecurity Auditor", "Chief Information Security Officer (CISO)"), (), ("Risk analysis", "Security risk assessment")),
    ("Risk management", ("Cybersecurity Risk Manager", "Cybersecurity Auditor", "Chief Information Security Officer (CISO)"), (), ("Cyber risk management", "Information risk management")),
    ("Root Cause Analysis", ("Cyber Incident Responder",), (), ("Root Cause Investigation",)),
    ("Scripting", ("Cybersecurity Implementer", "Cyber Incident Responder"), (), ("Shell scripting", "Bash", "PowerShell")),
    ("Secure software development", ("Cybersecurity Implementer",), (), ("Secure coding", "Secure SDLC", "Secure development lifecycle")),
    ("Security architecture", ("Cybersecurity Architect", "Chief Information Security Officer (CISO)"), (), ("Enterprise security architecture",)),
    ("Security auditing", ("Cybersecurity Auditor",), (), ("Security audit", "IT audit", "Information security audit")),
    ("Security awareness training", ("Cybersecurity Educator", "Chief Information Security Officer (CISO)"), (), ("Security awareness", "Awareness training", "Phishing simulation")),
    ("Security monitoring", ("Cyber Incident Responder",), (), ("Security operations", "SOC operations", "Log monitoring")),
    ("Security policy", ("Chief Information Security Officer (CISO)", "Cyber Legal, Policy and Compliance Officer"), (), ("Security policies", "Information security policy")),
    ("SIEM", ("Cyber Incident Responder", "Cyber Threat Intelligence Specialist"), (), ("Security Information and Event Management",)),
    ("Sigma rules", ("Cyber Incident Responder", "Cybersecurity Implementer"), (), ("Sigma detection rules",)),
    ("Social Engineering", ("Penetration Tester",), (), ("Social Engineering Testing", "Pretexting")),
    ("Splunk", ("Cyber Incident Responder", "Cyber Threat Intelligence Specialist"), (), ("Splunk Enterprise", "Splunk Enterprise Security", "Splunk ES")),
    ("STIX", ("Cyber Threat Intelligence Specialist",), (), ("Structured Threat Information Expression", "STIX 2.1")),
    ("TAXII", ("Cyber Threat Intelligence Specialist", "Cybersecurity Implementer"), (), ("Trusted Automated Exchange of Intelligence Information", "TAXII 2.1")),
    ("Terraform", ("Cybersecurity Architect", "Cybersecurity Implementer"), ("K0755", "K1121", "K1122", "K1369"), ("HashiCorp Terraform", "Terraform Security")),
    ("Third-party risk management", ("Cybersecurity Risk Manager", "Cybersecurity Auditor"), (), ("Vendor risk management", "Supplier risk management", "TPRM")),
    ("Threat actor profiling", ("Cyber Threat Intelligence Specialist",), (), ("Adversary profiling", "Threat actor analysis")),
    ("Threat hunting", ("Cyber Threat Intelligence Specialist", "Cyber Incident Responder"), (), ("Cyber threat hunting",)),
    ("Threat intelligence", ("Cyber Threat Intelligence Specialist",), (), ("Cyber threat intelligence", "CTI", "Threat intel")),
    ("Threat intelligence feed management", ("Cyber Threat Intelligence Specialist",), (), ("Threat feed management", "CTI feed management")),
    ("Threat intelligence reporting", ("Cyber Threat Intelligence Specialist",), (), ("CTI reporting", "Threat intelligence dissemination")),
    ("Vulnerability management", ("Cybersecurity Implementer", "Cybersecurity Risk Manager", "Penetration Tester"), (), ("Vulnerability assessment", "Vulnerability scanning")),
    ("Vulnerability research", ("Cybersecurity Researcher", "Penetration Tester"), (), ("Exploit development",)),
    ("Web application security", ("Penetration Tester", "Cybersecurity Implementer"), (), ("Application security", "AppSec", "OWASP", "OWASP Top 10")),
    ("Wireshark", ("Cyber Incident Responder", "Digital Forensics Investigator"), (), ()),
    ("YARA", ("Cyber Threat Intelligence Specialist", "Digital Forensics Investigator", "Cybersecurity Researcher"), (), ("YARA rules", "YARA rule development")),
    ("Zero trust", ("Cybersecurity Architect",), (), ("Zero trust architecture", "ZTA")),
)

_BUILTIN_CERTIFICATIONS: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("CISSP", "ISC2", ("Certified Information Systems Security Professional",), ("Chief Information Security Officer (CISO)", "Cybersecurity Architect", "Cybersecurity Risk Manager")),
    ("CISM", "ISACA", ("Certified Information Security Manager",), ("Chief Information Security Officer (CISO)", "Cybersecurity Risk Manager")),
    ("CISA", "ISACA", ("Certified Information Systems Auditor",), ("Cybersecurity Auditor", "Cyber Legal, Policy and Compliance Officer")),
    ("CIPP/E", "IAPP", ("Certified Information Privacy Professional/Europe", "CIPP-E"), ("Cyber Legal, Policy and Compliance Officer", "Chief Information Security Officer (CISO)")),
    ("BSI IT-Grundschutz Practitioner", "Bundesamt fuer Sicherheit in der Informationstechnik", ("IT-Grundschutz-Praktiker", "IT-Grundschutz Practitioner"), ("Cybersecurity Auditor", "Cybersecurity Risk Manager", "Cybersecurity Implementer")),
    ("CCSK", "Cloud Security Alliance", ("Certificate of Cloud Security Knowledge",), ("Cybersecurity Architect", "Cybersecurity Implementer")),
    ("CCNP Security", "Cisco", ("Cisco Certified Network Professional Security",), ("Cybersecurity Architect", "Cybersecurity Implementer")),
    ("Cisco CyberOps Associate", "Cisco", ("CCNA CyberOps", "Cisco Certified CyberOps Associate"), ("Cyber Incident Responder", "Cyber Threat Intelligence Specialist")),
    ("BTL1", "Security Blue Team", ("Blue Team Level 1",), ("Cyber Incident Responder", "Cyber Threat Intelligence Specialist")),
    ("BTL2", "Security Blue Team", ("Blue Team Level 2",), ("Cyber Incident Responder", "Digital Forensics Investigator", "Cyber Threat Intelligence Specialist")),
    ("ECIH", "EC-Council", ("EC-Council Certified Incident Handler",), ("Cyber Incident Responder",)),
    ("Splunk Core Certified Consultant", "Splunk", ("Splunk Certified Core Consultant",), ("Cyber Incident Responder", "Cyber Threat Intelligence Specialist")),
    ("GIAC GCFE", "GIAC", ("GCFE", "GIAC Certified Forensic Examiner"), ("Digital Forensics Investigator",)),
    ("GIAC GNFA", "GIAC", ("GNFA", "GIAC Network Forensic Analyst"), ("Digital Forensics Investigator", "Cyber Incident Responder")),
    ("CHFI", "EC-Council", ("Computer Hacking Forensic Investigator",), ("Digital Forensics Investigator",)),
    ("CEH", "EC-Council", ("Certified Ethical Hacker",), ("Penetration Tester", "Cybersecurity Educator")),
    ("eJPT", "INE Security", ("eLearnSecurity Junior Penetration Tester",), ("Penetration Tester", "Cybersecurity Educator")),
    ("eCPPT", "INE Security", ("eLearnSecurity Certified Professional Penetration Tester",), ("Penetration Tester",)),
    ("PNPT", "TCM Security", ("Practical Network Penetration Tester",), ("Penetration Tester",)),
    ("GIAC GWAPT", "GIAC", ("GWAPT", "GIAC Web Application Penetration Tester"), ("Penetration Tester", "Cybersecurity Implementer")),
    ("OSWE", "OffSec", ("Offensive Security Web Expert",), ("Penetration Tester", "Cybersecurity Researcher")),
    ("OSEP", "OffSec", ("Offensive Security Experienced Penetration Tester",), ("Penetration Tester",)),
    ("OSED", "OffSec", ("Offensive Security Exploit Developer",), ("Cybersecurity Researcher", "Penetration Tester")),
    ("CRTO", "Zero-Point Security", ("Certified Red Team Operator",), ("Penetration Tester",)),
    ("CRTE", "Altered Security", ("Certified Red Team Expert",), ("Penetration Tester",)),
)


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")


def _split_pipe(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.split("|") if part.strip())


def _find_repo_taxonomy_dir() -> Path | None:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "shared" / "taxonomy"
        if candidate.is_dir():
            return candidate
    return None


@lru_cache(maxsize=1)
def load_role_catalog() -> dict[str, Any]:
    """Load the 12 ECSF role profiles and their mapped skills and certifications."""
    profiles_by_name: dict[str, EcsfRoleProfile] = {
        profile.role: profile for profile in ECSF_ROLE_PROFILES
    }
    skills_by_name: dict[str, dict[str, Any]] = {}

    for skill, roles, nice_ids, aliases in _BUILTIN_SKILLS:
        skills_by_name[skill.casefold()] = {
            "skill": skill,
            "ecsf_roles": list(roles),
            "nice_ids": list(nice_ids),
            "aliases": list(aliases),
        }

    taxonomy_dir = _find_repo_taxonomy_dir()
    if taxonomy_dir is not None:
        canonical_skills = taxonomy_dir / "skills.csv"
        skill_sources = (
            (canonical_skills,)
            if canonical_skills.is_file()
            else tuple(sorted((taxonomy_dir / "contributions").glob("*_skills.csv")))
        )
        for source in skill_sources:
            try:
                with source.open(newline="", encoding="utf-8") as handle:
                    reader = csv.DictReader(handle)
                    for row in reader:
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

    ordered_profiles = sorted(profiles_by_name.values(), key=lambda p: p.role.casefold())
    skills_for_role: dict[str, list[dict[str, Any]]] = {p.role: [] for p in ordered_profiles}

    for entry in sorted(skills_by_name.values(), key=lambda item: item["skill"].casefold()):
        for role_name in entry["ecsf_roles"]:
            if role_name in skills_for_role:
                skills_for_role[role_name].append(entry)

    certs_for_role: dict[str, list[dict[str, Any]]] = {p.role: [] for p in ordered_profiles}
    for cert, issuer, aliases, roles in sorted(_BUILTIN_CERTIFICATIONS, key=lambda c: c[0].casefold()):
        cert_entry = {
            "certification": cert,
            "issuer": issuer,
            "aliases": list(aliases),
        }
        for role_name in roles:
            if role_name in certs_for_role:
                certs_for_role[role_name].append(cert_entry)

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
                "focus_area": profile.focus_area,
                "overlap": overlap,
                "shared_skills": shared,
                "source": "taxonomy",
            }
        )

    related.sort(key=lambda item: (-item["overlap"], item["role"].casefold()))
    return related
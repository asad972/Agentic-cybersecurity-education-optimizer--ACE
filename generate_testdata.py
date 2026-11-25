import json
import os
import random
import uuid
from datetime import datetime, timedelta

DEFAULT_SEED = 42
DEFAULT_COUNT = 200

# Países del alcance europeo (EU27 + UK, Suiza, etc.)
EUROPEAN_COUNTRIES = [
    "DE",
    "FR",
    "HU",
    "ES",
    "IT",
    "NL",
    "PL",
    "SE",
    "IE",
    "BE",
    "AT",
    "DK",
    "FI",
    "CZ",
    "PT",
    "RO",
]

JOB_ROLES = [
    (
        "SOC Analyst",
        "Cyber Incident Responder",
        ["SIEM", "Incident response", "Wireshark", "Splunk", "Python"],
    ),
    (
        "Penetration Tester",
        "Penetration Tester",
        ["Penetration testing", "Metasploit", "Burp Suite", "Kali Linux", "Nmap"],
    ),
    (
        "Cybersecurity Engineer",
        "Cybersecurity Architect",
        ["Firewalls", "Kubernetes", "Docker", "Network security", "Linux"],
    ),
    (
        "Security Consultant",
        "Cybersecurity Risk Manager",
        ["ISO 27001", "Risk assessment", "GDPR", "NIST", "Compliance"],
    ),
    (
        "Cloud Security Specialist",
        "Cybersecurity Architect",
        ["AWS Security", "Azure", "Cloud security", "Terraform", "Docker"],
    ),
]

COMPANIES = [
    "Siemens",
    "Airbus",
    "Deutsche Bank",
    "Orange Cyberdefense",
    "Thales",
    "SAP",
    "Santander",
    "Nokia",
    "Ericsson",
    "CyberArk",
    "Deloitte",
    "PwC",
    "Capgemini",
]
SOURCES_JOBS = ["adzuna", "arbeitsagentur", "francetravail"]
SOURCES_PROFILES = ["stackoverflow"]


def _deterministic_uuid(rng: random.Random) -> str:
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def generate_jobs(count: int = DEFAULT_COUNT, *, seed: int = DEFAULT_SEED) -> list[dict]:
    rng = random.Random(seed)
    jobs = []
    base_date = datetime(2026, 10, 1)

    for i in range(count):
        title, _role, skills = rng.choice(JOB_ROLES)
        country = rng.choice(EUROPEAN_COUNTRIES)
        company = rng.choice(COMPANIES)
        days_offset = rng.randint(0, 30)
        posted_date = (base_date - timedelta(days=days_offset)).strftime("%Y-%m-%d")

        job = {
            "event_id": _deterministic_uuid(rng),
            "source": rng.choice(SOURCES_JOBS),
            "country": country,
            "posted_date": posted_date,
            "job_title": title,
            "company": company,
            "description": (
                f"We are hiring a {title} at {company} in {country}. "
                f"Required skills and technologies: {', '.join(skills)}. "
                f"Experience in cybersecurity operations and incident handling is required."
            ),
            "url": f"https://jobs.example.com/{country.lower()}/{i + 1000}",
        }
        jobs.append(job)
    return jobs


def generate_profiles(count: int = DEFAULT_COUNT, *, seed: int = DEFAULT_SEED) -> list[dict]:
    rng = random.Random(seed + 1)
    profiles = []
    base_date = datetime(2026, 9, 15)

    for _ in range(count):
        title, role, skills = rng.choice(JOB_ROLES)
        country = rng.choice(EUROPEAN_COUNTRIES)
        days_offset = rng.randint(0, 60)
        profile_date = (base_date - timedelta(days=days_offset)).strftime("%Y-%m-%d")
        years_experience = rng.randint(1, 15)

        # Seleccionar un subconjunto aleatorio de skills del profesional
        profile_skills = rng.sample(skills, k=rng.randint(2, len(skills)))

        profile = {
            "event_id": _deterministic_uuid(rng),
            "source": rng.choice(SOURCES_PROFILES),
            "country": country,
            "profile_date": profile_date,
            "headline": f"{title} ({role})",
            "text": (
                f"{title} with {years_experience} years of cybersecurity experience. "
                f"Core skills and tools: {', '.join(profile_skills)}. "
                f"Experienced in {role} workflows and security operations."
            ),
        }
        profiles.append(profile)
    return profiles


def main(seed: int = DEFAULT_SEED, count: int = DEFAULT_COUNT) -> None:
    target_dir = os.path.join("shared", "testdata")
    os.makedirs(target_dir, exist_ok=True)

    jobs_path = os.path.join(target_dir, "jobs_sample.jsonl")
    profiles_path = os.path.join(target_dir, "profiles_sample.jsonl")

    # Guardar ofertas de trabajo en formato JSON Lines
    jobs = generate_jobs(count, seed=seed)
    with open(jobs_path, "w", encoding="utf-8", newline="\n") as f:
        for job in jobs:
            f.write(json.dumps(job, ensure_ascii=False) + "\n")
    print(f"Generadas {len(jobs)} ofertas de prueba en: {jobs_path}")

    # Guardar perfiles en formato JSON Lines
    profiles = generate_profiles(count, seed=seed)
    with open(profiles_path, "w", encoding="utf-8", newline="\n") as f:
        for profile in profiles:
            f.write(json.dumps(profile, ensure_ascii=False) + "\n")
    print(f"Generados {len(profiles)} perfiles de prueba en: {profiles_path}")


if __name__ == "__main__":
    main()

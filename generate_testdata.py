import json
import os
import random
import uuid
from datetime import datetime, timedelta

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
        "Cyber Threat Intelligence Specialist",
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


def generate_jobs(count=200):
    jobs = []
    base_date = datetime(2026, 10, 1)

    for i in range(count):
        title, role, skills = random.choice(JOB_ROLES)
        country = random.choice(EUROPEAN_COUNTRIES)
        company = random.choice(COMPANIES)
        days_offset = random.randint(0, 30)
        posted_date = (base_date - timedelta(days=days_offset)).strftime(
            "%Y-%m-%d"
        )

        job = {
            "event_id": str(uuid.uuid4()),
            "source": random.choice(SOURCES_JOBS),
            "country": country,
            "posted_date": posted_date,
            "job_title": title,
            "company": company,
            "description": (
                f"We are hiring a {title} at {company} in {country}. "
                f"Required skills and technologies: {', '.join(skills)}. "
                f"Experience in cybersecurity operations and incident handling is required."
            ),
            "url": f"https://jobs.example.com/{country.lower()}/{i+1000}",
        }
        jobs.append(job)
    return jobs


def generate_profiles(count=200):
    profiles = []
    base_date = datetime(2026, 9, 15)

    for _ in range(count):
        title, role, skills = random.choice(JOB_ROLES)
        country = random.choice(EUROPEAN_COUNTRIES)
        days_offset = random.randint(0, 60)
        profile_date = (base_date - timedelta(days=days_offset)).strftime(
            "%Y-%m-%d"
        )

        # Seleccionar un subconjunto aleatorio de skills del profesional
        profile_skills = random.sample(skills, k=random.randint(2, len(skills)))

        profile = {
            "event_id": str(uuid.uuid4()),
            "source": "stackoverflow",
            "country": country,
            "profile_date": profile_date,
            "role": title,
            "skills": profile_skills,
            "years_experience": random.randint(1, 15),
        }
        profiles.append(profile)
    return profiles


def main():
    target_dir = os.path.join("shared", "testdata")
    os.makedirs(target_dir, exist_ok=True)

    jobs_path = os.path.join(target_dir, "jobs_sample.jsonl")
    profiles_path = os.path.join(target_dir, "profiles_sample.jsonl")

    # Guardar ofertas de trabajo en formato JSON Lines
    jobs = generate_jobs(200)
    with open(jobs_path, "w", encoding="utf-8") as f:
        for job in jobs:
            f.write(json.dumps(job, ensure_ascii=False) + "\n")
    print(f"Generadas {len(jobs)} ofertas de prueba en: {jobs_path}")

    # Guardar perfiles en formato JSON Lines
    profiles = generate_profiles(200)
    with open(profiles_path, "w", encoding="utf-8") as f:
        for profile in profiles:
            f.write(json.dumps(profile, ensure_ascii=False) + "\n")
    print(f"Generados {len(profiles)} perfiles de prueba en: {profiles_path}")


if __name__ == "__main__":
    main()  
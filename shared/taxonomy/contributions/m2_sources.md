# M2 taxonomy contribution sources

## Scope

This contribution proposes the Skills and Certifications that S3 (Top 10 Skills and Top 10 Certifications per Country) and S4 (daily Demand per Skill and Country) count from job ads: 44 Skills and 30 Certifications. M1 reconciles it with the other members' proposals into the canonical v1 taxonomy; these files do not replace `skills.csv`, `certifications.csv` or `ecsf_roles.csv`.

The format follows the M3 contribution: one row per entry, multi-value fields separated by `|`, rows sorted by name.

## Decisions

- **No overlap with M3.** Cloud Security, Network Security, Firewalls, Linux, Docker, Kubernetes, Terraform, AWS Security and Azure are already in `m3_skills.csv`, and AWS Certified Security - Specialty, CCSP and Professional Cloud Security Engineer in `m3_certifications.csv`, so they are left out here. No name or alias in this file points to an M3 entry.
- **Tools are Skills of their own** when job ads name them, as in the M3 contribution: Splunk, Wireshark, Nmap, Metasploit, Kali Linux and Burp Suite each have a row, so every term in `shared/testdata/` maps to exactly one entry.
- **Narrow aliases.** An alias is only another name for the same thing (`DSGVO` and `RGPD` for GDPR, `Sec+` for CompTIA Security+). Broad phrases such as "Privacy" or "Reverse engineering" are left out, because they can mean more than one Skill.
- **European entries.** NIS2, GDPR with its German and French names, CIPP/E, BSI IT-Grundschutz Practitioner (Germany) and CREST Registered Penetration Tester (UK) cover requirements that are specific to European job ads.
- **AZ-500 stays as its own entry.** Microsoft retired the Azure Security Engineer Associate certification on 2026-08-31 (see `m3_sources.md`), but job ads still ask for it, so it is kept as a separate credential, not as an alias of its SC-500 successor.
- **`nice_ids` is empty for now.** The NICE identifiers still need to be checked against NICE Framework Components v2.2.0 before they are filled in.

## ECSF role mappings

Source: [ENISA European Cybersecurity Skills Framework Role Profiles][ecsf], published September 2022. Every Skill is mapped to the ECSF role profiles whose tasks and skills include it, using the published profile titles. `shared/python/tests/test_taxonomy_contributions.py` checks that every role name matches one of the 12 profiles.

## Certifications

Every certification is listed under its issuer's own name: ISC2, ISACA, CompTIA, EC-Council, GIAC, OffSec, Cisco, Microsoft, IAPP, Cloud Security Alliance, Linux Foundation, CREST, the German Federal Office for Information Security (BSI), and the ISO 27001 training bodies (PECB, TÜV, BSI Group).

[ecsf]: https://www.enisa.europa.eu/sites/default/files/publications/European%20Cybersecurity%20Skills%20Framework%20Role%20Profiles.pdf

# M5 taxonomy contribution sources

## Scope

This proposal covers the education and training vocabulary relevant to M5's
education-programme analysis. The entries are mapped to the ENISA ECSF
**Cybersecurity Educator** role because that profile covers cybersecurity
learning and education responsibilities. Mapping a particular programme to
that role still requires reviewed evidence from the programme.

The shared taxonomy counts cybersecurity skills and certifications. M5's
current education and workforce inputs do not collect certifications, and the
existing M2 and M3 proposals already cover the applicable general cybersecurity
credentials. Therefore `m5_certifications.csv` intentionally contains only
the required header; no additional certification is proposed.

## ECSF source

European Union Agency for Cybersecurity (ENISA), *European Cybersecurity Skills
Framework (ECSF) Role Profiles*, published September 2022:
<https://www.enisa.europa.eu/publications/european-cybersecurity-skills-framework-role-profiles>

The skill names in `m5_skills.csv` are proposed taxonomy labels for the
education and training activities covered by the Cybersecurity Educator
profile. They are not represented as verbatim ECSF skill names. NICE IDs are
left blank until an exact component-level cross-reference is verified.

Programme role mappings are a separate classification: a programme may map
to zero or more of the twelve published ECSF role profiles based on reviewed
curriculum evidence. It is not automatically a Cybersecurity Educator
programme simply because it is an education programme. If evidence is
insufficient, leave `ecsf_roles` empty for review rather than guessing.

## Reconciliation notes

- `Security awareness training` is already proposed in `m2_skills.csv`; M5
  does not duplicate it.
- ECSF role names use the published profile title so the contribution passes
  the shared role-name contract.
- Aliases are limited to close alternate phrasings of each proposed entry.
- The three synthetic demo categories (`Cyber Security Analyst`, `Cloud
  Security Specialist`, and `SOC Analyst`) are not ECSF role names and must not
  be published in `ecsf_roles`.

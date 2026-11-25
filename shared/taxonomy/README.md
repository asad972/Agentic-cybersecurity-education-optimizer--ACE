# Skill Taxonomy (shared contract, v1)

The closed, versioned list (`v1`) of every Skill, Certification and ECSF role ACE counts. S2 maps every extracted mention to an entry here, or reports it as unmapped.

Canonical files (`v1`, reconciled by M1 from all five member proposals in `contributions/`):

| File | Columns | Entries | Source |
| --- | --- | --- | --- |
| `skills.csv` | `skill`, `ecsf_roles`, `nice_ids`, `aliases` | 83 | Reconciled from `contributions/m1..m5_skills.csv` (ECSF role profiles + NICE cross-references) |
| `certifications.csv` | `certification`, `issuer`, `aliases` | 55 | Reconciled from `contributions/m1..m5_certifications.csv` (CISSP, CISM, Security+, OSCP, …) |
| `ecsf_roles.csv` | `role`, `summary` | 12 | The 12 ENISA ECSF role profiles |

`aliases` lists other spellings S2 should map to the entry (e.g. `SIEM` ← `Security Information and Event Management`).

Changes are agreed by the whole team: renaming an entry changes the keys in every count table.

Member proposals and source provenance notes are preserved in `contributions/` (`m1..m5_skills.csv`, `m1..m5_certifications.csv`, `m1..m5_sources.md`).

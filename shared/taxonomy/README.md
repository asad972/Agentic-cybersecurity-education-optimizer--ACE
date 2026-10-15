# Skill Taxonomy (shared contract, M1 merges)

The closed, versioned list of every Skill, Certification and ECSF role ACE counts. S2 maps every extracted mention to an entry here, or reports it as unmapped.

Planned files:

| File | Columns | Source |
| --- | --- | --- |
| `skills.csv` | `skill`, `ecsf_roles`, `nice_ids`, `aliases` | ECSF role profiles (skills and knowledge), NICE cross-references |
| `certifications.csv` | `certification`, `issuer`, `aliases` | Common cybersecurity certifications (CISSP, CISM, Security+, …) |
| `ecsf_roles.csv` | `role`, `summary` | The 12 ECSF role profiles |

`aliases` lists other spellings S2 should map to the entry (e.g. `SIEM` ← `Security Information and Event Management`).

Changes are agreed by the whole team: renaming an entry changes the keys in every count table.

# M4 threat taxonomy contribution

Owner: Roomi Babar Ali (M4 / `rumistein`). Prepared 2026-11-11 against
`master` at `481c80c`. Related task: #2 (all-member taxonomy inputs).

## Scope and format

- `m4_skills.csv`: 13 proposed skills covering threat collection, analysis,
  intelligence exchange, and detection.
- `m4_certifications.csv`: 6 relevant professional certifications.
- These are proposals for M1's canonical taxonomy merge and team review, not
  a replacement for the canonical files or a claim that M4 holds these credentials.
- Headers follow `shared/taxonomy/README.md`. Files are UTF-8 CSV with `|`
  separating multiple roles or aliases. Canonical names are stable matching keys.
- ECSF role assignments are proposed project mappings using ENISA's role titles;
  they are not an official ENISA endorsement of these tools or certifications.
- `nice_ids` is intentionally empty. A precise, versioned NICE crosswalk has
  not been verified for these entries; do not infer IDs from similar labels.
- Certification names follow M2's short-name convention; aliases include the
  official expanded names. Issuers are certification bodies, not course providers.
  EC-Council's stylized `C|TIA` is omitted because `|` is the field delimiter.

## Reuse and integration

M2 already contributes Threat intelligence, Threat hunting, Malware analysis,
Incident response, Digital forensics, Network traffic analysis, SIEM, Python,
and other general security skills. M4 reuses those canonical names and does not
duplicate their rows or aliases. M2 also already supplies related credentials
including GCIH, GCFA, CySA+, and SC-200; they are not repeated here.

The additions distinguish specific tools/standards and competencies from those
broader skills. For example, YARA is a tool skill, while Malware analysis is a
broader competency. Aliases express equivalent names, not related concepts;
bare words such as `attack`, `malware`, `phishing`, and `IOC` are not skill aliases.
The same holds for `Sigma`: alone it also matches Six Sigma and Sigma-Aldrich in
job ads, so the entry is named `Sigma rules` and has no bare `Sigma` alias (#91).

The threat collector's threat categories and S7/S8 event keys are a different
vocabulary from workforce skills. These CSVs do not change collectors, event
schemas, stage counts, or runtime matching. After team approval, M1 can merge
the entries into the canonical taxonomy for the shared matcher. A mapping from
threat categories to skills would need a separate agreed contract.

## Skill sources

Sources checked on 2026-11-11. Skill labels and role mappings below are curated
project proposals informed by these primary sources.

| Proposed entries | Source and relevance |
| --- | --- |
| All ECSF role names | [ENISA ECSF role profiles](https://www.enisa.europa.eu/publications/european-cybersecurity-skills-framework-role-profiles): role vocabulary. |
| MITRE ATT&CK | [MITRE ATT&CK](https://attack.mitre.org/): adversary behavior knowledge base. |
| STIX | [OASIS STIX introduction](https://oasis-open.github.io/cti-documentation/stix/intro.html): structured CTI representation. |
| TAXII | [OASIS TAXII introduction](https://oasis-open.github.io/cti-documentation/taxii/intro.html): CTI exchange protocol. |
| MISP | [MISP project](https://www.misp-project.org/): indicator storage, correlation and sharing. |
| Sigma rules; Detection engineering | [Sigma documentation](https://sigmahq.io/docs/): writing and sharing security detections. Detection engineering is our broader competency label, not a synonym for Sigma rules. |
| YARA | [YARA project](https://virustotal.github.io/yara/): pattern-based rules for malware identification. |
| Indicator of compromise analysis; Intelligence requirements management; Threat intelligence feed management; Threat intelligence reporting | [EC-Council CTIA curriculum](https://www.eccouncil.org/train-certify/certified-threat-intelligence-analyst-ctia/): requirements, collection, indicator evaluation and reporting. |
| Threat actor profiling | [GIAC GCTI objectives](https://www.giac.org/certifications/cyber-threat-intelligence-gcti/): campaign analysis and attribution. |
| Open-source intelligence | [GIAC GOSI](https://www.giac.org/certifications/open-source-intelligence-gosi/): OSINT collection and analysis. |

## Certification sources

| CSV name | Issuer's official page |
| --- | --- |
| CTIA | [EC-Council Certified Threat Intelligence Analyst](https://www.eccouncil.org/train-certify/certified-threat-intelligence-analyst-ctia/) |
| GIAC GCIA | [GIAC Certified Intrusion Analyst](https://www.giac.org/certifications/certified-intrusion-analyst-gcia/) |
| GIAC GCTI | [GIAC Cyber Threat Intelligence](https://www.giac.org/certifications/cyber-threat-intelligence-gcti/) |
| GIAC GDAT | [GIAC Defending Advanced Threats](https://www.giac.org/certifications/defending-advanced-threats-gdat/) |
| GIAC GOSI | [GIAC Open Source Intelligence](https://www.giac.org/certifications/open-source-intelligence-gosi/) |
| GIAC GREM | [GIAC Reverse Engineering Malware](https://www.giac.org/certifications/reverse-engineering-malware-grem/) |

## Validation and review

Run from the repository root with pytest installed:

```bash
python -m pytest shared/python/tests/test_taxonomy_contributions.py -q
```

The existing tests discover both M4 files and check column names, ECSF roles,
multi-value fields, fixture coverage, and name/alias collisions across all
contributions. Re-run after incorporating any new member contributions.

Open a focused PR from `m4/threat-taxonomy` to `master`, referencing #2 (do not
close the all-member issue). Request M1's review and mention all teammates as
required by `CONTRIBUTING.md`. Only the two M4 CSVs and this source note belong
in this contribution.

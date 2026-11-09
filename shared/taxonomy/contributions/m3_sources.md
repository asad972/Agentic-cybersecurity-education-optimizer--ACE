# M3 taxonomy contribution sources

Retrieved and checked on **2026-10-26**.

## Scope

This contribution proposes the cloud and infrastructure security vocabulary
needed by M3's Supply, Skills Gap, drift, and forecasting work. The canonical
Skill names intentionally match the checked-in profile fixtures so Demand and
Supply can use the same aggregation keys. M1 owns reconciliation with the other
members' proposals and the final versioned taxonomy; these files do not replace
the canonical `skills.csv`, `certifications.csv`, or `ecsf_roles.csv`.

Aliases are deliberately narrow. In particular, `IaC` is not treated as a
Terraform alias and `Container Security` is not assigned only to Docker or
Kubernetes, because those phrases can describe multiple technologies.

## ECSF role mappings

Source: [ENISA European Cybersecurity Skills Framework Role Profiles][ecsf],
published September 2022.

- **Cybersecurity Architect** plans and designs security-by-design solutions
  and cybersecurity controls for infrastructure, systems, software, hardware,
  and services. It applies to the proposed cloud, network, firewall,
  orchestration, and infrastructure-configuration Skills.
- **Cybersecurity Implementer** develops, deploys, operates, configures, and
  maintains cybersecurity solutions. It applies to every proposed Skill and is
  the sole role mapping for the implementation-focused Docker and Linux rows.

The canonical role labels reproduce the published ECSF profile titles.

## NICE cross-references

Source: [NIST NICE Framework Current Versions][nice]. The identifiers below
were checked against NICE Framework Components version **2.2.0**. The
`nice_ids` field can contain competency-area (`NF-COM-*`), knowledge (`K*`), or
skill (`S*`) identifiers, separated by `|`.

| NICE ID | Official statement |
| --- | --- |
| `NF-COM-004` | Cloud Security — capabilities to protect cloud data, applications, and infrastructure from internal and external threats |
| `NF-COM-009` | Operating Systems (OS) Security — capabilities to install, administer, troubleshoot, back up, and recover operating systems |
| `K0744` | Knowledge of operating system (OS) systems and software |
| `K0755` | Knowledge of configuration management (CM) tools and techniques |
| `K0863` | Knowledge of cloud computing principles and practices |
| `K0877` | Knowledge of application firewall principles and practices |
| `K0878` | Knowledge of network firewall principles and practices |
| `K0898` | Knowledge of cloud service models and frameworks |
| `K1014` | Knowledge of network security principles and practices |
| `K1121` | Knowledge of configuration management |
| `K1122` | Knowledge of configuration management principles and practices |
| `K1369` | Knowledge of cloud configuration management policies and procedures |
| `K1373` | Knowledge of containerization principles and practices |
| `S0077` | Skill in securing network communications |
| `S0418` | Skill in applying secure network architectures |
| `S0583` | Skill in implementing established network security practices |
| `S0606` | Skill in manipulating operating system components |
| `S0618` | Skill in configuring network protection components |
| `S0678` | Skill in administering operating systems |
| `S0950` | Skill in reviewing access control lists and firewall rules |

## Certification sources

| Proposed canonical certification | Issuer | Official source |
| --- | --- | --- |
| AWS Certified Security - Specialty | Amazon Web Services | [AWS certification page][aws] |
| CCSP - Certified Cloud Security Professional | ISC2 | [ISC2 certification page][ccsp] |
| Microsoft Certified: Cloud and AI Security Engineer Associate | Microsoft | [Microsoft certification page][sc500] |
| Professional Cloud Security Engineer | Google Cloud | [Google Cloud certification page][google] |

Microsoft retired **Microsoft Certified: Azure Security Engineer Associate**
(`AZ-500`) on 2026-08-31 and replaced it with the SC-500-based Cloud and AI
Security Engineer Associate certification. The retired name and exam code are
not aliases for the replacement because they represent distinct credentials.
See Microsoft's [August 2026 retirement notice][microsoft-retirement].

[ecsf]: https://www.enisa.europa.eu/sites/default/files/publications/European%20Cybersecurity%20Skills%20Framework%20Role%20Profiles.pdf
[nice]: https://www.nist.gov/itl/applied-cybersecurity/nice/nice-framework-resource-center/nice-framework-current-versions
[aws]: https://aws.amazon.com/certification/certified-security-specialty/
[ccsp]: https://www.isc2.org/Certifications/CCSP
[sc500]: https://learn.microsoft.com/en-us/credentials/certifications/cloud-and-ai-security-engineer-associate/
[google]: https://cloud.google.com/learn/certification/cloud-security-engineer/
[microsoft-retirement]: https://learn.microsoft.com/en-us/partner-center/announcements/2026-august

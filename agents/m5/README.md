# M5 data-processing modules

These modules turn extracted source records into ACE Kafka-message-shaped
Python dictionaries. Source extraction is intentionally separate because
CYBERHEAD export layouts and the exact ISC2 report tables are not part of this
repository. Do not treat sample or manually estimated values as source data.

## Modules

- `education.py` normalizes one programme record. Supply `programme_id`,
  `country`, `name`, and `institution`; `ecsf_roles`, `level`, and `url` are
  optional. ECSF role mapping must be done from reviewed source evidence.
- `workforce.py` normalizes one country/year workforce record. Workforce
  figures are optional; missing figures remain absent rather than being
  guessed as zero.
- `platform_analysis.py` formats reviewed platform/feature decisions. The
  caller must provide an explicit boolean decision and evidence notes where
  appropriate; the module does not invent product capabilities.
- `platform_evidence.json` contains a small, cited comparison of facts verified
  from CyberSeek, ENISA and ISC2 first-party pages. It is evidence input, not a
  live feed, and is not published to Cassandra automatically.

Example:

```python
from education import normalize_programme

message = normalize_programme(
    {
        "programme_id": "cyber-programme-1",
        "country": "DE",
        "name": "Cybersecurity MSc",
        "institution": "Example University",
        "ecsf_roles": ["Cybersecurity Implementer"],
        "level": "master",
    }
)
```

Run the local tests from the repository root:

```powershell
python -m unittest discover -s agents\m5 -p test_*.py -v
```

The tests also check that the normalizer output keys stay within the checked-in
JSON Schema contracts.

## Streaming stages

- `streaming/s9_emerging_skills/` implements S9 as a `WindowedStage`. The
  shared stage base validates `skills.demand`, applies event time and the grace
  period, checkpoints the 30-day per-country summaries, and validates
  `agg.emerging_skills` before publishing.
- `streaming/s10_hiring_companies/` implements S10 on the same base. Each daily
  window keeps a mergeable HyperLogLog sketch and a separate missing-company
  count; the rolling result is checkpointed per country.

Run the S9 and S10 algorithm and stage tests from the repository root after
installing `shared/python` as described in `shared/python/README.md`:

```powershell
python -m unittest discover -s streaming\s9_emerging_skills -p "test_*.py" -v
python -m unittest discover -s streaming\s10_hiring_companies -p "test_*.py" -v
```

## Still required for production

The normalizers are not source collectors. Add source-specific CYBERHEAD and
ISC2 readers after confirming their current formats and access conditions.
Map education programmes to ECSF roles only with a reviewed mapping process.
The platform comparison needs citations or other retained evidence in its
notes. The normalizers still need source-specific CYBERHEAD and ISC2 readers,
and their owners must publish messages through the shared `ace.contracts`
package rather than writing directly to Cassandra.

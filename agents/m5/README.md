# M5 data-processing modules

These modules normalize source records and the containerized agents publish
them to Kafka through `ace.contracts`. The agents accept canonical JSON,
JSONL, or CSV inputs. Source-specific extraction is intentionally separate:
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
  from CyberSeek, ENISA and ISC2 first-party pages. The platform agent publishes
  these reviewed rows to `analysis.platforms`; this is evidence input, not a
  live feed.

## Publishing agents

Build each image from the repository root:

```powershell
docker build -f agents/m5/education_agent/Dockerfile -t ace-m5-education .
docker build -f agents/m5/workforce_agent/Dockerfile -t ace-m5-workforce .
docker build -f agents/m5/platform_agent/Dockerfile -t ace-m5-platform .
```

With the local Compose stack running, resolve its network, then attach one-off
agent containers and mount the canonical input read-only:

```powershell
$network = docker inspect ace-kafka --format '{{range $name, $network := .NetworkSettings.Networks}}{{$name}}{{end}}'
docker run --rm --network $network -v "${PWD}\data\m5:/data:ro" ace-m5-education --input /data/programmes.jsonl
docker run --rm --network $network -v "${PWD}\data\m5:/data:ro" ace-m5-workforce --input /data/workforce.csv
docker run --rm --network $network ace-m5-platform
```

Set `KAFKA_BOOTSTRAP_SERVERS` when the broker is not reachable as
`kafka:29092`. CSV `ecsf_roles` cells must contain a JSON array, CSV workforce
numbers must be integers, and CSV platform decisions must be `true` or
`false`. Invalid messages are routed by `ace.contracts` to `errors.invalid`.

## Current scope and placeholders

- **Education:** the agent reads ready-made canonical files. CYBERHEAD
  collection/extraction is not implemented. Input `ecsf_roles` must already be
  reviewed; LLM role mapping is not implemented because `ace.llm` is not
  available in this checkout.
- **Workforce:** the agent reads ready-made canonical files. ISC2 PDF/table
  extraction is not implemented; figures must come from verified report data.
- **Platform analysis:** the agent republishes the hand-curated
  `platform_evidence.json`; it does not run an automated or LLM comparison.
  `analysed_at` records the publication run time, not the date the source fact
  was independently verified.

At row level, normalization failures are sent to `errors.invalid` with the
input row number, and valid rows in the same file are still published. File
parsing failures (for example malformed JSON) stop the run because records
cannot be reliably separated. Kafka broker startup failures report the
configured `KAFKA_BOOTSTRAP_SERVERS` value.

The related-work section and references are in
[M5-related-work.md](../../docs/M5-related-work.md).

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
python -m pip install -e shared/python
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
python -m unittest discover -s streaming\s9_emerging_skills -t streaming -p "test_*.py" -v
python -m unittest discover -s streaming\s10_hiring_companies -t streaming -p "test_*.py" -v
```

## Remaining source-specific work

The publishers deliberately do not guess source layouts or values. Production
collection still needs a CYBERHEAD export adapter and an ISC2 PDF/table
extractor that are verified against the actual source artifacts. Education
role mapping also needs the reviewed mapping workflow and the shared `ace.llm`
interface; this checkout does not contain that module. The platform publisher
uses cited, curated evidence and does not run an LLM or refresh itself.

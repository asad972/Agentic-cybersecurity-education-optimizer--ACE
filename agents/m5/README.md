# M5 data-processing modules

These modules normalize source records and the containerized agents publish
them to Kafka through `ace.contracts`. The agents accept canonical JSON,
JSONL, or CSV inputs. Portal-specific CYBERHEAD extraction and ISC2 report
parsing require verified source layouts; do not treat sample or manually
estimated values as source data.

## Modules

- `education.py` normalizes one programme record. Supply `programme_id`,
  `country`, `name`, and `institution`; `ecsf_roles`, `level`, and `url` are
  optional. Missing `ecsf_roles` are mapped from programme evidence using the
  shared `ace.llm` client.
- `workforce.py` normalizes one country/year workforce record. Workforce
  figures are optional; missing figures remain absent rather than being
  guessed as zero.
- `platform_analysis.py` formats reviewed platform/feature decisions. The
  caller must provide an explicit boolean decision and evidence notes where
  appropriate; the module does not invent product capabilities.
- `platform_evidence.json` contains a small, cited comparison of facts verified
  from CyberSeek, ENISA and ISC2 first-party pages. The platform agent adds an
  LLM summary of each cited row but retains the reviewed support
  decision and source note; this is evidence input, not a live feed.

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
docker run --rm --env-file .env --network $network -e KAFKA_BOOTSTRAP_SERVERS=kafka:29092 -e CASSANDRA_HOSTS=cassandra -v "${PWD}\data\m5:/data:ro" ace-m5-education --input /data/programmes.jsonl
docker run --rm --network $network -v "${PWD}\data\m5:/data:ro" ace-m5-workforce --input /data/workforce.csv
docker run --rm --env-file .env --network $network -e KAFKA_BOOTSTRAP_SERVERS=kafka:29092 -e CASSANDRA_HOSTS=cassandra ace-m5-platform
```

Set `KAFKA_BOOTSTRAP_SERVERS` when the broker is not reachable as
`kafka:29092`. CSV `ecsf_roles` cells must contain a JSON array, CSV workforce
numbers must be integers, and CSV platform decisions must be `true` or
`false`. For local Ollama on the host, set `LLM_LOCAL_URL` in `.env` to
`http://host.docker.internal:11434`. Invalid messages are routed by
`ace.contracts` to `errors.invalid`.

## Current scope and placeholders

- **Education:** the agent reads ready-made CYBERHEAD JSON/JSONL/CSV exports.
  Portal collection/export download is not implemented. If supplied,
  `ecsf_roles` must already be reviewed and use one of the 12 canonical ECSF
  role names. When `ecsf_roles` is absent, the agent maps roles from a
  programme's `description`,
  `overview`, `learning_outcomes`, or `curriculum` via `ace.llm`; it refuses to
  guess from a title alone. The normalizer and `raw.programmes` schema reject
  unknown or duplicate role labels. LLM budget exhaustion routes the affected
  source row to `errors.invalid` rather than publishing an unmapped success.
- **Workforce:** the agent reads ready-made canonical files. ISC2 PDF/table
  extraction is not implemented; figures must come from verified report data.
- **Platform analysis:** the agent republishes the hand-curated
  `platform_evidence.json` and adds a source-grounded LLM summary to each row.
  The model cannot change the reviewed `supported` decision; if the daily LLM
  budget is exceeded, the agent publishes the cited curated row without a
  generated summary. This does not discover new facts or refresh source data.
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
python -m pip install -e "shared/python[llm,dev]"
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

The LLM-based education role mapper and its shared client are implemented.
Remaining data-source work must be based on actual artifacts: implement a
CYBERHEAD portal/export adapter once its export format or API is available, and
an ISC2 PDF/table extractor once the report and country-level table are
identified. The platform publisher uses cited, curated evidence; an automated
comparison should wait for reviewed evidence covering the same features across
each platform rather than infer unsupported capabilities.

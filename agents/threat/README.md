# Threat collector — M4, issue #35

Reads OTX subscribed pulses and AbuseIPDB blacklist observations, resolves IP
countries, and publishes valid `threats.events` records keyed by country. Uses
M2's current `ace.contracts` registry, validation and error routing. It needs
neither PyFlink nor Cassandra. It makes read-only provider requests; it does not
scan IPs, open indicator URLs or submit abuse reports.

## Install and try without keys

Run from the repository root with Python **3.11 or 3.12**, matching the shared
`ace` package. For macOS/Linux:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e shared/python -r agents/threat/requirements-dev.txt
python -m agents.threat --fixture --fixture-hours 1 --output data/threat/events.jsonl
python -m pytest -q -c agents/threat/pytest.ini agents/threat/tests shared/python/tests/test_contracts.py
```

On Windows, create the environment with `py -3.12 -m venv .venv`, then activate
with `.venv\Scripts\Activate.ps1` in PowerShell. The remaining `python` commands
are the same. This agent does not import the stream-stage runtime or require Java.

Fixture mode creates deterministic, explicitly synthetic observations. The
one-hour fixture covers eight countries; the default seven-day fixture covers
October 12–18, 2026, including a deliberate spike and a missing hour for future
S7/S8 tests. Fixture mode needs no API keys and makes no network calls unless
`--publish` is supplied. Generated data and state belong in ignored `data/`.

## Build and run with Docker

The build context is the repository root. The Docker-specific ignore file limits
it to the shared package, schemas and this collector.

```sh
docker build -f agents/threat/Dockerfile -t ace-threat:issue-35 .
docker run --rm ace-threat:issue-35 --help
docker run --rm ace-threat:issue-35 --fixture --fixture-hours 1 --output /state/events.jsonl
```

The last command is a disposable smoke test; its generated file is removed with
the container. Mount a named volume when you want to preserve files and cursors.
The image runs as a non-root user and initializes `/state` with writable ownership.

For live collection, first start the repository's Kafka and topic initialization
services in the checkout where you normally run the root stack:

```sh
docker compose up -d kafka kafka-init
```

Wait for Kafka to become healthy and `kafka-init` to exit with code 0. If an
existing stack was started from another checkout, use that checkout rather than
creating another stack with the same container names. Inspect its network:

```sh
docker inspect ace-kafka --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{end}}'
```

Create a file named `.env` in the repository root, with your own values:

```dotenv
OTX_API_KEY=replace-with-your-key
ABUSEIPDB_API_KEY=replace-with-your-key
```

`.env` is ignored by Git. Never commit it. Replace `YOUR_KAFKA_NETWORK` below with
the network printed above. Both-source mode needs both keys; choose `--source otx`
or `--source abuseipdb` if you have only one. Small budgets are best for a first
account-level test:

```sh
docker run --rm --network YOUR_KAFKA_NETWORK --env-file .env \
  -e KAFKA_BOOTSTRAP_SERVERS=kafka:29092 \
  -v ace-threat-live-state:/state ace-threat:issue-35 \
  --source both --once --hours 24 --max-pages 1 --max-indicators 5 --abuse-limit 5
```

`kafka:29092` is the internal listener in this repository's root Compose stack.
Host Python uses `localhost:9092` instead. For ongoing collection, remove `--once`
and add `--consume-tasks`; polling defaults to **every six hours**. Logs show
observations, skipped rows and whether each bounded batch completed. Credentials,
quota failures and network failures produce a nonzero exit after pending/successful
feed observations are delivered where possible; restart after addressing the error.

Do not publish synthetic fixtures into a real-data topic. Use a disposable broker
for `--fixture --publish`, and keep fixture state separate from live state.

## Configuration and limits

| Setting | Default | Meaning |
| --- | --- | --- |
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | Broker bootstrap address |
| `THREAT_COLLECTOR_STATE` / `--state` | `data/threat/collector.sqlite`; `/state/collector.sqlite` in Docker | Persistent SQLite outbox, cursors and task receipts |
| `--source` | `both` | `otx`, `abuseipdb` or `both` |
| `--hours` | `24` | Observation window, 1–24 hours |
| `--country` | all | ISO country filter, e.g. `HU`, `GB` |
| `--max-pages` | `1` | OTX page budget, 1–10 |
| `--max-indicators` | `100` | OTX indicator budget, 1–1000 |
| `--abuse-limit` | `100` | Blacklist row budget, 1–1000; at most one country lookup per in-window IP |
| `--poll-seconds` | `21600` | Scheduled interval; extra orchestrator tasks also spend provider quota |

HTTP requests use connection/read timeouts and at most four attempts for transient
errors, with a 30-second retry-delay cap. Authentication failures are not retried.
OTX pagination remains on the subscribed-pulses HTTPS endpoint; redirects are not
followed. OTX cursors retain the original time window and page offset for resume.
Changing provider pages and bounded budgets mean coverage is not exhaustive.

The task consumer uses its own `ace-threat-agent` consumer group, validates the
Kafka key against `agent`, and accepts only `refresh_threats` for `threat-agent`.
Other agents' valid tasks are ignored. Malformed tasks go to `errors.invalid`.
Input offsets are committed after processing/delivery, never after a provider or
Kafka failure. Task IDs are remembered durably. A task represents one bounded
attempt, so partial source pages are reported as `batch_complete=False`, not as
full historical coverage. The consumer's maximum poll interval is one hour;
keep provider budgets small enough to finish a batch within that interval.

Cursor updates and outbox inserts are atomic. Kafka acknowledgements precede
marking rows sent. Repeated polls and restarts suppress already-sent IDs; a crash
between Kafka delivery and the SQLite acknowledgement can still replay an ID.
Downstream S7 must deduplicate `event_id` (at-least-once delivery). Use one process
per state file; retained IDs/cursors grow and need a retention policy for long runs.

## What the data means

- OTX: an IP indicator from a subscribed pulse, dated by indicator creation (or
  pulse creation as fallback). Naive provider timestamps are interpreted as UTC.
  Geography comes from the IP indicator's `general` response, not pulse target
  countries. Non-IP indicators and unusable geography are skipped. Tags determine
  broad types; an unclassified indicator uses `indicator`.
- AbuseIPDB: a changed blacklist observation, dated by `lastReportedAt` and labeled
  `abuse_report`. If `countryCode` is absent, the read-only `/check` endpoint provides
  it. Country filtering is applied locally. Provider confidence is not severity.
- Live IDs are deterministic per source observation; live severity is omitted.
  Countries describe indicator locations, not proven victims or attacker identities.
  These are feed observations, not independently confirmed attacks; sources overlap.

Standard AbuseIPDB blacklist responses can omit country. Country lookups consume
additional API quota; choose budgets/intervals to suit your account. The blacklist
is a ranked snapshot, not complete history. OTX requires subscribed pulses, so a
successful request may produce no observations. Unit/integration mocks cannot
verify your account's credentials, subscription, feed coverage or quota.

Provider contracts checked against [AbuseIPDB's documentation](https://docs.abuseipdb.com/)
and the [official OTX SDK](https://github.com/AlienVault-OTX/OTX-Python-SDK/blob/master/OTXv2.py).

## Optional real-Kafka integration test

`tests/kafka_smoke.py` requires `THREAT_SMOKE_KAFKA` pointing at an **empty disposable
broker**. It refuses existing collector topics, creates the three registered
topics, normalizes mocked OTX/AbuseIPDB responses, runs the actual fixture CLI twice,
and consumes/validates all messages. It checks country keys, restart deduplication
and `errors.invalid`, without live provider keys:

```sh
THREAT_SMOKE_KAFKA=localhost:19092 python -m agents.threat.tests.kafka_smoke
```

Do not point this script at the team's normal Kafka broker. Docker execution can
use `--entrypoint python`, `-e THREAT_SMOKE_KAFKA=broker:9092`, and
`-m agents.threat.tests.kafka_smoke` on the disposable broker's network.

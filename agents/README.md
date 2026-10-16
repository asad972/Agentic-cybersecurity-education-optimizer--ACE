# Agents

Job ads, profile, education (CYBERHEAD), workforce (ISC2), threat and platform analysis agents, plus the orchestrator. Each agent publishes JSON to Kafka and never writes to Cassandra. Message formats: [shared/schemas](../shared/schemas/).

The job ads agent replays a job-ads history dataset and fetches live ads from job APIs (Adzuna, Bundesagentur für Arbeit, France Travail); the profile agent replays the Stack Overflow Developer Survey. Both use the record's own date as event time.

| Agent | Owner |
| --- | --- |
| Job ads, profile, orchestrator | M1 |
| Education (CYBERHEAD), workforce (ISC2), platform analysis | M5 |
| Threat | M4 |

Planned layout: one subfolder per agent, each with its own `Dockerfile`.

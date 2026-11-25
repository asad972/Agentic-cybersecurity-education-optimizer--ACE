# Planning Orchestrator (agents/orchestrator)

Evaluates live pipeline coverage across Kafka topics (`agg.demand_daily`, `raw.programmes`, `agg.threats_daily`, `errors.invalid`, `analysis.platforms`) every 6 hours and publishes up to 5 validated collection tasks onto `tasks.collect`. Owner: M1.

## Features

- **Live coverage window:** Counts only messages within the recent lookback window (`--lookback-days` / `ORCHESTRATOR_LOOKBACK_DAYS`, default `7` days back from today UTC). Historical replayed job-ad dates (2015-2026) are intentionally excluded so the orchestrator schedules fresh live API collection rather than treating historical replay as current coverage. Messages with missing or unparseable dates are skipped.
- **Dual LLM + deterministic rule planning:** Uses `ace.llm.LLMClient` (`task="orchestrator_plan"`) when available and falls back automatically to deterministic coverage rules (`decided_by="rules"`) whenever the LLM is unavailable or over budget. Rule tasks request `1` to `2` pages per job-ad fetch (`<= 40` API calls/day across 4 cycles) to stay well within Adzuna's 250 calls/day limit.
- **Strict action allow-list & contract enforcement:** Only allows `fetch_job_ads` (`job-agent`), `refresh_programmes` (`education-agent`), `refresh_threats` (`threat-agent`), and `rerun_platform_analysis` (`platform-agent`), refusing disallowed agents or out-of-range parameters and validating every payload through `ace.contracts.route("tasks.collect", ...)`.
- **Bounded output:** Emits at most 5 tasks per cycle, each keyed by `agent` with a single-sentence reason (`<= 300` chars).

## Running Locally

    $env:PYTHONPATH = "shared/python;agents"
    python -m orchestrator --dry-run

## Running Tests

    $env:PYTHONPATH = "shared/python;agents"
    python -m pytest agents/orchestrator -v

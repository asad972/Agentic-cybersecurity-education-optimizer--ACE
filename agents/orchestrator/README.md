# Planning Orchestrator (agents/orchestrator)

Evaluates pipeline coverage across Kafka topics (agg.demand_daily, raw.programmes, agg.threats_daily, errors.invalid, analysis.platforms) every 6 hours and publishes up to 5 validated collection tasks onto tasks.collect. Owner: M1.

## Features

- Dual LLM + deterministic rule planning: Uses ace.llm.LLMClient (task="orchestrator_plan") when available and falls back automatically to deterministic coverage rules (decided_by="rules") whenever the LLM is unavailable or over budget.
- Strict action allow-list & contract enforcement: Only allows fetch_job_ads (job-agent), refresh_programmes (education-agent), refresh_threats (threat-agent), and rerun_platform_analysis (platform-agent), refusing disallowed agents or out-of-range parameters and validating every payload through ace.contracts.route("tasks.collect", ...).
- Bounded output: Emits at most 5 tasks per cycle, each keyed by agent with a single-sentence reason (<= 300 chars).

## Running Locally

    $env:PYTHONPATH = "shared/python;agents"
    python -m orchestrator --dry-run

## Running Tests

    $env:PYTHONPATH = "shared/python;agents"
    python -m pytest agents/orchestrator -v

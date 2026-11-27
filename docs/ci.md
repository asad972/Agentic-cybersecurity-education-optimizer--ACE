# Automatic checks (CI)

Every pull request and every push to `master` runs the checks in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) on GitHub Actions. The results show at the bottom of the pull request: a green tick when everything passes, a red cross with the failing job when something breaks. Click **Details** next to a job to see its log.

A new push to a pull request cancels the run that is still going for the older commit, so only the latest commit is checked.

## What is checked

| Job | What it does | Runs on |
| --- | --- | --- |
| Shared package and stream stages | Installs `shared/python` with the `llm` extra and every stage's own `requirements.txt`, then runs all tests in `shared/python/tests` and `streaming/` except the Flink ones | Python 3.11 |
| Stream stages on Flink | Same installs plus PyFlink, then runs the tests marked `@pytest.mark.flink`: the same fake records through a real local Flink | Python 3.11, Java 17 |
| Sink | Installs the shared package and `storage/sink`, runs `storage/sink/tests` | Python 3.11 |
| Batch job (forecast), Batch job (gap) | Installs `shared/python` with only the `batch` extra, then `batch/forecast` or `batch/gap`, and runs its tests from the repository root; tests that need a live Cassandra skip themselves | Python 3.11, as in the batch images |
| Monitoring exporter | Installs the shared package and `deploy/monitoring/exporter`, runs its tests | Python 3.11 |
| Prometheus config and rules | `promtool check config` on `prometheus.yml` and the rules it loads, `promtool test rules` on the rule tests | `prom/prometheus` image |
| M5 agents | Installs `shared/python` with the `llm` extra and runs the tests in `agents/m5` | Python 3.11, as in the M5 agent images |
| Threat agent | Installs `shared/python` and `agents/threat/requirements-dev.txt`, runs `agents/threat/tests` | Python 3.12, as in `agents/threat/Dockerfile` |
| Job ads agent | Installs `shared/python` plus `kafka-python`, `cassandra-driver` and `pyarrow` at the versions in `agents/job_ads/Dockerfile`, puts `agents` on the path and runs `agents/job_ads` | Python 3.11, as in `agents/job_ads/Dockerfile` |
| Profiles agent | Installs `shared/python` plus `kafka-python` and `cassandra-driver` at the versions in `agents/profiles/Dockerfile`, puts `agents` on the path and runs `agents/profiles` | Python 3.11, as in `agents/profiles/Dockerfile` |
| Orchestrator | Installs `shared/python` with the `llm` extra plus `kafka-python` at the version in `agents/orchestrator/Dockerfile`, puts `agents` on the path and runs `agents/orchestrator` | Python 3.11, as in `agents/orchestrator/Dockerfile` |
| M3 experiments | Builds `experiments/m3/Dockerfile`, runs the stream experiment tests in its stream environment and the forecast and harness tests in its forecast environment, then `--check experiments/m3/results.json` recomputes every experiment and fails if a result differs | Docker |
| API | Installs `api/` and runs `api/tests` | Python 3.12, as in `api/Dockerfile` |
| Dashboard tests and build | `pnpm install --frozen-lockfile`, `pnpm test` (Vitest unit tests) and `pnpm build` (type check and production build) | Node 22, pnpm from `package.json` |
| Docker images and Compose files | Checks `docker-compose.yml`, `api/compose.yml` and `dashboard/compose.yml`, then builds the API, dashboard, sink, monitoring exporter, three M5 agent, threat agent, job ads agent, profiles agent, orchestrator, forecast, gap, S3, S4, S7 and S8 images (built only, never pushed) | Docker |
| Setup script (bash) | `bash -n` and ShellCheck on `setup.sh` | Ubuntu |
| Setup script (Windows PowerShell 5.1) | Parses `setup.ps1` and checks it is ASCII-only, since PowerShell 5.1 misreads other characters | Windows PowerShell 5.1 |

New tests are picked up without changing the workflow: a stage's tests in `streaming/<stage>/tests/`, new tests in `agents/m5`, `agents/threat/tests`, `agents/job_ads/tests`, `agents/profiles/tests`, `agents/orchestrator/tests` or `batch/*/tests`, new API tests in `api/tests/`, new dashboard pages under `dashboard/src/`. The M3 experiment tests are the exception: each file is named in `ci.yml`, because it has to run in the right one of the image's two environments, so a new file in `experiments/m3/tests/` needs adding there. A stage that needs a package beyond the shared one adds it, pinned exactly, to the `stages` extra in `shared/python/pyproject.toml`, as River is, so the stage images and Flink get it too. Both stage jobs also install any `streaming/<stage>/requirements.txt`. Only a new image, a new agent folder or a new Python package with its own `pyproject.toml` needs a new step in `ci.yml`.

Each stage folder and its `tests/` folder need an empty `__init__.py`, as in `streaming/s4_demand_frequency/`. Without them, two test files with the same name in different folders (for example two `test_stage.py`) stop the whole test run.

All jobs must pass before a pull request is merged. The Flink job is the slowest (a few minutes), but it is the only check that a stage really runs on Flink, the engine we deploy, so a red Flink job blocks the merge like any other.

### What is not checked yet

- The setup scripts come with their own pull request. While `setup.sh` or `setup.ps1` is not on the branch, its job skips its check and still shows a green tick, with a notice such as "setup.sh is not on this branch" in the job's summary. A green tick on those two jobs only means "passed or not there yet". Every other job always runs.
- Only the images that have a Dockerfile and a `docker build` step are built: the API, the dashboard, the sink, the monitoring exporter, the three M5 agents, the threat agent, the job ads agent, the profiles agent, the orchestrator, the forecast and gap jobs, and S3, S4, S7 and S8 in the `docker` job, plus the M3 experiments image in its own job. Apart from the M3 image, which runs its tests and the results check, the images are built but never started, so CI does not check that a container runs. A new Dockerfile (another agent or stage) needs its own `docker build` step.
- No job runs against a real Kafka or Cassandra. Tests that need a live Cassandra (such as `shared/python/tests/test_llm_cassandra.py`) are skipped.

### Making the checks required

The `merging Rule` ruleset on `master` asks for a pull request with one approval, but it does not yet ask for the checks to pass. GitHub only blocks a merge on a red check once the checks are required:

1. Let the workflow run once on a pull request, so GitHub knows the job names.
2. Open **Settings > Rules > Rulesets > merging Rule**.
3. Turn on **Require status checks to pass** and add every job in the table above.
4. Save the ruleset.

## Running the same checks locally

Run these from the repository root in PowerShell. The Python tests need the project virtual environment from [shared/python/README.md](../shared/python/README.md#setup); a plain `py -3.11` has no pytest.

Shared package, stream stages and Flink tests (Java 11 or 17 for the Flink part):

```powershell
C:\venvs\ace\Scripts\Activate.ps1
pip install -e "shared/python[flink,llm,dev]"
Get-ChildItem streaming\*\requirements.txt | ForEach-Object { pip install -r $_.FullName }
python -m pytest shared/python/tests -m "not flink"
python -m pytest streaming -m "not flink"
python -m pytest shared/python/tests -m flink
python -m pytest streaming -m flink
```

Run `shared/python/tests` and `streaming` as separate commands, as CI does. In one command pytest uses `shared/python` as its root, and S7's Flink tests, which start a child pytest by test id, then fail with "file or directory not found".

Sink and monitoring exporter, in the same environment:

```powershell
pip install -e "storage/sink[dev]"
python -m pytest storage/sink/tests
pip install -e "deploy/monitoring/exporter[dev]"
python -m pytest deploy/monitoring/exporter/tests
```

M5 agents, in the same environment:

```powershell
python -m pytest agents/m5
```

Job ads agent, in the same environment (the three packages at the versions in `agents/job_ads/Dockerfile`):

```powershell
pip install kafka-python==2.2.15 cassandra-driver==3.30.1 pyarrow==19.0.1
$env:PYTHONPATH = 'shared/python;agents'
python -m pytest -q agents/job_ads
Remove-Item Env:PYTHONPATH
```

Profiles agent and orchestrator, in the same environment (`kafka-python` and `cassandra-driver` are already installed by the step above):

```powershell
$env:PYTHONPATH = 'shared/python;agents'
python -m pytest -q agents/profiles
python -m pytest -q agents/orchestrator
Remove-Item Env:PYTHONPATH
```

Batch jobs, each in its own Python 3.11 environment (the forecast job pins pandas 3, which River in the shared package's `stages` extra cannot use, so only the `batch` extra is installed):

```powershell
py -3.11 -m venv C:\venvs\ace-forecast
C:\venvs\ace-forecast\Scripts\Activate.ps1
pip install -e "shared/python[batch]" -e "batch/forecast[dev]"
python -m pytest batch/forecast/tests
py -3.11 -m venv C:\venvs\ace-gap
C:\venvs\ace-gap\Scripts\Activate.ps1
pip install -e "shared/python[batch]" -e "batch/gap[dev]"
python -m pytest batch/gap/tests
```

M3 experiments (Docker Desktop running). The image has two separate environments, so each test file runs with the Python it belongs to:

```powershell
docker build -f experiments/m3/Dockerfile -t ace-m3-experiments .
docker run --rm --entrypoint /opt/ace-stream/bin/python ace-m3-experiments -m pytest -q experiments/m3/tests/test_stream_experiments.py
docker run --rm --entrypoint /opt/ace-forecast/bin/python ace-m3-experiments -m pytest -q experiments/m3/tests/test_forecast_experiments.py experiments/m3/tests/test_harness.py
docker run --rm ace-m3-experiments --check experiments/m3/results.json
```

The last command prints `experiment results match` when every recomputed result equals `results.json`.

Prometheus configuration and rules (Docker Desktop running):

```powershell
docker run --rm -v "${PWD}\deploy\monitoring\prometheus:/etc/prometheus:ro" --entrypoint promtool prom/prometheus:v3.15.1 check config /etc/prometheus/prometheus.yml
docker run --rm -v "${PWD}\deploy\monitoring\prometheus:/p:ro" -w /p --entrypoint promtool prom/prometheus:v3.15.1 test rules tests/pipeline-health-test.yml
```

API and threat agent, in their own Python 3.12 environment:

```powershell
py -3.12 -m venv C:\venvs\ace-api
C:\venvs\ace-api\Scripts\Activate.ps1
pip install -e "api[dev]"
python -m pytest -q -c api/pyproject.toml api/tests
pip install -e "shared/python[dev]"
pip install -r agents/threat/requirements-dev.txt
python -m pytest agents/threat/tests
```

Dashboard unit tests and build, without a local Node.js (Docker Desktop running):

```powershell
docker run --rm -v "${PWD}\dashboard:/app" -w /app node:22-alpine sh -c "corepack enable && corepack install && pnpm install --frozen-lockfile --store-dir /tmp/pnpm-store && pnpm test && pnpm build"
```

This leaves `node_modules` in `dashboard/`, which git ignores. Docker images and Compose files:

```powershell
docker compose config -q
docker compose -f api/compose.yml config -q
docker compose -f dashboard/compose.yml config -q
docker build -f api/Dockerfile .
docker build -f dashboard/Dockerfile dashboard
docker build -f storage/sink/Dockerfile .
docker build -f deploy/monitoring/exporter/Dockerfile .
docker build -f agents/m5/education_agent/Dockerfile .
docker build -f agents/m5/workforce_agent/Dockerfile .
docker build -f agents/m5/platform_agent/Dockerfile .
docker build -f agents/threat/Dockerfile .
docker build -f agents/job_ads/Dockerfile .
docker build -f agents/profiles/Dockerfile .
docker build -f agents/orchestrator/Dockerfile .
docker build -f batch/forecast/Dockerfile .
docker build -f batch/gap/Dockerfile .
docker build --target runtime -f streaming/s3_top10/Dockerfile .
docker build --target runtime -f streaming/s4_demand_frequency/Dockerfile .
docker build --target runtime -f streaming/s7_threat_frequency/Dockerfile .
docker build --target runtime -f streaming/s8_threat_bursts/Dockerfile .
```

Setup scripts (the first line runs ShellCheck in Docker; the second prints nothing when `setup.ps1` parses):

```powershell
docker run --rm -v "${PWD}:/mnt:ro" koalaman/shellcheck:v0.9.0 setup.sh
$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile("$PWD\setup.ps1", [ref]$null, [ref]$e); $e
```

Each command prints nothing or a build log and ends without an error when the check passes.

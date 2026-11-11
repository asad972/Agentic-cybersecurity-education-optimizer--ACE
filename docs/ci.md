# Automatic checks (CI)

Every pull request and every push to `master` runs the checks in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) on GitHub Actions. The results show at the bottom of the pull request: a green tick when everything passes, a red cross with the failing job when something breaks. Click **Details** next to a job to see its log.

A new push to a pull request cancels the run that is still going for the older commit, so only the latest commit is checked.

## What is checked

| Job | What it does | Runs on |
| --- | --- | --- |
| Shared package and stream stages | Installs `shared/python` and runs all tests in `shared/python/tests` and `streaming/` except the Flink ones | Python 3.11 |
| Stream stages on Flink | Runs the tests marked `@pytest.mark.flink`: the same fake records through a real local Flink | Python 3.11, Java 17 |
| Sink | Installs the shared package and `storage/sink`, runs `storage/sink/tests` (skipped while the sink is not on the branch) | Python 3.11 |
| Monitoring exporter | Installs the shared package and `deploy/monitoring/exporter`, runs its tests (skipped while monitoring is not on the branch) | Python 3.11 |
| Prometheus config and rules | `promtool check config` on `prometheus.yml` and the rules it loads, `promtool test rules` on the rule tests (skipped while monitoring is not on the branch) | `prom/prometheus` image |
| API | Installs `api/` and runs `api/tests` | Python 3.12, as in `api/Dockerfile` |
| Dashboard build | `pnpm install --frozen-lockfile` and `pnpm build` (type check and production build) | Node 22, pnpm from `package.json` |
| Docker images and Compose files | Checks `docker-compose.yml`, `api/compose.yml` and `dashboard/compose.yml`, then builds the API, dashboard, sink and monitoring exporter images (built only, never pushed) | Docker |
| Setup script (bash) | `bash -n` and ShellCheck on `setup.sh` | Ubuntu |
| Setup script (Windows PowerShell 5.1) | Parses `setup.ps1` and checks it is ASCII-only, since PowerShell 5.1 misreads other characters | Windows PowerShell 5.1 |

New tests are picked up without changing the workflow: a stage's tests in `streaming/<stage>/tests/`, new API tests in `api/tests/`, new dashboard pages under `dashboard/src/`. Only a new image or a new Python package with its own `pyproject.toml` needs a new step in `ci.yml`.

Each stage folder and its `tests/` folder need an empty `__init__.py`, as in `streaming/s4_demand_frequency/`. Without them, two test files with the same name in different folders (for example two `test_stage.py`) stop the whole test run.

All jobs must pass before a pull request is merged. The Flink job is the slowest (a few minutes), but it is the only check that a stage really runs on Flink, the engine we deploy, so a red Flink job blocks the merge like any other.

### What is not checked yet

- Some jobs check parts that arrive with their own pull requests: the sink, the monitoring exporter, the Prometheus rules and the setup scripts. While a part is not on the branch, its steps are skipped and the job still shows a green tick. The job's summary then shows a notice such as "storage/sink is not on this branch". A green tick on those jobs only means "passed or not there yet".
- Only the images that have a Dockerfile are built: the API, the dashboard, and the sink and exporter once they are on the branch. The agents, the batch jobs and the Flink stages have no Dockerfile yet. Each one needs a `docker build` step in the `docker` job when its Dockerfile is added.

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
pip install -e "shared/python[flink,dev]"
python -m pytest shared/python/tests streaming -m "not flink"
python -m pytest shared/python/tests streaming -m flink
```

Sink and monitoring exporter, in the same environment:

```powershell
pip install -e "storage/sink[dev]"
python -m pytest storage/sink/tests
pip install -e "deploy/monitoring/exporter[dev]"
python -m pytest deploy/monitoring/exporter/tests
```

Prometheus configuration and rules (Docker Desktop running):

```powershell
docker run --rm -v "${PWD}\deploy\monitoring\prometheus:/etc/prometheus:ro" --entrypoint promtool prom/prometheus:v3.15.1 check config /etc/prometheus/prometheus.yml
docker run --rm -v "${PWD}\deploy\monitoring\prometheus:/p:ro" -w /p --entrypoint promtool prom/prometheus:v3.15.1 test rules tests/pipeline-health-test.yml
```

API, in its own Python 3.12 environment:

```powershell
py -3.12 -m venv C:\venvs\ace-api
C:\venvs\ace-api\Scripts\Activate.ps1
pip install -e "api[dev]"
python -m pytest -q -c api/pyproject.toml api/tests
```

Dashboard, Docker images and Compose files (Docker Desktop running). Building the dashboard image runs the same `pnpm install --frozen-lockfile` and `pnpm build` as CI, so no local Node.js is needed:

```powershell
docker compose config -q
docker compose -f api/compose.yml config -q
docker compose -f dashboard/compose.yml config -q
docker build -f api/Dockerfile api
docker build -f dashboard/Dockerfile dashboard
docker build -f storage/sink/Dockerfile .
docker build -f deploy/monitoring/exporter/Dockerfile .
```

Setup scripts (the first line runs ShellCheck in Docker; the second prints nothing when `setup.ps1` parses):

```powershell
docker run --rm -v "${PWD}:/mnt:ro" koalaman/shellcheck:v0.9.0 setup.sh
$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile("$PWD\setup.ps1", [ref]$null, [ref]$e); $e
```

Each command prints nothing or a build log and ends without an error when the check passes.

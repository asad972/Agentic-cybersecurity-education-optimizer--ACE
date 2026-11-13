# S4 Demand frequency (Count-Min Sketch)

`skills.demand` → `agg.demand_daily`. For every Country and day: how many job ads asked for each Skill, Certification and ECSF role, plus the number of job ads. Feeds the Demand heat map (Pipeline 2), the Europe map of job ads, S6 drift detection and forecasting. Owner: M2.

| File | What it does |
| --- | --- |
| `stage.py` | The stage on `ace.stage`: one sketch per (Country, day) window, queried for every taxonomy entry when the day closes |
| `count_min.py` | The Count-Min Sketch: add, estimate, merge |
| `taxonomy_keys.py` | The key set: every Skill, Certification and Role in `shared/taxonomy/`; replaced by `ace.taxonomy` once it is merged |
| `__main__.py` | Runs the stage over a file of `skills.demand` messages and writes what it publishes |
| `fixtures/job_ads.jsonl` | Six fake job ads, one of them invalid, that the container runs by default |
| `Dockerfile` | The stage image (`runtime`) and an image that runs S4's tests (`test`) |

## How it counts

Each window keeps a table of 5 rows × 2048 counters. A mention of an item (e.g. `skill:SIEM`) adds 1 to one counter per row, picked by that row's hash of the name. When the day closes, the stage asks for every taxonomy entry: its estimate is the smallest of its 5 counters. Other items that share a counter can only push it up, so **an estimate is never below the true count**. Items with an estimate of 0 are left out. The number of job ads is a plain counter, so it is exact.

**Error bound.** With N the number of mentions in the window, an estimate is at most the true count + εN with probability at least 1 − δ, where ε = e / width and δ = e^−depth (Cormode and Muthukrishnan, 2005).

| | Default | Meaning |
| --- | --- | --- |
| width | 2048 | ε = e / 2048 ≈ 0.0013 |
| depth | 5 | δ = e^−5 ≈ 0.7 % |
| memory | 80 KB per window | 10 240 counters of 8 bytes, whatever the number of job ads |

A busy Country with 1000 job ads a day and about 7 mentions each gives N = 7000, so an estimate is at most 9 too high with 99.3 % probability. In practice it is better: the taxonomy has about 100 entries (200 at most), so in 2048 columns an entry shares a counter with another in all 5 rows only rarely (at most (200/2048)^5 ≈ 0.001 % per entry) and nearly every estimate is exact. `CountMinSketch.from_error(epsilon, delta)` picks width and depth for another target.

**Key set.** A sketch cannot list what it has seen, only answer "how often did X appear?". So the stage asks about every taxonomy entry, as the architecture says. The key set is read once when the stage starts and travels with the stage to Flink's workers, so they do not need the taxonomy files. In a container, `ACE_TAXONOMY_DIR` points to the taxonomy folder.

**Items not in the taxonomy.** S2 only sends taxonomy entries, so an unknown item means S4 started with an older taxonomy than S2. Such items are counted in the sketch like the rest, and their names are kept in a small set per window (at most 200), so they are still published until S4 restarts with the new taxonomy.

**Hashing.** Each row uses BLAKE2b with the row number and a seed as salt. Python's own `hash()` of a string changes in every process, so two Flink workers, or a job restored from a checkpoint, would put the same Skill in different counters. BLAKE2b gives the same counters everywhere, which also makes sketches mergeable: two sketches with the same width, depth and seed add up counter by counter to the sketch of both streams together. S4 does not need the merge today (it publishes each day on its own), but a weekly or EU-wide view could merge daily sketches instead of recounting.

## Tests

```powershell
python -m pytest streaming/s4_demand_frequency -m "not flink"
```

`test_main.py` runs the command-line runner on the fixture file. `test_count_min.py` checks the sketch alone: never under-counts, stays within its bound on a skewed stream, merges, survives pickling (Flink checkpoints), and hashes the same in a new process. `test_s4.py` runs the stage with `run_local`: exact counts on the fake job ads, never under-counting with a deliberately narrow sketch, exact job ad counts, unknown items, and every message checked against `agg.demand_daily`. The `flink` test runs the same job ads on a local Flink.

## Run

On a file of `skills.demand` messages, one JSON message per line, from `streaming/`:

```powershell
python -m s4_demand_frequency --input s4_demand_frequency/fixtures/job_ads.jsonl --output-dir out
```

It writes `demand_daily.jsonl` and `invalid.jsonl` to `out/`. At the end of the file every open day closes, as when a Flink job reaches the end of a bounded input. `--engine flink` runs the same records on a local Flink instead (needs Java). Reading from and publishing to Kafka belongs to the deployment.

In Docker, from the repository root. Name the `runtime` stage: without `--target`, Docker builds the last stage, which is the tests.

```powershell
docker build --target runtime -f streaming/s4_demand_frequency/Dockerfile -t ace-s4 .
docker run --rm ace-s4
```

The image runs the fixture file on Flink and prints `demand_daily.jsonl: 4 rows` and `invalid.jsonl: 1 rows`. To keep the output files, mount a folder on `/output`, e.g. `docker run --rm -v ${PWD}/out:/output ace-s4`. To run S4's tests in the image, including the Flink test:

```powershell
docker build --target test -f streaming/s4_demand_frequency/Dockerfile -t ace-s4-test .
docker run --rm ace-s4-test
```

## References

- Cormode, G. and Muthukrishnan, S. (2005). An improved data stream summary: the count-min sketch and its applications. *Journal of Algorithms*, 55(1), 58–75. https://doi.org/10.1016/j.jalgor.2003.12.001 (the original; older than ten years)
- Cormode, G. and Yi, K. (2020). *Small Summaries for Big Data*. Cambridge University Press. https://doi.org/10.1017/9781108769938 (Count-Min Sketch, error bounds and merging)
- Agarwal, P. K., Cormode, G., Huang, Z., Phillips, J. M., Wei, Z. and Yi, K. (2013). Mergeable summaries. *ACM Transactions on Database Systems*, 38(4), 1–28. https://doi.org/10.1145/2500128 (why per-day sketches can be merged; older than ten years)
- Python Software Foundation. `hashlib`: BLAKE2. https://docs.python.org/3.11/library/hashlib.html#blake2 (salted hashing used for the rows)

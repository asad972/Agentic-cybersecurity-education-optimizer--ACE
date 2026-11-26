# Multilingual S2 extraction evaluation

This benchmark compares the taxonomy baseline with Qwen and Claude using the
merged S2 extraction path. The corpus contains 20 English, 15 German, and 15
French job-ad excerpts written for this experiment. The labels cover required and
optional qualifications, clear local-language equivalents, ECSF profiles, and
unmapped capability phrases. We exclude incidental and negated mentions from
gold. The corpus includes two ads with no canonical Skills per language, and the
runner scores them before S2's Kafka emission filter. The labels were reviewed
against the ads and taxonomy before the model runs. Authored excerpts represent
controlled challenges, and a single labelling pass does not establish
inter-rater agreement or performance on real hiring data.

## Results on November 26, 2026

The taxonomy baseline and Qwen completed all 50 ads at revision `0c4565f`.
Qwen ran on native Ollama 0.40.18 using model digest prefix `359d7dd4bcda`.
The 50 benchmark calls had zero cache hits and zero failures. Both modes found
79 correct canonical Skill labels, added 18 incorrect labels, and missed 46.

| Metric | Taxonomy baseline | Qwen |
| --- | ---: | ---: |
| Skill precision | 81.4% | 81.4% |
| Skill recall | 63.2% | 63.2% |
| Skill F1 | 71.2% | 71.2% |
| Certification F1 | 96.3% | 96.3% |
| ECSF role accuracy | 50.0% | 76.0% |
| Unmapped phrase F1 | 0.0% | 24.4% |

Skill F1 was 88.7% for English, 59.0% for German, and 50.9% for French in both
modes. Qwen improved role classification on cases such as `de-03`, where it
identified the Digital Forensics Investigator and the baseline returned no role.
On `de-01` it found `Alarmpriorisierung`, a gold unmapped phrase, but also
returned German expressions for existing canonical Skills as unmapped. It
found 19 of the 40 gold unmapped phrases with 97 false positives. On `de-13`
both modes picked up Cloud Security from a reference to another team's vacancy.
The results illustrate how S2's union with the taxonomy baseline preserves
existing matches and how exact alias coverage can limit multilingual recall.

Claude has no measured result. The hosted preflight stopped before any call
because no team API key was configured. `results.json` contains the observed
baseline and local metrics and leaves the final recommendation unset.

## Run

Use Python 3.11 from the repository root. Each mode writes its own resumable
checkpoint. Keep checkpoints outside the checkout.

```bash
python -m pip install -e 'shared/python[llm,dev]'
export PYTHONPATH="$PWD/streaming"
ACE_QUALITY_DIR="$(mktemp -d)"
python -m experiments.m3.llm_quality run --mode baseline --output "$ACE_QUALITY_DIR/baseline.json"
```

Use a dedicated Cassandra instance for this evaluation with
`storage/cql/schema.cql` applied. Start Ollama and pull `qwen3:4b`. For
example, Docker can run both services on loopback:

```bash
docker run -d --name ace-issue30-cassandra -p 127.0.0.1:19042:9042 \
  -e MAX_HEAP_SIZE=1G -e HEAP_NEWSIZE=256M \
  -v ace-issue30-cassandra:/var/lib/cassandra \
  -v "$PWD/storage/cql/schema.cql:/schema.cql:ro" cassandra:4.1
# When cqlsh can connect:
docker exec ace-issue30-cassandra cqlsh -f /schema.cql
docker run -d --name ace-issue30-ollama -p 127.0.0.1:11434:11434 \
  -v ace-issue30-ollama:/root/.ollama ollama/ollama
docker exec ace-issue30-ollama ollama pull qwen3:4b
```

On Apple Silicon, native Ollama can share the host's GPU and memory. If the
Docker VM cannot load the model alongside Cassandra, run Ollama natively and
point `LLM_LOCAL_URL` to that instance. [Ollama macOS documentation](https://docs.ollama.com/macos)

```bash
export CASSANDRA_HOSTS=127.0.0.1 CASSANDRA_PORT=19042 CASSANDRA_KEYSPACE=ace
export LLM_LOCAL_URL=http://127.0.0.1:11434 LLM_DAILY_BUDGET_EUR=2
python -m experiments.m3.llm_quality run --mode local --smoke --output "$ACE_QUALITY_DIR/local-smoke.json"
python -m experiments.m3.llm_quality run --mode local --output "$ACE_QUALITY_DIR/local.json"
```

For Claude, set `LLM_API_KEY`, `LLM_HOSTED_INPUT_EUR_PER_MTOK`, and
`LLM_HOSTED_OUTPUT_EUR_PER_MTOK` in the shell using the approved team credential
and current rates in EUR. The runner pins `claude-haiku-5-5` and rejects a daily
cap above EUR 2. Only one evaluation process should use this Cassandra
instance. A hosted budget error stops the run. Resume with the same command,
checkpoint, Cassandra data, prices, and Git revision. [Anthropic model catalog](https://platform.claude.com/docs/en/models/overview)

```bash
python -m experiments.m3.llm_quality run --mode hosted --smoke --output "$ACE_QUALITY_DIR/hosted-smoke.json"
python -m experiments.m3.llm_quality run --mode hosted --output "$ACE_QUALITY_DIR/hosted.json"
python -m experiments.m3.llm_quality score \
  --baseline "$ACE_QUALITY_DIR/baseline.json" --local "$ACE_QUALITY_DIR/local.json" \
  --hosted "$ACE_QUALITY_DIR/hosted.json" --output "$ACE_QUALITY_DIR/results.json"
```

Omit either provider argument when its checkpoint is unavailable. Metrics use
the intersection of successfully completed ads. Coverage and failure counts
are shown separately for each mode. Cache hits retain the recorded token
counts. The hosted spend uses the evaluator's delta in Cassandra's settled
budget, which can include pessimistic charges for failed calls. Cache hits
have no new charge. The results record the evaluated revision. Use that Git
revision when resuming its checkpoints.

## Decision rule

Canonical Skill micro-F1 is primary. The runner also scores Certifications,
exact ECSF roles, and exact normalized unmapped phrases. Claude is recommended
only when it improves Skill micro-F1 by at least 0.05, the lower bound of the
95% language-stratified paired bootstrap interval exceeds zero, and neither
German nor French F1 drops by more than 0.05. The bootstrap uses 10,000 draws
with seed 30. The recommendation remains empty until all three modes cover
all 50 ads.

```bash
python -m pytest -q -m 'not flink' experiments/m3/llm_quality \
  streaming/s2_skill_extraction/tests shared/python/tests/test_llm.py \
  shared/python/tests/test_taxonomy.py
```

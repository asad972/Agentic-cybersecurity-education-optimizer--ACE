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

All three modes completed all 50 ads at scoring revision `719b54b`. Qwen's
original live run used Ollama 0.40.18 and model digest prefix `359d7dd4bcda` at
`0c4565f`. We replayed all 50 answers from the Cassandra cache at the scoring
revision and checked that every extracted label matched the original run.
Claude Haiku 5.5 made 50 successful live calls. ACE's budget ledger settled
€0.007179 for the benchmark and €0.000096 for the separate smoke test.

| Metric | Taxonomy baseline | Qwen | Claude |
| --- | ---: | ---: | ---: |
| Skill precision | 81.4% | 81.4% | 81.4% |
| Skill recall | 63.2% | 63.2% | 63.2% |
| Skill F1 | 71.2% | 71.2% | 71.2% |
| Certification F1 | 96.3% | 96.3% | 96.3% |
| ECSF role accuracy | 50.0% | 76.0% | 82.0% |
| Unmapped phrase F1 | 0.0% | 24.4% | 52.5% |

Canonical Skill predictions matched on every ad, with 79 true positives,
18 false positives and 46 false negatives. Skill F1 was 88.7% for English,
59.0% for German, and 50.9% for French in all three modes. On `de-13`, all
three picked up Cloud Security from a reference to another team's vacancy.
These results illustrate how S2's union with the taxonomy baseline preserves
existing matches and how exact alias coverage can limit multilingual recall.

Claude correctly classified the privacy adviser in `en-13` as a Cyber Legal,
Policy and Compliance Officer. Both models found the gold unmapped phrase
`Alarmpriorisierung` for `de-01`; Claude still returned `Sigma-Regeln` as
unmapped, even though it describes a canonical Skill. Claude recovered
32 of 40 gold unmapped phrases with 50 false positives. Qwen found 19 of 40
with 97 false positives.

**Recommendation: keep Qwen as the S2 default.** Claude's canonical Skill F1
gain was zero, with a 95% paired interval of `[0, 0]`. The measured gains for
ECSF roles and unmapped phrases make Claude worth considering if either output
becomes the primary target.

The first hosted smoke exposed a wire-schema requirement. The Anthropic
adapter now applies the SDK's `transform_schema()` before token counting and
generation. ACE continues to validate returned data against the original
Pydantic model. [Anthropic structured-output requirements](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)

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

# S1 Near-Duplicate Job Filter (`s1_dedup`)

**Owner:** M1  
**Input topic:** `raw.jobs`  
**Output topic:** `jobs.clean`  
**Metric:** `duplicates_removed_total`

## Overview
`S1DedupStage` subclasses `ace.stage.RecordStage` and partitions state per `country` (the Kafka key of `raw.jobs`). For each incoming job advertisement:

1. **Sliding window eviction (`window_days=30`):** Tracks `latest_date` per country (ignoring dates more than 1 day ahead of the newest date seen so far so a wrong future `posted_date` cannot empty the window) and evicts expired entries simultaneously from `seen_ids`, `history`, `signatures`, and `MinHashLSH`.
2. **Exact ID deduplication:** Drops repeated `event_id` records immediately and increments `duplicates_removed_total`.
3. **MinHash + LSH similarity check:** Computes deterministic 128-permutation MinHash signatures (`seed=42`) over normalized unigrams and bigrams (`job_title`, `company`, `description`), queries `datasketch.MinHashLSH` (`lsh_threshold=0.6`) for candidate IDs, looks up candidate signatures in O(1) via `memory["signatures"]`, and verifies exact Jaccard similarity (`>= 0.75`).

## Running Tests
```powershell
python -m pytest streaming/s1_dedup -m "not flink"
```
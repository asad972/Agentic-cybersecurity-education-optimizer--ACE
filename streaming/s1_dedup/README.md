# S1 Near-Duplicate Job Filter (`s1_dedup`)

**Owner:** M1  
**Input topic:** `raw.jobs`  
**Output topic:** `jobs.clean`  
**Metric:** `duplicates_removed_total`

## Overview
`S1DedupStage` subclasses `ace.stage.RecordStage` and partitions state per `country` (the Kafka key of `raw.jobs`). For each incoming job advertisement:

1. **Sliding window eviction (`window_days=30`):** Tracks `latest_date` per country and evicts expired entries simultaneously from `seen_ids`, `history`, and the `MinHashLSH` index so memory stays bounded and late out-of-order ads never move the cutoff window backwards.
2. **Exact ID deduplication:** Drops repeated `event_id` records and increments `duplicates_removed_total`.
3. **MinHash + LSH similarity check:** Computes deterministic 128-permutation MinHash signatures (`seed=42`) over normalized unigrams and bigrams (`job_title`, `company`, `description`), queries `datasketch.MinHashLSH` for candidate matches, verifies exact Jaccard similarity (`>= 0.75`), and emits unique postings to `jobs.clean`.

## Running Tests
```bash
pytest streaming/s1_dedup -v
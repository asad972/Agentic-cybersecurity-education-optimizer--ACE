# S1 Near-Duplicate Job Filter (`s1_dedup`)

**Owner:** M1  
**Input topic:** `raw.jobs`  
**Output topic:** `jobs.clean`

## Overview
`S1DedupStage` subclasses `ace.stage.RecordStage` and partitions state per `country` (the Kafka key of `raw.jobs`). For each incoming job advertisement:

1. **Exact ID deduplication:** Drops postings whose `event_id` has already been seen in that country.
2. **Sliding window eviction:** Evicts signatures older than `window_days` (default `30` days) relative to the latest event date seen for that country.
3. **MinHash + LSH similarity check:** Computes deterministic 128-permutation MinHash signatures over normalized unigrams and bigrams (`job_title`, `company`, `description`), queries a `MinHashLSH` index (`threshold=0.75`), and verifies candidate Jaccard similarity before emitting unique postings to `jobs.clean`.

## Running Tests
```bash
pytest streaming/s1_dedup -v
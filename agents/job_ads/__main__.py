"""CLI entrypoint for the ACE Job Ads replay agent."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from ace.replay import InMemoryPositionStore
from job_ads.agent import (
    RAW_JOBS_TOPIC,
    DryRunProducer,
    JobAdsAgent,
    KafkaJsonProducer,
    open_cassandra_store,
)


def _default_dataset_path() -> Path:
    env_input = os.environ.get("JOB_ADS_INPUT", "").strip()
    if env_input:
        return Path(env_input)
    repo_sample = Path(__file__).resolve().parents[2] / "shared" / "testdata" / "jobs_sample.jsonl"
    if repo_sample.is_file():
        return repo_sample
    return Path("/opt/ace/testdata/jobs_sample.jsonl")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="job_ads",
        description="Replay job advertisement datasets to Kafka (raw.jobs) in event-time order.",
    )
    parser.add_argument(
        "--input",
        nargs="+",
        default=None,
        help="Path(s) to .jsonl, .csv, or .parquet job advertisement dataset files.",
    )
    parser.add_argument(
        "--dataset",
        default=os.environ.get("JOB_ADS_DATASET", "job_ads"),
        help="Dataset identifier stored in Cassandra replay_position (default: job_ads).",
    )
    parser.add_argument(
        "--source",
        default=os.environ.get("JOB_ADS_SOURCE", "job_ads_dataset"),
        help="Fallback source name when rows omit a source column.",
    )
    parser.add_argument(
        "--default-country",
        default=os.environ.get("JOB_ADS_DEFAULT_COUNTRY") or None,
        help="Fallback ISO alpha-2 country code when rows omit country and nuts_code (e.g. DE).",
    )
    parser.add_argument(
        "--seconds-per-day",
        type=float,
        default=None,
        help="Simulated seconds per event-time day (overrides REPLAY_SECONDS_PER_DAY).",
    )
    parser.add_argument(
        "--checkpoint-interval",
        type=int,
        default=int(os.environ.get("REPLAY_CHECKPOINT_INTERVAL", "25")),
        help="Save replay checkpoint every N published records (default: 25).",
    )
    parser.add_argument(
        "--bootstrap-servers",
        default=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
        help="Kafka bootstrap servers (default: KAFKA_BOOTSTRAP_SERVERS or localhost:9092).",
    )
    parser.add_argument(
        "--cassandra-hosts",
        default=os.environ.get("CASSANDRA_HOSTS", "localhost"),
        help="Cassandra contact points (default: CASSANDRA_HOSTS or localhost).",
    )
    parser.add_argument(
        "--cassandra-keyspace",
        default=os.environ.get("CASSANDRA_KEYSPACE", "ace"),
        help="Cassandra keyspace containing replay_position (default: ace).",
    )
    parser.add_argument(
        "--no-cassandra",
        action="store_true",
        help="Use in-memory checkpoint store instead of connecting to Cassandra.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and replay into an in-memory producer without Kafka or Cassandra.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = build_parser().parse_args(argv)
    input_paths = [Path(p) for p in args.input] if args.input else [_default_dataset_path()]

    cluster = None
    kafka_producer = None
    try:
        if args.dry_run:
            store = InMemoryPositionStore()
            producer = DryRunProducer()
        else:
            if args.no_cassandra:
                store = InMemoryPositionStore()
            else:
                cluster, store = open_cassandra_store(
                    args.cassandra_hosts,
                    keyspace=args.cassandra_keyspace,
                )
            kafka_producer = KafkaJsonProducer(args.bootstrap_servers)
            producer = kafka_producer

        agent = JobAdsAgent(
            default_source=args.source,
            default_country=args.default_country,
            seconds_per_day=args.seconds_per_day,
            checkpoint_interval=args.checkpoint_interval,
            position_store=store,
        )
        total = agent.replay_paths(
            input_paths,
            producer,
            dataset=args.dataset,
        )
        logging.info(
            "Job ads replay finished: dataset=%s, last_position=%d, skipped_rows=%d",
            args.dataset,
            total,
            agent.last_skipped_count,
        )
        if isinstance(producer, DryRunProducer):
            valid_count = sum(1 for topic, _, _ in producer.sent if topic == RAW_JOBS_TOPIC)
            invalid_count = len(producer.sent) - valid_count
            print(
                f"Dry-run complete: {valid_count} valid raw.jobs, "
                f"{invalid_count} invalid, {agent.last_skipped_count} skipped"
            )
        return 0
    finally:
        if kafka_producer is not None:
            kafka_producer.close()
        if cluster is not None:
            cluster.shutdown()


if __name__ == "__main__":
    sys.exit(main())
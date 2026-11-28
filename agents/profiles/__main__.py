"""CLI entrypoint for the ACE Profile replay agent."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from ace.replay import InMemoryPositionStore
from profiles.agent import (
    RAW_PROFILES_TOPIC,
    DryRunProducer,
    KafkaJsonProducer,
    ProfileAgent,
    open_cassandra_store,
)


def _default_dataset_path() -> Path:
    env_input = os.environ.get("PROFILES_INPUT", "").strip()
    if env_input:
        return Path(env_input)
    repo_sample = Path(__file__).resolve().parents[2] / "shared" / "testdata" / "profiles_sample.jsonl"
    if repo_sample.is_file():
        return repo_sample
    return Path("/opt/ace/testdata/profiles_sample.jsonl")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="profiles",
        description="Replay anonymised profile datasets to Kafka (raw.profiles) in event-time order.",
    )
    parser.add_argument(
        "--input",
        nargs="+",
        default=None,
        help="Path(s) to .jsonl or .csv profile / Stack Overflow Developer Survey files.",
    )
    parser.add_argument(
        "--dataset",
        default=os.environ.get("PROFILES_DATASET", "profiles"),
        help="Dataset identifier stored in Cassandra replay_position (default: profiles).",
    )
    parser.add_argument(
        "--source",
        default=os.environ.get("PROFILES_SOURCE", "stackoverflow"),
        help="Fallback source name when rows omit a source column (default: stackoverflow).",
    )
    parser.add_argument(
        "--default-country",
        default=os.environ.get("PROFILES_DEFAULT_COUNTRY") or None,
        help="Fallback ISO alpha-2 country code when rows omit country (e.g. DE).",
    )
    parser.add_argument(
        "--default-profile-date",
        default=os.environ.get("PROFILES_DEFAULT_DATE") or None,
        help="Fallback YYYY-MM-DD survey date when CSV rows omit profile_date.",
    )
    parser.add_argument(
        "--include-all-roles",
        action="store_true",
        help="Include all European survey respondents instead of filtering to security roles/skills.",
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

        agent = ProfileAgent(
            default_source=args.source,
            default_country=args.default_country,
            default_profile_date=args.default_profile_date,
            security_only=not args.include_all_roles,
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
            "Profiles replay finished: dataset=%s, last_position=%d, skipped_rows=%d",
            args.dataset,
            total,
            agent.last_skipped_count,
        )
        if isinstance(producer, DryRunProducer):
            valid_count = sum(1 for topic, _, _ in producer.sent if topic == RAW_PROFILES_TOPIC)
            invalid_count = len(producer.sent) - valid_count
            print(
                f"Dry-run complete: {valid_count} valid raw.profiles, "
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

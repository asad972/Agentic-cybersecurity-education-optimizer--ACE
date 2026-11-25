"""CLI entrypoint for the ACE planning orchestrator."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from ace.llm import LLMClient, LLMError
from orchestrator.agent import DryRunProducer, KafkaJsonProducer, OrchestratorAgent
from orchestrator.models import MAX_TASKS_PER_RUN, CoverageSnapshot
from orchestrator.planner import OrchestratorPlanner

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orchestrator",
        description="Evaluate ACE pipeline coverage and publish collection tasks to tasks.collect.",
    )
    parser.add_argument(
        "--bootstrap-servers",
        default=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
        help="Kafka bootstrap servers (default: KAFKA_BOOTSTRAP_SERVERS or localhost:9092).",
    )
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=float(os.environ.get("ORCHESTRATOR_INTERVAL_SECONDS", "21600")),
        help="Interval between planning cycles in seconds (default: 21600 = 6 hours).",
    )
    parser.add_argument(
        "--max-tasks",
        type=int,
        default=int(os.environ.get("ORCHESTRATOR_MAX_TASKS", str(MAX_TASKS_PER_RUN))),
        help="Maximum collection tasks emitted per run (1..5, default: 5).",
    )
    parser.add_argument(
        "--rules-only",
        action="store_true",
        help="Use deterministic rules directly without initializing LLMClient.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single planning cycle and exit instead of looping every 6 hours.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run one planning cycle against an empty/local snapshot without Kafka or Cassandra.",
    )
    return parser


def _build_llm_client(rules_only: bool) -> LLMClient | None:
    if rules_only or os.environ.get("ORCHESTRATOR_MODE", "").strip().lower() == "rules":
        return None
    try:
        return LLMClient.from_env()
    except LLMError as exc:
        logger.warning("LLMClient unavailable (%s); orchestrator will use fixed rules.", exc)
        return None


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = build_parser().parse_args(argv)
    llm_client = _build_llm_client(args.rules_only)

    planner = OrchestratorPlanner(
        llm_client=llm_client,
        use_llm=llm_client is not None,
        max_tasks=args.max_tasks,
    )
    agent = OrchestratorAgent(planner=planner)

    kafka_producer = None
    try:
        if args.dry_run:
            producer = DryRunProducer()
            tasks = agent.run_once(producer, snapshot=CoverageSnapshot())
            print(f"Dry-run complete: emitted {len(tasks)} collect_task message(s)")
            for task in tasks:
                print(json.dumps(task, ensure_ascii=False))
            return 0

        kafka_producer = KafkaJsonProducer(args.bootstrap_servers)
        if args.once:
            agent.run_once(kafka_producer, bootstrap_servers=args.bootstrap_servers)
        else:
            agent.run_loop(
                kafka_producer,
                bootstrap_servers=args.bootstrap_servers,
                interval_seconds=args.interval_seconds,
            )
        return 0
    finally:
        if kafka_producer is not None:
            kafka_producer.close()
        if llm_client is not None:
            llm_client.close()


if __name__ == "__main__":
    sys.exit(main())

"""Command line entry point for ``python -m ace_gap``."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import logging
import os

from .config import GapConfig
from .month import TargetMonth, previous_complete_month
from .repository import CassandraGapRepository
from .runner import run_gap_job
from .taxonomy import load_taxonomy


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Compute monthly ACE Skills Gap and ECSF role relations")
    result.add_argument("--month", type=_month, default=None, help="completed month in YYYY-MM format")
    return result


def _month(value: str) -> TargetMonth:
    try:
        return TargetMonth.parse(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(levelname)s %(message)s")
    config = GapConfig.from_env()
    taxonomy = load_taxonomy(config.taxonomy_dir)
    target = arguments.month or previous_complete_month(datetime.now(timezone.utc).date())
    with CassandraGapRepository.connect(config) as repository:
        run_gap_job(repository, taxonomy, target, config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

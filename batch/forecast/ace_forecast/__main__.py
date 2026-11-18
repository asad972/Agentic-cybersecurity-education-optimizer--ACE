"""Command line entry point for ``python -m ace_forecast``."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import logging
import os
from pathlib import Path

from .config import ForecastConfig
from .repository import CassandraForecastRepository
from .runner import run_forecasts
from .taxonomy import load_skills


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Forecast monthly ACE Skill Demand")
    result.add_argument("--as-of", type=_date, default=None, help="UTC cutoff date (YYYY-MM-DD)")
    return result


def _date(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from exc


def default_taxonomy_dir() -> Path:
    configured = os.getenv("ACE_TAXONOMY_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[3] / "shared" / "taxonomy"


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(levelname)s %(message)s")
    config = ForecastConfig.from_env()
    skills = load_skills(default_taxonomy_dir())
    as_of = arguments.as_of or datetime.now(timezone.utc).date()
    with CassandraForecastRepository.connect(config) as repository:
        run_forecasts(repository, skills, as_of, config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

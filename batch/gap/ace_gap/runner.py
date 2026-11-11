"""Orchestrate monthly reads, gap calculations, relations, and writes."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
import logging

from .calculation import calculate_gap, calculate_role_relations
from .config import GapConfig, SKILLS_SCOPE
from .month import TargetMonth
from .repository import GapRepository, StoredCount
from .taxonomy import Taxonomy
from .types import GapRow


LOGGER = logging.getLogger("ace_gap")


@dataclass(frozen=True, slots=True)
class RunSummary:
    month: str
    countries_with_input: int
    demand_partitions: int
    supply_snapshots: int
    gap_rows: int
    role_rows: int


def run_gap_job(
    repository: GapRepository,
    taxonomy: Taxonomy,
    target: TargetMonth,
    config: GapConfig,
) -> RunSummary:
    aggregate_demand: Counter[str] = Counter()
    gap_rows: list[GapRow] = []
    countries = 0
    demand_partitions = 0
    supply_snapshots = 0
    demand_input = False

    for country in SKILLS_SCOPE:
        demand: Counter[str] = Counter()
        country_demand_partitions = 0
        for day in target.days():
            skill_rows = _skill_counts(repository.read_demand_day(country, day), taxonomy)
            if skill_rows:
                country_demand_partitions += 1
                demand_input = True
                demand.update(skill_rows)
        demand_partitions += country_demand_partitions

        snapshot = repository.latest_supply(country, target.last_day, config.supply_lookback_days)
        supply = Counter()
        if snapshot is not None:
            supply.update(_skill_counts(snapshot.counts, taxonomy))
            supply_snapshots += 1

        if not demand and snapshot is None:
            continue
        countries += 1
        aggregate_demand.update(demand)
        country_rows = calculate_gap(
            country,
            target.key,
            taxonomy,
            demand,
            supply,
            config.min_supply,
        )
        gap_rows.extend(country_rows)
        _metric(
            "gap_country",
            country=country,
            month=target.key,
            demand_partitions=country_demand_partitions,
            supply_day=snapshot.day.isoformat() if snapshot else None,
            supply_missing=snapshot is None,
            rows=len(country_rows),
        )

    role_rows = calculate_role_relations(taxonomy, aggregate_demand) if demand_input else ()
    if gap_rows:
        repository.write_gaps(gap_rows)
    if role_rows:
        repository.write_relations(role_rows)
    elif not demand_input:
        LOGGER.warning(json.dumps({"event": "role_relations_skipped", "month": target.key}, sort_keys=True))
    if not gap_rows:
        LOGGER.warning(json.dumps({"event": "gap_no_input", "month": target.key}, sort_keys=True))

    summary = RunSummary(
        target.key,
        countries,
        demand_partitions,
        supply_snapshots,
        len(gap_rows),
        len(role_rows),
    )
    _metric(
        "gap_summary",
        month=summary.month,
        countries_with_input=summary.countries_with_input,
        demand_partitions=summary.demand_partitions,
        supply_snapshots=summary.supply_snapshots,
        gap_rows=summary.gap_rows,
        role_rows=summary.role_rows,
    )
    return summary


def _skill_counts(rows: tuple[StoredCount, ...], taxonomy: Taxonomy) -> Counter[str]:
    canonical = taxonomy.skill_lookup
    result: Counter[str] = Counter()
    for row in rows:
        if row.kind != "skill":
            continue
        skill = canonical.get(row.item.casefold())
        if skill is None:
            raise ValueError(f"Input contains unknown canonical Skill: {row.item!r}")
        if isinstance(row.count, bool) or not isinstance(row.count, int) or row.count < 0:
            raise ValueError(f"Input count must be a nonnegative integer for {row.item!r}")
        result[skill] += row.count
    return result


def _metric(event: str, **values) -> None:
    LOGGER.info(json.dumps({"event": event, **values}, sort_keys=True, separators=(",", ":")))

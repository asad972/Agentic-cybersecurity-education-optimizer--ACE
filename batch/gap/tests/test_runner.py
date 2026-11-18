from datetime import date

import pytest

import ace_gap.runner as runner_module
from ace_gap.config import GapConfig
from ace_gap.month import TargetMonth
from ace_gap.repository import StoredCount, SupplySnapshot
from ace_gap.runner import run_gap_job
from ace_gap.taxonomy import Taxonomy


TAXONOMY = Taxonomy(
    skills=("Cloud Security", "Linux"),
    roles=("Architect", "Implementer"),
    skill_roles=(
        ("Cloud Security", ("Architect", "Implementer")),
        ("Linux", ("Implementer",)),
    ),
)


class MemoryRepository:
    def __init__(self, demand=None, supply=None, fail_write=False):
        self.demand = demand or {}
        self.supply = supply or {}
        self.fail_write = fail_write
        self.demand_reads = []
        self.supply_reads = []
        self.gaps = []
        self.relations = []

    def read_demand_day(self, country, day):
        self.demand_reads.append((country, day))
        return tuple(self.demand.get((country, day), ()))

    def latest_supply(self, country, on_or_before, lookback_days):
        self.supply_reads.append((country, on_or_before, lookback_days))
        return self.supply.get(country)

    def write_gaps(self, rows):
        if self.fail_write:
            raise RuntimeError("write failed")
        self.gaps.extend(rows)

    def write_relations(self, rows):
        if self.fail_write:
            raise RuntimeError("write failed")
        self.relations.extend(rows)

    def close(self):
        pass


@pytest.fixture(autouse=True)
def small_scope(monkeypatch):
    monkeypatch.setattr(runner_module, "SKILLS_SCOPE", ("DE", "FR"))


def test_country_rows_monthly_aggregation_supply_and_role_relations():
    target = TargetMonth(2026, 10)
    repository = MemoryRepository(
        demand={
            ("DE", date(2026, 10, 1)): (
                StoredCount("skill", "cloud security", 6),
                StoredCount("certification", "CISSP", 9),
            ),
            ("DE", date(2026, 10, 2)): (StoredCount("skill", "Cloud Security", 4),),
            ("FR", date(2026, 10, 1)): (StoredCount("skill", "Linux", 3),),
        },
        supply={
            "DE": SupplySnapshot(date(2026, 10, 30), (StoredCount("skill", "Cloud Security", 20),)),
        },
    )
    summary = run_gap_job(repository, TAXONOMY, target, GapConfig())
    assert summary.countries_with_input == 2
    assert summary.demand_partitions == 3
    assert summary.supply_snapshots == 1
    assert summary.gap_rows == 4
    assert summary.role_rows == 2
    assert summary.unknown_skill_rows == 0
    rows = {(row.country, row.skill): row for row in repository.gaps}
    assert rows[("DE", "Cloud Security")].demand == 10
    assert rows[("DE", "Cloud Security")].gap_ratio == 0.5
    assert rows[("DE", "Linux")].supply == 0
    assert rows[("FR", "Linux")].demand == 3
    assert all(row.overlap == pytest.approx(0.5) for row in repository.relations)
    assert repository.supply_reads == [
        ("DE", date(2026, 10, 31), 365),
        ("FR", date(2026, 10, 31), 365),
    ]


def test_country_without_demand_or_supply_is_skipped():
    repository = MemoryRepository(
        supply={"DE": SupplySnapshot(date(2026, 10, 31), (StoredCount("skill", "Linux", 25),))}
    )
    summary = run_gap_job(repository, TAXONOMY, TargetMonth(2026, 10), GapConfig())
    assert summary.countries_with_input == 1
    assert len(repository.gaps) == 2
    assert repository.relations == []


def test_empty_input_is_noop_and_logs_warnings(caplog):
    repository = MemoryRepository()
    summary = run_gap_job(repository, TAXONOMY, TargetMonth(2026, 10), GapConfig())
    assert summary.gap_rows == summary.role_rows == 0
    assert "gap_no_input" in caplog.text
    assert "role_relations_skipped" in caplog.text


def test_unknown_skills_are_skipped_counted_and_warned_once(caplog):
    caplog.set_level("WARNING", logger="ace_gap")
    repository = MemoryRepository(
        demand={
            ("DE", date(2026, 10, 1)): (
                StoredCount("skill", "Unknown Demand", 99),
                StoredCount("skill", "Linux", 3),
            )
        },
        supply={
            "DE": SupplySnapshot(
                date(2026, 10, 31),
                (
                    StoredCount("skill", "Unknown Supply", 40),
                    StoredCount("skill", "Linux", 20),
                ),
            )
        },
    )

    summary = run_gap_job(repository, TAXONOMY, TargetMonth(2026, 10), GapConfig())

    assert summary.unknown_skill_rows == 2
    linux = next(row for row in repository.gaps if row.country == "DE" and row.skill == "Linux")
    assert (linux.demand, linux.supply, linux.gap_ratio) == (3, 20, 0.15)
    warnings = [record.message for record in caplog.records if "gap_unknown_skills_skipped" in record.message]
    assert len(warnings) == 1
    assert '"demand_rows": 1' in warnings[0]
    assert '"supply_rows": 1' in warnings[0]
    assert '"total_rows": 2' in warnings[0]
    assert "Unknown Demand" not in caplog.text
    assert "Unknown Supply" not in caplog.text


def test_unknown_only_demand_does_not_create_country_output(caplog):
    caplog.set_level("WARNING", logger="ace_gap")
    repository = MemoryRepository(
        {("DE", date(2026, 10, 1)): (StoredCount("skill", "Unknown", 1),)}
    )
    summary = run_gap_job(repository, TAXONOMY, TargetMonth(2026, 10), GapConfig())
    assert summary.countries_with_input == 0
    assert summary.unknown_skill_rows == 1
    assert repository.gaps == []


def test_negative_known_count_fails_the_job():
    negative = MemoryRepository({("DE", date(2026, 10, 1)): (StoredCount("skill", "Linux", -1),)})
    with pytest.raises(ValueError, match="nonnegative"):
        run_gap_job(negative, TAXONOMY, TargetMonth(2026, 10), GapConfig())


def test_write_failure_fails_the_job():
    repository = MemoryRepository(
        demand={("DE", date(2026, 10, 1)): (StoredCount("skill", "Linux", 1),)},
        fail_write=True,
    )
    with pytest.raises(RuntimeError, match="write failed"):
        run_gap_job(repository, TAXONOMY, TargetMonth(2026, 10), GapConfig())

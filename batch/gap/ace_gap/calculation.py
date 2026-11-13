"""Pure Skills Gap and ECSF role-relation calculations."""

from __future__ import annotations

from collections.abc import Mapping

from .taxonomy import Taxonomy
from .types import GapRow, RoleRelation


def calculate_gap(
    country: str,
    month: str,
    taxonomy: Taxonomy,
    demand: Mapping[str, int],
    supply: Mapping[str, int],
    min_supply: int,
) -> tuple[GapRow, ...]:
    if min_supply <= 0:
        raise ValueError("min_supply must be positive")
    _validate_counts("Demand", taxonomy, demand)
    _validate_counts("Supply", taxonomy, supply)
    return tuple(
        GapRow(
            country=country,
            month=month,
            skill=skill,
            demand=demand.get(skill, 0),
            supply=supply.get(skill, 0),
            gap_ratio=(demand.get(skill, 0) / supply.get(skill, 0))
            if supply.get(skill, 0) >= min_supply
            else None,
        )
        for skill in taxonomy.skills
    )


def calculate_role_relations(
    taxonomy: Taxonomy,
    aggregate_demand: Mapping[str, int],
    top_n: int = 10,
) -> tuple[RoleRelation, ...]:
    if top_n <= 0:
        raise ValueError("top_n must be positive")
    _validate_counts("Demand", taxonomy, aggregate_demand)
    top_skills: dict[str, frozenset[str]] = {}
    for role in taxonomy.roles:
        candidates = [
            skill
            for skill in taxonomy.skills
            if role in taxonomy.roles_for(skill) and aggregate_demand.get(skill, 0) > 0
        ]
        ranked = sorted(candidates, key=lambda skill: (-aggregate_demand[skill], skill.casefold(), skill))
        top_skills[role] = frozenset(ranked[:top_n])

    relations: list[RoleRelation] = []
    for role in taxonomy.roles:
        for related in taxonomy.roles:
            if role == related:
                continue
            union = top_skills[role] | top_skills[related]
            overlap = len(top_skills[role] & top_skills[related]) / len(union) if union else 0.0
            relations.append(RoleRelation(role, related, overlap))
    return tuple(relations)


def _validate_counts(label: str, taxonomy: Taxonomy, counts: Mapping[str, int]) -> None:
    canonical = set(taxonomy.skills)
    unknown = sorted(set(counts) - canonical, key=str.casefold)
    if unknown:
        raise ValueError(f"{label} contains unknown canonical Skills: {unknown}")
    invalid = {skill: count for skill, count in counts.items() if isinstance(count, bool) or not isinstance(count, int) or count < 0}
    if invalid:
        raise ValueError(f"{label} counts must be nonnegative integers: {invalid}")

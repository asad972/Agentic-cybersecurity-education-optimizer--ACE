import pytest

from ace_gap.calculation import calculate_gap, calculate_role_relations
from ace_gap.taxonomy import Taxonomy


TAXONOMY = Taxonomy(
    skills=("Alpha", "Beta", "Delta", "Gamma"),
    roles=("Architect", "Implementer", "Researcher"),
    skill_roles=(
        ("Alpha", ("Architect", "Implementer")),
        ("Beta", ("Architect",)),
        ("Delta", ("Researcher",)),
        ("Gamma", ("Implementer",)),
    ),
)


def test_gap_writes_every_canonical_skill_in_order():
    rows = calculate_gap(
        "DE",
        "2026-10",
        TAXONOMY,
        {"Alpha": 40, "Gamma": 0},
        {"Alpha": 20, "Beta": 19, "Gamma": 25},
        20,
    )
    assert [row.skill for row in rows] == list(TAXONOMY.skills)
    by_skill = {row.skill: row for row in rows}
    assert by_skill["Alpha"].gap_ratio == 2.0
    assert by_skill["Beta"].gap_ratio is None
    assert by_skill["Delta"].demand == by_skill["Delta"].supply == 0
    assert by_skill["Gamma"].gap_ratio == 0.0


def test_gap_accepts_float_result_and_rejects_unknown_or_invalid_counts():
    row = calculate_gap("FR", "2026-10", TAXONOMY, {"Alpha": 3}, {"Alpha": 20}, 20)[0]
    assert row.gap_ratio == pytest.approx(0.15)
    with pytest.raises(ValueError, match="unknown"):
        calculate_gap("FR", "2026-10", TAXONOMY, {"Unknown": 1}, {}, 20)
    with pytest.raises(ValueError, match="nonnegative"):
        calculate_gap("FR", "2026-10", TAXONOMY, {"Alpha": -1}, {}, 20)


def test_role_relations_are_ordered_symmetric_jaccard_pairs():
    relations = calculate_role_relations(
        TAXONOMY,
        {"Alpha": 10, "Beta": 9, "Delta": 0, "Gamma": 8},
    )
    assert len(relations) == 6
    lookup = {(row.role, row.related_role): row.overlap for row in relations}
    assert lookup[("Architect", "Implementer")] == pytest.approx(1 / 3)
    assert lookup[("Implementer", "Architect")] == pytest.approx(1 / 3)
    assert lookup[("Architect", "Researcher")] == 0.0
    assert lookup[("Researcher", "Architect")] == 0.0


def test_role_top_ten_uses_positive_demand_and_deterministic_ties():
    skills = tuple(f"Skill {index:02d}" for index in range(12))
    taxonomy = Taxonomy(
        skills=skills,
        roles=("A", "B"),
        skill_roles=tuple((skill, ("A", "B") if index == 0 else ("A",)) for index, skill in enumerate(skills)),
    )
    demand = {skill: 1 for skill in reversed(skills)}
    relations = calculate_role_relations(taxonomy, demand, top_n=10)
    # A keeps Skill 00..09 after the canonical tie-break; B contains Skill 00.
    assert relations[0].overlap == pytest.approx(0.1)
    assert relations[1].overlap == pytest.approx(0.1)


def test_empty_role_sets_have_zero_overlap():
    relations = calculate_role_relations(TAXONOMY, {})
    assert all(row.overlap == 0.0 for row in relations)

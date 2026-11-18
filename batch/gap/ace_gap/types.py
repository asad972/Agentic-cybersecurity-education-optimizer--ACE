"""Immutable result values."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GapRow:
    country: str
    month: str
    skill: str
    demand: int
    supply: int
    gap_ratio: float | None


@dataclass(frozen=True, slots=True)
class RoleRelation:
    role: str
    related_role: str
    overlap: float

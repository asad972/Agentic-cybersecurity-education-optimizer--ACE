"""Compatibility helper backed by the shared Skill Taxonomy."""

from __future__ import annotations

from pathlib import Path

from ace.taxonomy import TaxonomyError, load_taxonomy


def load_skills(taxonomy_dir: Path) -> tuple[str, ...]:
    """Return canonical Skills through :mod:`ace.taxonomy`."""
    return load_taxonomy(taxonomy_dir).skills


__all__ = ["TaxonomyError", "load_skills"]

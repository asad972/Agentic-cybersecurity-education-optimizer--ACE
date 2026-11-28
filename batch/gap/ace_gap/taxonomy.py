"""Compatibility exports backed by the shared Skill Taxonomy."""

from __future__ import annotations

from ace.taxonomy import Taxonomy, TaxonomyError, load_taxonomy

__all__ = ["Taxonomy", "TaxonomyError", "load_taxonomy"]

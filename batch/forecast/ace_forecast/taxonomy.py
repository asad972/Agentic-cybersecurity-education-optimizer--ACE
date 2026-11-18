"""Canonical Skill loading with a contribution fallback during taxonomy assembly."""

from __future__ import annotations

import csv
from pathlib import Path


class TaxonomyError(ValueError):
    pass


def load_skills(taxonomy_dir: Path) -> tuple[str, ...]:
    canonical = taxonomy_dir / "skills.csv"
    if canonical.is_file():
        sources = (canonical,)
    else:
        sources = tuple(sorted((taxonomy_dir / "contributions").glob("*_skills.csv")))
    if not sources:
        raise TaxonomyError(f"No Skill taxonomy files found under {taxonomy_dir}")

    by_identity: dict[str, tuple[str, Path]] = {}
    for source in sources:
        with source.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if "skill" not in (reader.fieldnames or ()):
                raise TaxonomyError(f"Missing skill column in {source}")
            for row_number, row in enumerate(reader, start=2):
                name = (row.get("skill") or "").strip()
                if not name:
                    raise TaxonomyError(f"Blank canonical Skill in {source}:{row_number}")
                identity = name.casefold()
                if identity in by_identity:
                    previous, previous_source = by_identity[identity]
                    raise TaxonomyError(
                        f"Canonical Skill collision: {previous!r} in {previous_source} and {name!r} in {source}"
                    )
                by_identity[identity] = (name, source)
    return tuple(name for name, _ in sorted(by_identity.values(), key=lambda item: item[0].casefold()))

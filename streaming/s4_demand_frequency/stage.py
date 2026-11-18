"""S4 Demand frequency: Skills, Certifications and Roles per Country and day.

Reads skills.demand and publishes one agg.demand_daily message per Country and
day when the day closes, plus the number of job ads that day for the Europe map.

Each (Country, day) window keeps one Count-Min Sketch of every Skill,
Certification and Role mention. When the window closes, the stage asks the
sketch for every Skill Taxonomy entry, so the taxonomy is the key set, and
publishes each count above zero. A count is never below the true count and
at most epsilon * mentions that day above it (see count_min.py). The number
of job ads is counted exactly.

S2 only sends taxonomy entries, so an item S4 does not know means S4 loaded
an older taxonomy than S2. Such items go into the sketch like any other and
their names are kept in a small side set, so they are still published.
"""

from __future__ import annotations

from typing import Iterable, Iterator

from ace.stage import Window, WindowedStage
from s4_demand_frequency.count_min import DEPTH, WIDTH, CountMinSketch
from s4_demand_frequency.taxonomy_keys import Key, key_set

# At most this many unknown item names per window; more would mean S2 sends
# something other than taxonomy entries, and the side set must stay small.
UNKNOWN_LIMIT = 200


class DemandFrequency(WindowedStage):
    name = "s4-demand-count"
    input = "skills.demand"
    output = "agg.demand_daily"
    time_field = "posted_date"

    def __init__(self, keys: Iterable[Key] | None = None, width: int = WIDTH, depth: int = DEPTH):
        # Loaded once here, so Flink's workers get the key set with the stage
        # and do not need the taxonomy files.
        self.keys = frozenset(keys) if keys is not None else key_set()
        self.width = width
        self.depth = depth

    def new(self):
        return {"job_ads": 0, "sketch": CountMinSketch(self.width, self.depth), "unknown": set()}

    def add(self, state, record):
        state["job_ads"] += 1
        unknown = state["unknown"]
        for key in mentions(record):
            state["sketch"].add(cell(key))
            if key not in self.keys and len(unknown) < UNKNOWN_LIMIT:
                unknown.add(key)
        return state

    def result(self, key, window: Window, state, memory):
        sketch = state["sketch"]
        estimates = ((kind, item, sketch.estimate(cell((kind, item))))
                     for kind, item in sorted(self.keys | state["unknown"]))
        return [{
            "country": key,
            "day": window.day,
            "job_ads": state["job_ads"],
            "counts": [
                {"kind": kind, "item": item, "count": count}
                for kind, item, count in estimates if count > 0
            ],
        }]


def mentions(record) -> Iterator[Key]:
    """Every (kind, item) one job ad asks for."""
    for skill in record["skills"]:
        yield "skill", skill
    for certification in record.get("certifications", []):
        yield "certification", certification
    if "ecsf_role" in record:
        yield "role", record["ecsf_role"]


def cell(key: Key) -> str:
    """The name a (kind, item) has in the sketch; kind never contains a colon."""
    kind, item = key
    return f"{kind}:{item}"

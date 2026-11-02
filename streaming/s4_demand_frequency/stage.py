"""S4 Demand frequency: Skills, Certifications and Roles per Country and day.

Reads skills.demand and publishes one agg.demand_daily message per Country and
day when the day closes, plus the number of job ads that day for the Europe map.

Skeleton: counts exactly with a Counter. WBS 5.4 swaps in the Count-Min Sketch,
queried for every Skill Taxonomy entry, without changing the stage's shape.
"""

from collections import Counter

from ace.stage import Window, WindowedStage


class DemandFrequency(WindowedStage):
    name = "s4-demand-count"
    input = "skills.demand"
    output = "agg.demand_daily"
    time_field = "posted_date"

    def new(self):
        return {"job_ads": 0, "counts": Counter()}

    def add(self, state, record):
        state["job_ads"] += 1
        counts = state["counts"]
        for skill in record["skills"]:
            counts["skill", skill] += 1
        for certification in record.get("certifications", []):
            counts["certification", certification] += 1
        if "ecsf_role" in record:
            counts["role", record["ecsf_role"]] += 1
        return state

    def result(self, key, window: Window, state, memory):
        return [{
            "country": key,
            "day": window.day,
            "job_ads": state["job_ads"],
            "counts": [
                {"kind": kind, "item": item, "count": count}
                for (kind, item), count in sorted(state["counts"].items())
            ],
        }]

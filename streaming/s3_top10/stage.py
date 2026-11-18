"""S3 Top 10: Top 10 Skills and Top 10 Certifications per Country over 30 days.

Reads skills.demand and keeps one Space-Saving summary per kind (Skill,
Certification) for each Country and day. When a day closes, its summaries are
kept in the Country's memory with those of the 29 days before it; the 30 are
merged, and two agg.top10_demand messages are published for that Country and
day: the Top 10 Skills and the Top 10 Certifications of the last 30 days.

Capacity: 100 counters per kind and day. Space-Saving overestimates a count by
at most N/k, here 1% of the mentions of that kind in the 30 days, and every
item with more than 1% of them is sure to be kept in the summary. Two items
can only swap places when their counts are less than 1% apart; the 10th of about
100 taxonomy entries normally holds far more than 1%, so the Top 10 is
reliable. While a Country has no more than 100 different Skills (or
Certifications) in a day, nothing is evicted and the Top 10 counts are
exact. Memory stays small: 30 days x 2 kinds x 100 counters per Country.
"""

from datetime import date, timedelta
from functools import reduce

from ace.stage import Window, WindowedStage

from s3_top10.space_saving import SpaceSaving

CAPACITY = 100          # counters per summary (k)
DAYS = 30               # length of the Top 10 window
TOP = 10
KINDS = ("skill", "certification")


class TopTen(WindowedStage):
    name = "s3-top10"
    input = "skills.demand"
    output = "agg.top10_demand"
    time_field = "posted_date"

    def new(self):
        return {kind: SpaceSaving(CAPACITY) for kind in KINDS}

    def add(self, state, record):
        # Demand counts job ads, so a job ad counts once per Skill or
        # Certification, even if it lists one twice. dict.fromkeys keeps the
        # listed order, so every run evicts the same counters.
        for skill in dict.fromkeys(record["skills"]):
            state["skill"].add(skill)
        for certification in dict.fromkeys(record.get("certifications", [])):
            state["certification"].add(certification)
        return state

    def result(self, key, window: Window, state, memory):
        days = memory.setdefault("days", {})    # "YYYY-MM-DD" -> the day's summaries
        days[window.day] = state
        today = window.start.date()
        first = (today - timedelta(days=DAYS - 1)).isoformat()
        # Forget days that can no longer fall inside any later 30-day window.
        newest = max(date.fromisoformat(day) for day in days)
        oldest_needed = (newest - timedelta(days=DAYS - 1)).isoformat()
        for day in [d for d in days if d < oldest_needed]:
            del days[day]

        in_window = sorted(d for d in days if first <= d <= window.day)
        messages = []
        for kind in KINDS:
            merged = reduce(SpaceSaving.merge, (days[d][kind] for d in in_window),
                            SpaceSaving(CAPACITY))
            messages.append({
                "country": key,
                "day": window.day,
                "kind": kind,
                "items": [
                    {"rank": rank, "item": entry.item, "count": entry.count}
                    for rank, entry in enumerate(merged.top(TOP), start=1)
                ],
            })
        return messages

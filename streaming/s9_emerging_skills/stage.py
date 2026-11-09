"""S9: aggregate unmapped skill mentions through the shared Flink stage base."""

from typing import Any

from ace.stage import Window, WindowedStage

from .lossy_counting import EmergingSkillsCounter, LossyCounting


class EmergingSkillsStage(WindowedStage):
    """Emit the most frequent unmapped skills per country for a rolling 30 days."""

    name = "s9-emerging-skills"
    input = "skills.demand"
    output = "agg.emerging_skills"
    time_field = "posted_date"

    def __init__(self, epsilon: float = 0.01, limit: int = 50) -> None:
        # Keep the sketch configuration consistent across daily windows.
        self.epsilon = epsilon
        self.limit = limit
        # Validate configuration immediately instead of failing inside a job.
        EmergingSkillsCounter(epsilon=epsilon, limit=limit)

    def new(self) -> LossyCounting:
        # Each country/day window gets a bounded-memory Lossy Counting sketch.
        return LossyCounting(epsilon=self.epsilon)

    def add(self, state: LossyCounting, record: dict[str, Any]) -> LossyCounting:
        # S2 includes unmapped only when it has mentions, so absence means none.
        mentions = record.get("unmapped", [])
        if not isinstance(mentions, list):
            raise ValueError("skills.demand unmapped must be a list")

        # Count each unmapped mention in this event-time window.
        for mention in mentions:
            if not isinstance(mention, str):
                continue
            state.add(mention)
        return state

    def result(
        self, key: str, window: Window, state: LossyCounting, memory: dict
    ) -> list[dict[str, Any]]:
        # Keep completed day sketches in Flink's checkpointed per-country memory.
        counter = memory.setdefault(
            "emerging_skills",
            EmergingSkillsCounter(epsilon=self.epsilon, limit=self.limit),
        )
        if not isinstance(counter, EmergingSkillsCounter):
            raise TypeError("S9 checkpoint memory has an unexpected value")

        # The shared base calls result once, when this event-time day closes.
        counter.add_daily_sketch(key, window.start.date(), state)
        return [counter.emit(key, window.start.date())]

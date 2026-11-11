"""Country-specific Half-Space Trees over finalized hourly threat counts."""

from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass

import river
from river import anomaly

from ace import contracts
from ace.stage import RecordStage

TYPES = ("malware", "phishing", "ddos", "scanning", "spam", "brute_force",
         "abuse_report", "indicator", "other")


@dataclass(frozen=True)
class BurstConfig:
    threshold: float = 0.50
    warmup_hours: int = 48
    window_size: int = 48
    n_trees: int = 25
    height: int = 8
    max_feature_count: int = 1000
    upward_ratio: float = 3.0
    min_total: int = 15
    baseline_alpha: float = 0.05
    seed: int = 42

    def __post_init__(self):
        integers = (self.warmup_hours, self.window_size, self.n_trees,
                    self.height, self.max_feature_count, self.min_total, self.seed)
        if any(type(value) is not int for value in integers):
            raise ValueError("model sizes, counts and seed must be integers")
        if not 0 <= self.threshold <= 1:
            raise ValueError("threshold must be between 0 and 1")
        if self.window_size < 2 or self.warmup_hours < self.window_size:
            raise ValueError("warmup must cover at least one model window")
        if self.n_trees < 1 or not 1 <= self.height <= 12 or self.max_feature_count < 1:
            raise ValueError("invalid HST model bounds")
        if (not math.isfinite(self.upward_ratio) or self.upward_ratio <= 1
                or self.min_total < 1 or not 0 < self.baseline_alpha <= 1):
            raise ValueError("invalid upward-burst baseline configuration")


class ThreatBurstStage(RecordStage):
    """One model in ace's checkpointed memory per Country, never on self."""

    name = "s8-threat-bursts"

    def __init__(self, config: BurstConfig | None = None):
        self.config = config or BurstConfig()
        self.inputs = (contracts.topic("agg.threats_hourly").name,)
        self.output = contracts.topic("alerts.threats").name

    @staticmethod
    def grouped_counts(record: dict) -> dict[str, int]:
        counts = dict.fromkeys(TYPES, 0)
        for kind, count in record["counts"].items():
            counts[kind if kind in counts else "other"] += int(count)
        return counts

    def features(self, record: dict) -> dict[str, float]:
        denominator = math.log1p(self.config.max_feature_count)
        return {
            **{kind: math.log1p(min(count, self.config.max_feature_count)) / denominator
               for kind, count in self.grouped_counts(record).items()},
            "hour": int(record["hour"]) / 23.0,
        }

    def _state(self, country: str, memory: dict) -> None:
        signature = {"state_version": 1, "river_version": river.__version__,
                     "config": asdict(self.config), "features": TYPES}
        if memory:
            if memory.get("signature") != signature:
                raise ValueError("S8 model configuration or River version changed; "
                                 "explicit state migration required")
            return
        seed = self.config.seed + int(hashlib.sha256(country.encode()).hexdigest()[:8], 16)
        memory.update(
            signature=signature,
            model=anomaly.HalfSpaceTrees(
                n_trees=self.config.n_trees, height=self.config.height,
                window_size=self.config.window_size, seed=seed,
                limits={name: (0.0, 1.0) for name in (*TYPES, "hour")},
            ),
            baseline=dict.fromkeys(TYPES, 0.0), samples=0, last_hour=None,
            duplicate_records=0, out_of_order_records=0,
        )

    def process(self, topic: str, record: dict, memory: dict):
        # The shared aggregate schema also covers daily rows, so hour is optional
        # there. S8 requires it, without changing that shared schema for others.
        reason = None
        if topic != self.inputs[0]:
            reason = "S8 accepts only finalized hourly threat aggregates"
        elif "hour" not in record:
            reason = "S8 hourly aggregate requires hour (0-23, UTC)"
        if reason:
            yield contracts.INVALID_TOPIC, contracts.invalid_record(topic, self.name, reason, record)
            return

        country = record["country"]
        hour = f"{record['day']}T{int(record['hour']):02}:00:00Z"
        self._state(country, memory)
        previous = memory["last_hour"]
        if previous is not None and hour <= previous:
            counter = "duplicate_records" if hour == previous else "out_of_order_records"
            memory[counter] += 1
            return

        model = memory["model"]
        features = self.features(record)
        counts = self.grouped_counts(record)
        baseline = memory["baseline"]
        total, normal_total = sum(counts.values()), sum(baseline.values())
        # Test then train: an observation must not teach its own reference level.
        score = float(model.score_one(features))
        enough_history = memory["samples"] >= self.config.warmup_hours
        upward = total >= self.config.min_total and total > normal_total * self.config.upward_ratio
        alert = None
        if enough_history and upward and score >= self.config.threshold:
            subject = max(sorted(counts), key=lambda kind: counts[kind] - baseline[kind])
            alert = {
                "country": country, "day": record["day"], "created_at": hour,
                "alert_type": "threat_burst", "subject": subject, "score": round(score, 6),
                "details": (
                    f"Hourly feed observations={total}; prior EWMA={normal_total:.2f}; "
                    f"HST={score:.4f} >= {self.config.threshold:.2f}; "
                    f"largest upward type group: {subject}={counts[subject]} "
                    f"(baseline {baseline[subject]:.2f}). "
                    "Unknown threat types are grouped into other. "
                    "created_at identifies the affected UTC hour, not delivery time. "
                    "Feed observations are not confirmed attacks."
                ),
            }

        model.learn_one(features)
        alpha = self.config.baseline_alpha
        for kind, count in counts.items():
            baseline[kind] = (count if memory["samples"] == 0 else
                              (1 - alpha) * baseline[kind] + alpha * count)
        memory["samples"] += 1
        memory["last_hour"] = hour
        # ace stores this picklable model/baseline in Flink keyed state. Tree
        # size and the type vocabulary are fixed; no event history is retained.
        if alert is not None:
            yield self.output, alert

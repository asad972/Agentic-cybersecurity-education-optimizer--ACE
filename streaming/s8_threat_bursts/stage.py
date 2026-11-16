"""Country-specific Half-Space Trees over finalized hourly threat counts."""

from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import river
from river import anomaly

from ace import contracts
from ace.stage import LATE_METRIC, RecordStage

LOGGER = logging.getLogger(__name__)
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
MODEL_FIELDS = ("n_trees", "height", "window_size", "max_feature_count", "seed")

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
    cooldown_hours: int = 6

    def __post_init__(self):
        integers = (self.warmup_hours, self.window_size, self.n_trees,
                    self.height, self.max_feature_count, self.min_total, self.seed,
                    self.cooldown_hours)
        if any(type(value) is not int for value in integers):
            raise ValueError("model sizes, counts and seed must be integers")
        if not 0 <= self.threshold <= 1:
            raise ValueError("threshold must be between 0 and 1")
        if self.window_size < 2 or self.warmup_hours < self.window_size:
            raise ValueError("warmup must cover at least one model window")
        if self.n_trees < 1 or not 1 <= self.height <= 12 or self.max_feature_count < 1:
            raise ValueError("invalid HST model bounds")
        if (not math.isfinite(self.upward_ratio) or self.upward_ratio <= 1
                or self.min_total < 1 or not 0 < self.baseline_alpha <= 1
                or self.cooldown_hours < 0):
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
        signature = {"state_version": 2, "river_version": river.__version__,
                     "model_config": {name: getattr(self.config, name) for name in MODEL_FIELDS},
                     "features": TYPES}
        if memory:
            previous = memory.get("signature", {})
            # Upgrade the original S8 checkpoint without discarding a trained
            # model. Alert policy is deliberately not a model compatibility key.
            legacy = previous.get("state_version") == 1
            if legacy:
                previous = {**previous, "state_version": 2,
                            "model_config": {name: previous.get("config", {}).get(name)
                                             for name in MODEL_FIELDS}}
                previous.pop("config", None)
            if previous != signature:
                raise ValueError("S8 model configuration or River version changed; "
                                 "explicit state migration required")
            if legacy:
                if memory["last_hour"] is not None:
                    memory["last_hour"] = int(
                        (datetime.fromisoformat(memory["last_hour"]) - EPOCH).total_seconds() // 3600)
                memory.update(signature=signature, last_alerts={})
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
            duplicate_records=0, out_of_order_records=0, last_alerts={},
        )

    def _learn(self, memory: dict, counts: dict, features: dict) -> None:
        memory["model"].learn_one(features)
        alpha = self.config.baseline_alpha
        for kind, count in counts.items():
            memory["baseline"][kind] = (count if memory["samples"] == 0 else
                                       (1 - alpha) * memory["baseline"][kind] + alpha * count)
        memory["samples"] += 1

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
        moment = datetime.fromisoformat(record["day"]).replace(tzinfo=timezone.utc) + timedelta(
            hours=int(record["hour"]))
        hour = (moment - EPOCH).days * 24 + moment.hour
        self._state(country, memory)
        previous = memory["last_hour"]
        if previous is not None and hour <= previous:
            counter = "duplicate_records" if hour == previous else "out_of_order_records"
            memory[counter] += 1
            LOGGER.warning("%s stage=%s country=%s reason=%s hour=%d last_hour=%d dropped=1 total=%d",
                           LATE_METRIC, self.name, country, counter, hour, previous, memory[counter])
            return

        # S7 omits empty windows. Between two finalized observations, advance
        # the model, baseline and warm-up once for every elapsed UTC hour.
        if previous is not None:
            for quiet_hour in range(previous + 1, hour):
                quiet = {"counts": {}, "hour": quiet_hour % 24}
                self._learn(memory, self.grouped_counts(quiet), self.features(quiet))

        # At most one new entry per processed hour, retained only for cooldown.
        last_alerts = memory["last_alerts"]
        for subject, emitted_hour in list(last_alerts.items()):
            if hour - emitted_hour >= self.config.cooldown_hours:
                del last_alerts[subject]

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
            group = max(sorted(counts), key=lambda kind: counts[kind] - baseline[kind])
            # Keep the original collector label in the alert. Unknown types
            # share only the model feature/baseline, never the emitted subject.
            candidates = {kind: count for kind, count in record["counts"].items()
                          if (kind if kind in TYPES else "other") == group}
            subject = max(sorted(candidates), key=candidates.get)
            alert = {
                "country": country, "day": record["day"],
                "created_at": moment.isoformat().replace("+00:00", "Z"),
                "alert_type": "threat_burst", "subject": subject, "score": round(score, 6),
                "details": (
                    f"Hourly feed observations={total}; prior EWMA={normal_total:.2f}; "
                    f"HST={score:.4f} >= {self.config.threshold:.2f}; "
                    f"largest upward model group: {group}={counts[group]} "
                    f"(baseline {baseline[group]:.2f}); "
                    f"source type: {subject}={candidates[subject]}. "
                    "created_at identifies the affected UTC hour, not delivery time. "
                    "Feed observations are not confirmed attacks."
                ),
            }
            if subject in last_alerts:
                alert = None
            else:
                last_alerts[subject] = hour

        self._learn(memory, counts, features)
        memory["last_hour"] = hour
        # ace stores this picklable model/baseline in Flink keyed state. Tree
        # size and the type vocabulary are fixed; no event history is retained.
        if alert is not None:
            yield self.output, alert

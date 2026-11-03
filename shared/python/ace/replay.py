import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Callable, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)


class EventReplayer:
    """Replays static dataset records in event-time order into Kafka or a collector callback.

    Attributes:
        date_field: The key in each JSON record representing its event date (e.g.
          'posted_date').
        seconds_per_day: Real-world seconds representing one day of simulated
          event time.
    """

    def __init__(
        self,
        date_field: str = "posted_date",
        date_format: str = "%Y-%m-%d",
        seconds_per_day: float = 1.0,
    ):
        self.date_field = date_field
        self.date_format = date_format
        self.seconds_per_day = float(
            os.getenv("REPLAY_SECONDS_PER_DAY", seconds_per_day)
        )
        self.current_position = 0

    def load_and_sort(
        self, file_path: str, resume_from: int = 0
    ) -> List[Dict[str, Any]]:
        """Reads a JSON Lines file, sorts entries chronologically by event date, and skips already processed records."""
        records = []
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Dataset file not found: {file_path}")

        with open(file_path, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    records.append(record)
                except json.JSONDecodeError as err:
                    logger.warning(
                        "Skipping invalid JSON at line %d: %s", line_num, err
                    )

        # Ordenar cronológicamente por la fecha del evento
        def parse_date(rec: Dict[str, Any]) -> datetime:
            val = rec.get(self.date_field)
            if not val:
                return datetime.min
            try:
                return datetime.strptime(str(val)[:10], self.date_format)
            except ValueError:
                return datetime.min

        records.sort(key=parse_date)
        self.current_position = resume_from
        return records[resume_from:]

    def replay_stream(
        self,
        records: List[Dict[str, Any]],
        emit_fn: Callable[[Dict[str, Any]], None],
        on_progress: Optional[Callable[[int], None]] = None,
    ) -> None:
        """Emits records sequentially, pausing between distinct dates to simulate event time progression."""
        prev_date: Optional[datetime] = None

        for idx, record in enumerate(records):
            current_date_str = record.get(self.date_field)
            if current_date_str:
                try:
                    curr_date = datetime.strptime(
                        str(current_date_str)[:10], self.date_format
                    )
                    if prev_date and curr_date > prev_date:
                        days_diff = (curr_date - prev_date).days
                        sleep_time = days_diff * self.seconds_per_day
                        if sleep_time > 0:
                            time.sleep(sleep_time)
                    prev_date = curr_date
                except ValueError:
                    pass

            emit_fn(record)
            self.current_position += 1

            if on_progress and (idx + 1) % 25 == 0:
                on_progress(self.current_position)


def replay_file_to_kafka(
    file_path: str,
    date_field: str,
    producer: Any,
    topic: str,
    key_field: str = "country",
    seconds_per_day: float = 0.5,
) -> int:
    """Convenience helper to replay a file directly into a Kafka producer."""
    replayer = EventReplayer(
        date_field=date_field, seconds_per_day=seconds_per_day
    )
    records = replayer.load_and_sort(file_path)

    def publish_kafka(msg: Dict[str, Any]) -> None:
        key = str(msg.get(key_field, "EU")).encode("utf-8")
        producer.send(topic, key=key, value=msg)

    replayer.replay_stream(records, emit_fn=publish_kafka)
    return replayer.current_position
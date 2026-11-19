"""Collector-local serialization, UTC timestamps and ISO country checks."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pycountry


def json_text(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must have an explicit timezone")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return datetime.fromisoformat(utc_text(parsed).replace("Z", "+00:00"))


def country_code(value: str) -> str:
    code = str(value).strip().upper()
    if pycountry.countries.get(alpha_2=code) is None:
        raise ValueError("source did not provide a valid ISO country")
    return code


def write_jsonl(path: str, events: list[dict]):
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as output:
        for row in events:
            output.write(json_text(row) + "\n")

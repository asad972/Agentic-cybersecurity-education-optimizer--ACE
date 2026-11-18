"""Turns one Kafka message into the Cassandra rows it is stored as.

Every topic the sink reads has one small function in ace_sink/topics/ that
takes a message and the time the sink received it, and returns its rows, one
per target table:

    @rows_for("agg.companies_daily")
    def companies(message, received_at):
        return [Row("companies_by_day", {"day": ..., "country": ..., ...})]

The functions only build rows; they never touch Cassandra, so they are tested
with plain messages and no database. Writing a row is an upsert (a Cassandra
INSERT replaces the row with the same primary key), so a message that is
written twice leaves the same rows behind.
"""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from ace import contracts

RowFunction = Callable[[dict[str, Any], datetime], list["Row"]]

_functions: dict[str, RowFunction] = {}


class Unstorable(ValueError):
    """A message that passed its schema check but cannot be stored, e.g. an hourly count with no hour.

    The sink sends it to errors.invalid. Any other error in a row function is a
    bug and stops the sink, so it is noticed instead of hiding valid messages.
    """


@dataclass(frozen=True)
class Row:
    """One row for one table: column name -> value. None is stored as null."""

    table: str
    values: dict[str, Any]


def rows_for(topic_name: str) -> Callable[[RowFunction], RowFunction]:
    """Register the row function for a topic; an unknown topic name fails at once."""
    contracts.topic(topic_name)

    def register(function: RowFunction) -> RowFunction:
        if topic_name in _functions:
            raise ValueError(f"{topic_name} already has a row function")
        _functions[topic_name] = function
        return function

    return register


def topics() -> list[str]:
    """The topics the sink can write, in topics.csv order."""
    return [name for name in contracts.topics() if name in _functions]


def rows(topic_name: str, message: dict[str, Any], received_at: datetime) -> list[Row]:
    """The rows one message on topic_name is stored as."""
    if topic_name not in _functions:
        raise KeyError(f"the sink has no row function for {topic_name}")
    return _functions[topic_name](message, received_at)


def _load_topic_modules() -> None:
    """Import every module in ace_sink/topics/, so their row functions register."""
    from ace_sink import topics as package

    for module in pkgutil.iter_modules(package.__path__):
        importlib.import_module(f"{package.__name__}.{module.name}")


_load_topic_modules()

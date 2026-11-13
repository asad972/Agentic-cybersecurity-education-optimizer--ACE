"""Writes rows to Cassandra with prepared INSERT statements.

A Cassandra INSERT is an upsert: it replaces the row with the same primary
key, so writing the same rows twice changes nothing. None is written as null,
so a message that no longer has an optional value clears the old one.

Every row is bound to its statement before anything is written. A value the
driver cannot bind (e.g. 3.0 or 3000000000 for an int column) makes the whole
message Unstorable, so it goes to errors.invalid instead of stopping the sink.

A row Cassandra itself refuses (e.g. a key longer than 64 KB) is Unstorable
too. Which driver errors mean "Cassandra refused this row" is passed in as
rejected, so the tests need no driver; any other error (Cassandra unavailable,
a timeout) still stops the sink.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from ace_sink.rows import Row, Unstorable

_NAME = re.compile(r"[a-z_][a-z0-9_]*")


class CassandraWriter:
    def __init__(self, session: Any, rejected: tuple[type[BaseException], ...] = ()):
        self.session = session
        self.rejected = rejected
        self._statements: dict[tuple[str, tuple[str, ...]], Any] = {}

    def _statement(self, table: str, columns: tuple[str, ...]) -> Any:
        """The prepared INSERT for this table and set of columns, prepared once."""
        key = (table, columns)
        if key not in self._statements:
            for name in (table, *columns):
                if not _NAME.fullmatch(name):
                    raise ValueError(f"not a table or column name: {name!r}")
            placeholders = ", ".join("?" for _ in columns)
            cql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
            self._statements[key] = self.session.prepare(cql)
        return self._statements[key]

    def write(self, rows: Iterable[Row]) -> None:
        """Write all rows. Raises Unstorable for values that cannot be bound or that
        Cassandra refuses, and the driver's own error if Cassandra fails, so the
        message is not marked as done."""
        bound = []
        for row in rows:
            statement = self._statement(row.table, tuple(row.values))
            try:
                bound.append((row.table, statement.bind(tuple(row.values.values()))))
            except (TypeError, ValueError, OverflowError) as error:
                raise Unstorable(f"{row.table}: {error}") from error
        pending = [(table, self.session.execute_async(statement)) for table, statement in bound]
        refused = None
        for table, future in pending:
            try:
                future.result()
            except self.rejected as error:
                refused = refused or Unstorable(f"{table}: Cassandra refused the row: {error}")
        if refused is not None:
            raise refused

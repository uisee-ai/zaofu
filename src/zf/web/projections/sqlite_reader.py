"""Read-only connections: a query must not initialize or maintain the store."""

from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
import sqlite3


@contextmanager
def connect_readonly(path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.2)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    try:
        yield conn
    finally:
        conn.close()

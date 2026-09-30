from __future__ import annotations

import sqlite3
from pathlib import Path


def test_generic_persistence_fixture_migration_constraints_idempotency_and_restore(tmp_path: Path):
    database = tmp_path / "product.db"
    connection = sqlite3.connect(database)
    try:
        connection.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE)")
        connection.execute("INSERT INTO items(name) VALUES (?)", ("one",))
        connection.commit()
        try:
            connection.execute("INSERT INTO items(name) VALUES (?)", ("one",))
            raise AssertionError("duplicate write must be rejected")
        except sqlite3.IntegrityError:
            connection.rollback()
        backup = tmp_path / "backup.db"
        target = sqlite3.connect(backup)
        try:
            connection.backup(target)
        finally:
            target.close()
    finally:
        connection.close()
    restored = sqlite3.connect(backup)
    try:
        assert restored.execute("SELECT name FROM items").fetchone() == ("one",)
    finally:
        restored.close()

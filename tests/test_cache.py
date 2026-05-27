from __future__ import annotations

import sqlite3

from fund_estimator.services.cache import SQLiteCache


def test_watchlist_schema_migration_keeps_legacy_default_rows(tmp_path):
    db_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE watchlist (
                code TEXT PRIMARY KEY,
                name TEXT,
                added_at TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            "INSERT INTO watchlist (code, name, added_at, sort_order) VALUES (?, ?, ?, ?)",
            ("001438", "旧自选", "2026-05-27T00:00:00+00:00", 0),
        )

    cache = SQLiteCache(db_path)

    default_rows = cache.list_watchlist()
    phone_rows = cache.list_watchlist("phone-a")

    assert [row["code"] for row in default_rows] == ["001438"]
    assert [row["code"] for row in phone_rows] == ["001438"]
    cache.delete_watchlist("001438", "phone-a")
    assert cache.list_watchlist("phone-a") == []
    assert [row["code"] for row in cache.list_watchlist()] == ["001438"]

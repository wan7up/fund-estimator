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

    # 旧表数据仍归 default 存档可读、可删；新设备不继承存档，
    # 默认只带一只候选基金。
    assert [row["code"] for row in default_rows] == ["001438"]
    assert [row["code"] for row in phone_rows] == ["011370"]
    cache.delete_watchlist("001438")
    assert cache.list_watchlist() == []
    assert [row["code"] for row in cache.list_watchlist("phone-a")] == ["011370"]


def test_watchlist_add_inserts_at_top_and_existing_moves_to_top(tmp_path):
    cache = SQLiteCache(tmp_path / "watchlist_top.sqlite3")

    cache.add_watchlist("000001", "first", "phone-a")
    cache.add_watchlist("001438", "second", "phone-a")
    cache.add_watchlist("001433", "third", "phone-a")

    # phone-a 是新设备：列表尾部带默认候选 011370
    assert [row["code"] for row in cache.list_watchlist("phone-a")] == ["001433", "001438", "000001", "011370"]

    cache.add_watchlist("000001", "first renamed", "phone-a")
    rows = cache.list_watchlist("phone-a")

    assert [row["code"] for row in rows] == ["000001", "001433", "001438", "011370"]
    assert rows[0]["name"] == "first renamed"

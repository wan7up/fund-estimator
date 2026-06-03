from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


class SQLiteCache:
    def __init__(self, path: str | Path = "data/fund_estimator.sqlite3") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cache_entries (
                    namespace TEXT NOT NULL,
                    cache_key TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    ttl_seconds INTEGER NOT NULL,
                    PRIMARY KEY (namespace, cache_key)
                )
                """
            )
            self._init_watchlist_tables(conn)
            self._init_lof_watchlist_tables(conn)

    def _init_watchlist_tables(self, conn: sqlite3.Connection) -> None:
        columns = conn.execute("PRAGMA table_info(watchlist)").fetchall()
        if not columns:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS watchlist (
                    device_id TEXT NOT NULL DEFAULT 'default',
                    code TEXT NOT NULL,
                    name TEXT,
                    added_at TEXT NOT NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (device_id, code)
                )
                """
            )
        else:
            column_names = {row["name"] for row in columns}
            if "device_id" not in column_names:
                rows = conn.execute("SELECT * FROM watchlist ORDER BY added_at ASC").fetchall()
                conn.execute(
                    """
                    CREATE TABLE watchlist_new (
                        device_id TEXT NOT NULL DEFAULT 'default',
                        code TEXT NOT NULL,
                        name TEXT,
                        added_at TEXT NOT NULL,
                        sort_order INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY (device_id, code)
                    )
                    """
                )
                has_sort_order = "sort_order" in column_names
                for index, row in enumerate(rows):
                    sort_order = row["sort_order"] if has_sort_order and row["sort_order"] is not None else index
                    conn.execute(
                        """
                        INSERT INTO watchlist_new (device_id, code, name, added_at, sort_order)
                        VALUES ('default', ?, ?, ?, ?)
                        """,
                        (row["code"], row["name"], row["added_at"], sort_order),
                    )
                conn.execute("DROP TABLE watchlist")
                conn.execute("ALTER TABLE watchlist_new RENAME TO watchlist")
            elif "sort_order" not in column_names:
                conn.execute("ALTER TABLE watchlist ADD COLUMN sort_order INTEGER")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS watchlist_devices (
                device_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO watchlist_devices (device_id, created_at)
            VALUES ('default', ?)
            """,
            (datetime.now(UTC).isoformat(),),
        )
        device_rows = conn.execute("SELECT DISTINCT device_id FROM watchlist").fetchall()
        for row in device_rows:
            conn.execute(
                """
                INSERT OR IGNORE INTO watchlist_devices (device_id, created_at)
                VALUES (?, ?)
                """,
                (row["device_id"], datetime.now(UTC).isoformat()),
            )
        rows = conn.execute(
            "SELECT device_id, code FROM watchlist WHERE sort_order IS NULL ORDER BY device_id ASC, added_at ASC"
        ).fetchall()
        counters: dict[str, int] = {}
        for row in rows:
            device_id = row["device_id"]
            index = counters.get(device_id, 0)
            conn.execute(
                "UPDATE watchlist SET sort_order = ? WHERE device_id = ? AND code = ?",
                (index, device_id, row["code"]),
            )
            counters[device_id] = index + 1

    def _ensure_watchlist_device(self, conn: sqlite3.Connection, device_id: str) -> None:
        exists = conn.execute(
            "SELECT 1 FROM watchlist_devices WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        if exists:
            return
        conn.execute(
            "INSERT INTO watchlist_devices (device_id, created_at) VALUES (?, ?)",
            (device_id, datetime.now(UTC).isoformat()),
        )
        if device_id == "default":
            return
        rows = conn.execute(
            """
            SELECT code, name, added_at, sort_order
            FROM watchlist
            WHERE device_id = 'default'
            ORDER BY sort_order ASC, added_at ASC
            """
        ).fetchall()
        for row in rows:
            conn.execute(
                """
                INSERT INTO watchlist (device_id, code, name, added_at, sort_order)
                VALUES (?, ?, ?, ?, ?)
                """,
                (device_id, row["code"], row["name"], row["added_at"], row["sort_order"]),
            )

    def get(self, namespace: str, key: str, *, include_expired: bool = False) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload, fetched_at, ttl_seconds
                FROM cache_entries
                WHERE namespace = ? AND cache_key = ?
                """,
                (namespace, key),
            ).fetchone()
        if row is None:
            return None

        fetched_at = datetime.fromisoformat(row["fetched_at"])
        expires_at = fetched_at + timedelta(seconds=int(row["ttl_seconds"]))
        is_expired = datetime.now(UTC) >= expires_at
        if is_expired and not include_expired:
            return None

        payload = json.loads(row["payload"])
        payload["_cache"] = {
            "fetched_at": fetched_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "expired": is_expired,
        }
        return payload

    def set(self, namespace: str, key: str, payload: dict[str, Any], ttl_seconds: int) -> None:
        clean_payload = {k: v for k, v in payload.items() if k != "_cache"}
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO cache_entries (namespace, cache_key, payload, fetched_at, ttl_seconds)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(namespace, cache_key) DO UPDATE SET
                    payload = excluded.payload,
                    fetched_at = excluded.fetched_at,
                    ttl_seconds = excluded.ttl_seconds
                """,
                (
                    namespace,
                    key,
                    json.dumps(clean_payload, ensure_ascii=False, default=str),
                    datetime.now(UTC).isoformat(),
                    ttl_seconds,
                ),
            )

    def list_watchlist(self, device_id: str = "default") -> list[sqlite3.Row]:
        with self._connect() as conn:
            self._ensure_watchlist_device(conn, device_id)
            return list(
                conn.execute(
                    """
                    SELECT code, name, added_at, sort_order
                    FROM watchlist
                    WHERE device_id = ?
                    ORDER BY sort_order ASC, added_at ASC
                    """,
                    (device_id,),
                ).fetchall()
            )

    def add_watchlist(self, code: str, name: str | None = None, device_id: str = "default") -> None:
        with self._connect() as conn:
            self._ensure_watchlist_device(conn, device_id)
            conn.execute(
                "UPDATE watchlist SET sort_order = sort_order + 1 WHERE device_id = ?",
                (device_id,),
            )
            conn.execute(
                """
                INSERT INTO watchlist (device_id, code, name, added_at, sort_order)
                VALUES (?, ?, ?, ?, 0)
                ON CONFLICT(device_id, code) DO UPDATE SET
                    name = COALESCE(excluded.name, watchlist.name),
                    added_at = excluded.added_at,
                    sort_order = 0
                """,
                (device_id, code, name, datetime.now(UTC).isoformat()),
            )

    def delete_watchlist(self, code: str, device_id: str = "default") -> bool:
        with self._connect() as conn:
            self._ensure_watchlist_device(conn, device_id)
            cur = conn.execute("DELETE FROM watchlist WHERE device_id = ? AND code = ?", (device_id, code))
            return cur.rowcount > 0

    def reorder_watchlist(self, codes: list[str], device_id: str = "default") -> bool:
        with self._connect() as conn:
            self._ensure_watchlist_device(conn, device_id)
            rows = conn.execute("SELECT code FROM watchlist WHERE device_id = ?", (device_id,)).fetchall()
            current_codes = [row["code"] for row in rows]
            if len(codes) != len(set(codes)) or set(codes) != set(current_codes):
                return False
            for index, code in enumerate(codes):
                conn.execute(
                    "UPDATE watchlist SET sort_order = ? WHERE device_id = ? AND code = ?",
                    (index, device_id, code),
                )
            return True

    def _init_lof_watchlist_tables(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lof_watchlist (
                device_id TEXT NOT NULL DEFAULT 'default',
                code TEXT NOT NULL,
                name TEXT,
                added_at TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (device_id, code)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lof_watchlist_devices (
                device_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO lof_watchlist_devices (device_id, created_at)
            VALUES ('default', ?)
            """,
            (datetime.now(UTC).isoformat(),),
        )

    def _ensure_lof_watchlist_device(self, conn: sqlite3.Connection, device_id: str) -> None:
        exists = conn.execute(
            "SELECT 1 FROM lof_watchlist_devices WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        if exists:
            return
        conn.execute(
            "INSERT INTO lof_watchlist_devices (device_id, created_at) VALUES (?, ?)",
            (device_id, datetime.now(UTC).isoformat()),
        )

    def list_lof_watchlist(self, device_id: str = "default") -> list[sqlite3.Row]:
        with self._connect() as conn:
            self._ensure_lof_watchlist_device(conn, device_id)
            return list(
                conn.execute(
                    """
                    SELECT code, name, added_at, sort_order
                    FROM lof_watchlist
                    WHERE device_id = ?
                    ORDER BY sort_order ASC, added_at ASC
                    """,
                    (device_id,),
                ).fetchall()
            )

    def add_lof_watchlist(self, code: str, name: str | None = None, device_id: str = "default") -> None:
        with self._connect() as conn:
            self._ensure_lof_watchlist_device(conn, device_id)
            conn.execute(
                """
                INSERT INTO lof_watchlist (device_id, code, name, added_at, sort_order)
                VALUES (
                    ?,
                    ?,
                    ?,
                    ?,
                    COALESCE((SELECT MAX(sort_order) + 1 FROM lof_watchlist WHERE device_id = ?), 0)
                )
                ON CONFLICT(device_id, code) DO UPDATE SET name = COALESCE(excluded.name, lof_watchlist.name)
                """,
                (device_id, code, name, datetime.now(UTC).isoformat(), device_id),
            )

    def delete_lof_watchlist(self, code: str, device_id: str = "default") -> bool:
        with self._connect() as conn:
            self._ensure_lof_watchlist_device(conn, device_id)
            cur = conn.execute("DELETE FROM lof_watchlist WHERE device_id = ? AND code = ?", (device_id, code))
            return cur.rowcount > 0

    def reorder_lof_watchlist(self, codes: list[str], device_id: str = "default") -> bool:
        with self._connect() as conn:
            self._ensure_lof_watchlist_device(conn, device_id)
            rows = conn.execute("SELECT code FROM lof_watchlist WHERE device_id = ?", (device_id,)).fetchall()
            current_codes = [row["code"] for row in rows]
            if len(codes) != len(set(codes)) or set(codes) != set(current_codes):
                return False
            for index, code in enumerate(codes):
                conn.execute(
                    "UPDATE lof_watchlist SET sort_order = ? WHERE device_id = ? AND code = ?",
                    (index, device_id, code),
                )
            return True

"""
db_manager.py
─────────────
Async SQLite backend with a versioned migration system.

How migrations work
───────────────────
  1. On connect(), a `schema_migrations` table is created if it doesn't exist.
  2. Every entry in _MIGRATIONS has a unique integer version number.
  3. The runner checks which versions are already recorded and skips them.
  4. Only NEW migrations are applied, each in its own transaction.
  5. Adding a schema change = append one entry to _MIGRATIONS. That's it.
     Existing data is never touched unless the migration explicitly changes it.

To add a new migration
──────────────────────
  Append to _MIGRATIONS:

      (
          3,                              # next sequential version number
          "short human-readable name",
          [
              "ALTER TABLE foo ADD COLUMN bar TEXT NOT NULL DEFAULT ''",
              "CREATE INDEX IF NOT EXISTS ...",
          ],
      ),

  Safe restarts: if the server crashes mid-migration the transaction is rolled
  back and will be retried cleanly on the next start.

Request categories
──────────────────
  success       HTTP 2xx   scraper returned data correctly
  client_error  HTTP 4xx   caller sent bad params / wrong currency / bad date
  server_error  HTTP 5xx   scraper crashed, browser died, Playwright timeout

  Auth rejections (401 invalid key, 429 rate-limit) happen in the middleware
  BEFORE the endpoint runs and are NEVER written to endpoint stats.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import aiosqlite

logger = logging.getLogger(__name__)

# ── Endpoint metadata ──────────────────────────────────────────────────────── #

ENDPOINT_DISPLAY: Dict[str, str] = {
    "/myfxbook/events":     "MyFxBook Calendar",
    "/forexfactory/events": "ForexFactory Calendar",
    "/broker-spreads/live": "Broker Spreads Live",
}

_PREFIX_MAP: Dict[str, str] = {
    "/myfxbook":       "/myfxbook/events",
    "/forexfactory":   "/forexfactory/events",
    "/broker-spreads": "/broker-spreads/live",
}


def _canonical(path: str) -> str:
    for prefix, canonical in _PREFIX_MAP.items():
        if path.startswith(prefix):
            return canonical
    return path


def _category(status_code: int) -> str:
    if status_code < 400:  return "success"
    if status_code < 500:  return "client_error"
    return "server_error"


# ── Versioned migrations ───────────────────────────────────────────────────── #
#
# Rules:
#   - Never edit or delete an existing migration.
#   - Always append. Version numbers must be sequential integers.
#   - Each list entry is one SQL statement executed in the migration transaction.
#   - ALTER TABLE "duplicate column" errors are swallowed so migrations are safe
#     to run on databases that were hand-patched.

_MIGRATIONS: List[Tuple[int, str, List[str]]] = [

    # ── v1: initial schema ─────────────────────────────────────────────────── #
    (
        1,
        "Initial schema: api_keys, request_logs, endpoint_stats",
        [
            """
            CREATE TABLE IF NOT EXISTS api_keys (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                name           TEXT    UNIQUE NOT NULL,
                encrypted_key  TEXT    NOT NULL,
                rate_limit     INTEGER NOT NULL DEFAULT 60,
                created_at     TEXT    NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_keys_name ON api_keys (name)",

            """
            CREATE TABLE IF NOT EXISTS request_logs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ts           TEXT    NOT NULL,
                endpoint     TEXT    NOT NULL,
                method       TEXT    NOT NULL DEFAULT 'GET',
                status_code  INTEGER NOT NULL,
                ok           INTEGER NOT NULL DEFAULT 1,
                key_name     TEXT,
                response_ms  REAL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_logs_ts       ON request_logs (ts)",
            "CREATE INDEX IF NOT EXISTS idx_logs_endpoint ON request_logs (endpoint)",
            "CREATE INDEX IF NOT EXISTS idx_logs_ts_ep    ON request_logs (ts, endpoint)",

            """
            CREATE TABLE IF NOT EXISTS endpoint_stats (
                endpoint      TEXT    PRIMARY KEY,
                display_name  TEXT    NOT NULL,
                total         INTEGER NOT NULL DEFAULT 0,
                success       INTEGER NOT NULL DEFAULT 0,
                failed        INTEGER NOT NULL DEFAULT 0,
                last_called   TEXT,
                avg_ms_sum    REAL    NOT NULL DEFAULT 0.0,
                avg_ms_count  INTEGER NOT NULL DEFAULT 0
            )
            """,
        ],
    ),

    # ── v2: 3-category error tracking ─────────────────────────────────────── #
    # Splits the old boolean 'ok / failed' into three explicit categories:
    #   success / client_error / server_error
    (
        2,
        "3-category error tracking: client_errors + server_errors columns",
        [
            # Add category column to request_logs
            "ALTER TABLE request_logs ADD COLUMN category TEXT NOT NULL DEFAULT 'success'",

            # Back-fill category from status_code for any existing rows
            """
            UPDATE request_logs
            SET    category = CASE
                       WHEN status_code >= 500 THEN 'server_error'
                       WHEN status_code >= 400 THEN 'client_error'
                       ELSE 'success'
                   END
            WHERE  category = 'success'
            """,

            # Add split counters to endpoint_stats
            "ALTER TABLE endpoint_stats ADD COLUMN client_errors INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE endpoint_stats ADD COLUMN server_errors INTEGER NOT NULL DEFAULT 0",

            # Migrate old 'failed' into server_errors (best-effort; may already be 0)
            "UPDATE endpoint_stats SET server_errors = failed WHERE server_errors = 0 AND failed > 0",
        ],
    ),

    # ── add future migrations here ─────────────────────────────────────────── #
    # Example:
    # (
    #     3,
    #     "Add user_agent column to request_logs",
    #     [
    #         "ALTER TABLE request_logs ADD COLUMN user_agent TEXT",
    #     ],
    # ),

]


# ── DatabaseManager ───────────────────────────────────────────────────────── #

class DatabaseManager:
    """
    Single async SQLite connection for the whole FastAPI process.

    Call ``await db.connect()`` in lifespan startup and
    ``await db.close()``   in lifespan shutdown.
    """

    def __init__(self, db_path: Path) -> None:
        self._path  = Path(db_path)
        self._conn: Optional[aiosqlite.Connection] = None
        self._wlock = asyncio.Lock()   # serialise all writes

    # ── Lifecycle ─────────────────────────────────────────────────────────── #

    async def connect(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row

        # Performance pragmas (applied before migrations)
        await self._conn.execute("PRAGMA journal_mode = WAL")
        await self._conn.execute("PRAGMA synchronous  = NORMAL")
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.commit()

        await self._run_migrations()
        await self._seed_stats()
        logger.info("SQLite ready → %s  (schema v%d)", self._path, _MIGRATIONS[-1][0])

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None
            logger.info("SQLite connection closed.")

    # ── Migration runner ──────────────────────────────────────────────────── #

    async def _run_migrations(self) -> None:
        """
        Create the tracking table, then apply every migration that hasn't
        been recorded yet.  Safe to call on every startup.
        """
        # The tracking table itself is always created first.
        # It is never listed in _MIGRATIONS to avoid a chicken-and-egg problem.
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version     INTEGER PRIMARY KEY,
                description TEXT    NOT NULL,
                applied_at  TEXT    NOT NULL
            )
        """)
        await self._conn.commit()

        # Which versions are already applied?
        async with self._conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ) as cur:
            applied = {row[0] for row in await cur.fetchall()}

        if applied:
            logger.info("Schema migrations already applied: %s", sorted(applied))

        # Apply pending migrations in version order
        for version, description, statements in sorted(_MIGRATIONS, key=lambda m: m[0]):
            if version in applied:
                continue

            logger.info("Applying migration v%d: %s …", version, description)
            try:
                for sql in statements:
                    try:
                        await self._conn.execute(sql)
                    except aiosqlite.OperationalError as exc:
                        # "duplicate column name" → column already exists from a
                        # previous hand-patch; safe to skip this single statement.
                        if "duplicate column name" in str(exc).lower():
                            logger.debug(
                                "v%d: column already exists, skipping statement.", version
                            )
                        else:
                            raise

                # Record the migration as successfully applied
                await self._conn.execute(
                    """
                    INSERT INTO schema_migrations (version, description, applied_at)
                    VALUES (?, ?, ?)
                    """,
                    (version, description, datetime.utcnow().isoformat() + "Z"),
                )
                await self._conn.commit()
                logger.info("Migration v%d applied ✓", version)

            except Exception:
                await self._conn.rollback()
                logger.exception("Migration v%d FAILED – rolled back.", version)
                raise   # crash fast; the app should not start with a broken schema

    async def _seed_stats(self) -> None:
        """Ensure every tracked endpoint has a zero-row in endpoint_stats."""
        async with self._wlock:
            for ep, name in ENDPOINT_DISPLAY.items():
                await self._conn.execute(
                    """
                    INSERT OR IGNORE INTO endpoint_stats
                        (endpoint, display_name, total, success, client_errors, server_errors)
                    VALUES (?, ?, 0, 0, 0, 0)
                    """,
                    (ep, name),
                )
            await self._conn.commit()

    # ── Schema introspection (useful for debugging) ────────────────────────── #

    async def get_schema_version(self) -> int:
        """Return the highest migration version that has been applied."""
        async with self._conn.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ) as cur:
            row = await cur.fetchone()
        return row[0] or 0

    # ── API key operations ────────────────────────────────────────────────── #

    async def add_key(self, name: str, encrypted_key: str, rate_limit: int = 60) -> None:
        ts = datetime.utcnow().isoformat() + "Z"
        async with self._wlock:
            try:
                await self._conn.execute(
                    """
                    INSERT INTO api_keys (name, encrypted_key, rate_limit, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (name, encrypted_key, rate_limit, ts),
                )
                await self._conn.commit()
            except aiosqlite.IntegrityError:
                raise ValueError(f"A key named '{name}' already exists.")

    async def list_keys(self) -> List[Dict]:
        async with self._conn.execute(
            "SELECT name, rate_limit, created_at FROM api_keys ORDER BY created_at DESC"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def delete_key(self, name: str) -> bool:
        async with self._wlock:
            cur = await self._conn.execute(
                "DELETE FROM api_keys WHERE name = ?", (name,)
            )
            await self._conn.commit()
        return cur.rowcount > 0

    async def get_all_encrypted_keys(self) -> List[Dict]:
        async with self._conn.execute(
            "SELECT name, encrypted_key, rate_limit FROM api_keys"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # ── Request logging ────────────────────────────────────────────────────── #

    async def log_request(
        self,
        endpoint: str,
        method: str,
        status_code: int,
        key_name: Optional[str] = None,
        response_ms: Optional[float] = None,
    ) -> None:
        """
        Write one audit row and atomically update endpoint_stats.
        Only call this for requests that actually reached the scraper.
        Do NOT call for 401/429 middleware rejections.
        """
        canonical = _canonical(endpoint)
        cat       = _category(status_code)
        ts        = datetime.utcnow().isoformat() + "Z"

        async with self._wlock:
            await self._conn.execute(
                """
                INSERT INTO request_logs
                    (ts, endpoint, method, status_code, category, key_name, response_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (ts, canonical, method, status_code, cat, key_name, response_ms),
            )

            # Safety net: ensure stats row exists even for unexpected endpoints
            await self._conn.execute(
                """
                INSERT OR IGNORE INTO endpoint_stats
                    (endpoint, display_name, total, success, client_errors, server_errors)
                VALUES (?, ?, 0, 0, 0, 0)
                """,
                (canonical, ENDPOINT_DISPLAY.get(canonical, canonical)),
            )

            if cat == "success":
                await self._conn.execute(
                    """
                    UPDATE endpoint_stats
                    SET total        = total + 1,
                        success      = success + 1,
                        last_called  = ?,
                        avg_ms_sum   = avg_ms_sum   + COALESCE(?, 0),
                        avg_ms_count = avg_ms_count + CASE WHEN ? IS NOT NULL THEN 1 ELSE 0 END
                    WHERE endpoint = ?
                    """,
                    (ts, response_ms, response_ms, canonical),
                )
            elif cat == "client_error":
                await self._conn.execute(
                    """
                    UPDATE endpoint_stats
                    SET total         = total + 1,
                        client_errors = client_errors + 1,
                        last_called   = ?
                    WHERE endpoint = ?
                    """,
                    (ts, canonical),
                )
            else:  # server_error
                await self._conn.execute(
                    """
                    UPDATE endpoint_stats
                    SET total         = total + 1,
                        server_errors = server_errors + 1,
                        last_called   = ?
                    WHERE endpoint = ?
                    """,
                    (ts, canonical),
                )

            await self._conn.commit()

    # ── Stats queries ─────────────────────────────────────────────────────── #

    async def get_stats(self) -> List[Dict]:
        async with self._conn.execute(
            """
            SELECT
                endpoint,
                display_name                                        AS name,
                total,
                success,
                client_errors,
                server_errors,
                last_called,
                CASE
                    WHEN avg_ms_count > 0
                    THEN ROUND(avg_ms_sum / avg_ms_count, 1)
                    ELSE NULL
                END                                                 AS avg_ms
            FROM endpoint_stats
            ORDER BY total DESC
            """
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_total_today(self) -> int:
        today = datetime.utcnow().strftime("%Y-%m-%dT00:00:00Z")
        async with self._conn.execute(
            "SELECT COUNT(*) FROM request_logs WHERE ts >= ?", (today,)
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row else 0

    # ── Maintenance ───────────────────────────────────────────────────────── #

    async def cleanup_old_logs(self, retention_days: int = 3) -> int:
        """
        Delete request_logs rows older than retention_days.
        endpoint_stats counters are NEVER touched — they accumulate forever.
        """
        cutoff = (datetime.utcnow() - timedelta(days=retention_days)).isoformat() + "Z"
        async with self._wlock:
            cur = await self._conn.execute(
                "DELETE FROM request_logs WHERE ts < ?", (cutoff,)
            )
            await self._conn.commit()
        n = cur.rowcount
        if n:
            logger.info("Log cleanup: removed %d rows older than %d days.", n, retention_days)
        return n
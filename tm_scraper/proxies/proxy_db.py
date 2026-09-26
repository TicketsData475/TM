"""Read/claim access to the shared `proxies` pool in the infra Postgres.

Ported from axs-scraper-2 (app/database/proxies_db.py), but on psycopg2 (the TM
scraper's stack) instead of SQLAlchemy. The `proxies` table lives in a SEPARATE
task-manager DB (PROXIES_DB_*), not the TM data DB. A threaded pool + a
connection per call keeps it safe under the scraper's concurrent workers. The
table is owned by the infra DB — never created here.

Table columns used: proxy_id, proxy_host, proxy_port, proxy_user,
proxy_password, proxy_refresh_url, last_used, in_use.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager, suppress

import psycopg2
from psycopg2.pool import ThreadedConnectionPool

import config
from proxies import Proxy

logger = logging.getLogger("tm.proxies")

_COLS = "proxy_id, proxy_host, proxy_port, proxy_user, proxy_password, proxy_refresh_url"


def _row_to_proxy(row) -> Proxy:
    return Proxy(
        type="http",
        host=row[1],
        port=int(row[2]),
        username=row[3],
        password=row[4],
        refresh_url=row[5],
        proxy_id=row[0],
    )


class ProxyDB:
    """Thread-safe claim/release access to the proxies table in the infra DB."""

    def __init__(self) -> None:
        missing = [k for k in ("PROXIES_DB_HOST", "PROXIES_DB_NAME",
                               "PROXIES_DB_USER", "PROXIES_DB_PASSWORD")
                   if not getattr(config, k)]
        if missing:
            raise RuntimeError(f"Proxies DB not configured: missing {missing}")
        self._pool = ThreadedConnectionPool(
            1, 4,   # small: one browser claims/releases one proxy at a time
            host=config.PROXIES_DB_HOST, port=config.PROXIES_DB_PORT,
            dbname=config.PROXIES_DB_NAME, user=config.PROXIES_DB_USER,
            password=config.PROXIES_DB_PASSWORD,
        )
        logger.info("ProxyDB connected to %s:%s/%s",
                    config.PROXIES_DB_HOST, config.PROXIES_DB_PORT, config.PROXIES_DB_NAME)

    @contextmanager
    def _conn(self):
        conn = self._pool.getconn()
        try:
            yield conn
        finally:
            self._pool.putconn(conn)

    def get_proxy_by_proxy_id(self, proxy_id: int) -> Proxy | None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT {_COLS} FROM proxies WHERE proxy_id = %s", (proxy_id,))
            row = cur.fetchone()
            conn.rollback()
            if row is None:
                logger.warning("Proxy id %s not found in proxies DB", proxy_id)
                return None
            return _row_to_proxy(row)

    def claim_proxy(self, allowed_ids: list[int] | None = None) -> Proxy | None:
        """Atomically claim the least-recently-used free proxy (SKIP LOCKED)."""
        where = "in_use = false"
        params: tuple = ()
        if allowed_ids:
            where += " AND proxy_id = ANY(%s)"
            params = (allowed_ids,)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {_COLS} FROM proxies WHERE {where} "
                "ORDER BY last_used ASC NULLS FIRST FOR UPDATE SKIP LOCKED LIMIT 1",
                params,
            )
            row = cur.fetchone()
            if row is None:
                conn.rollback()
                return None
            cur.execute("UPDATE proxies SET in_use = true, last_used = now() "
                        "WHERE proxy_id = %s", (row[0],))
            conn.commit()
            return _row_to_proxy(row)

    def release_proxy(self, proxy_id: int) -> None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("UPDATE proxies SET in_use = false WHERE proxy_id = %s", (proxy_id,))
            conn.commit()

    def reclaim_stale(self, minutes: int = 15) -> int:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE proxies SET in_use = false "
                "WHERE in_use = true AND last_used < now() - (%s || ' minutes')::interval",
                (int(minutes),),
            )
            conn.commit()
            return cur.rowcount or 0

    def close(self) -> None:
        with suppress(Exception):
            self._pool.closeall()

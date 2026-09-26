"""ProxyProviders backed by the infra proxies DB (by id).

Two modes, mirroring axs-scraper-2:
  * ProxyDbClaimProvider   — claim/release the least-recently-used free proxy
                             among PROXY_DB_IDS (rotation + cross-process safe).
  * load_proxies_by_ids    — snapshot the given ids into Proxy objects for an
                             in-memory RoundRobinListProxyProvider (no claim).
"""
import logging

from proxies import Proxy, ProxyNotAvailable, ProxyProvider
from proxies.proxy_db import ProxyDB

logger = logging.getLogger("tm.proxies")


def load_proxies_by_ids(db: ProxyDB, ids: list[int]) -> list[Proxy]:
    proxies = []
    for pid in ids:
        proxy = db.get_proxy_by_proxy_id(pid)
        if proxy:
            proxies.append(proxy)
    if not proxies:
        raise ValueError(f"No proxies found in DB for ids {ids}")
    logger.info("Loaded %d proxies from the DB for ids %s", len(proxies), ids)
    return proxies


class ProxyDbClaimProvider(ProxyProvider):
    """next() atomically claims a free proxy; release() frees it again."""

    def __init__(self, db: ProxyDB, allowed_ids: list[int] | None = None):
        self._db = db
        self._allowed_ids = allowed_ids or None

    def next(self) -> Proxy:
        proxy = self._db.claim_proxy(self._allowed_ids)
        if proxy is None:
            raise ProxyNotAvailable("all proxies in the pool are in use")
        return proxy

    def release(self, proxy: Proxy) -> None:
        if proxy and proxy.proxy_id is not None:
            self._db.release_proxy(proxy.proxy_id)

"""Build the ProxyProvider from config.

Resolution order: proxies DB by id -> single proxy. Returns None when proxies
are disabled.
"""
import logging

import config
from proxies import Proxy, ProxyProvider
from proxies.round_robin import RoundRobinListProxyProvider
from proxies.single_source import SingleProxyProvider
from proxies.support import (
    CredsTemplateExpandingDecorator,
    ThreadSafeProxyProviderDecorator,
)

logger = logging.getLogger("tm.proxies")

_cached: ProxyProvider | None = None
_built = False


def get_proxy_provider() -> ProxyProvider | None:
    """Return a shared ProxyProvider, or None when proxies are disabled/unset."""
    global _cached, _built
    if _built:
        return _cached
    _built = True

    if not config.PROXY_ENABLED:
        _cached = None
        return None

    # 1) Proxies DB by id (preferred)
    if config.PROXIES_DB_HOST and config.PROXY_DB_IDS:
        from proxies.proxy_db import ProxyDB
        from proxies.db_source import ProxyDbClaimProvider, load_proxies_by_ids

        db = ProxyDB()
        # backstop: free any proxies a crashed run left marked in_use
        try:
            reclaimed = db.reclaim_stale(config.PROXY_DB_STALE_MINUTES)
            if reclaimed:
                logger.info("reclaimed %d stale proxy claims", reclaimed)
        except Exception as e:  # noqa: BLE001
            logger.warning("reclaim_stale failed: %s", e)

        if config.PROXY_DB_CLAIM_MODE:
            provider = ProxyDbClaimProvider(db, config.PROXY_DB_IDS)
            provider = ThreadSafeProxyProviderDecorator(provider)
        else:
            provider = RoundRobinListProxyProvider(load_proxies_by_ids(db, config.PROXY_DB_IDS))
            provider = CredsTemplateExpandingDecorator(provider)
            provider = ThreadSafeProxyProviderDecorator(provider)

    # 2) single proxy
    elif config.PROXY_HOST:
        provider = SingleProxyProvider(Proxy(
            type=config.PROXY_TYPE, host=config.PROXY_HOST, port=config.PROXY_PORT,
            username=config.PROXY_USERNAME, password=config.PROXY_PASSWORD,
        ))
        provider = CredsTemplateExpandingDecorator(provider)
    else:
        raise ValueError("PROXY_ENABLED=true but no proxy source configured "
                         "(PROXIES_DB_*+PROXY_DB_IDS, or PROXY_HOST)")

    _cached = provider
    return provider

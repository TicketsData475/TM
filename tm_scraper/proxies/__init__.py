"""Proxy provider system (ported from the axs-scraper-2 pattern).

A ProxyProvider yields Proxy objects via next(); sources (yaml/single) and
decorators (round-robin, thread-safe, creds-template) compose the same way.
Unlike axs (Redis round-robin for many pods), the TM scraper is single-process,
so round-robin is in-memory.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal, Optional


@dataclass
class Proxy:
    type: Literal["http", "https"]
    host: str
    port: int
    username: Optional[str]
    password: Optional[str]
    refresh_url: Optional[str] = None
    proxy_id: Optional[int] = None      # set when the proxy came from the proxies DB


class ProxyNotAvailable(Exception):
    """Raised when no proxy is available."""

    def __init__(self, message: str = "No proxy available"):
        super().__init__(message)


class ProxyProvider(ABC):
    @abstractmethod
    def next(self) -> Proxy:
        """Get the next proxy from the provider."""
        raise NotImplementedError()

    def release(self, proxy: Proxy) -> None:
        """Return a proxy to the pool. No-op for stateless providers; the DB
        claim provider overrides this to clear the `in_use` flag."""
        return None


def proxy_to_url(proxy: Proxy) -> str:
    """`http://user:pass@host:port` for curl_cffi / requests-style clients."""
    scheme = proxy.type or "http"
    if proxy.username:
        return f"{scheme}://{proxy.username}:{proxy.password}@{proxy.host}:{proxy.port}"
    return f"{scheme}://{proxy.host}:{proxy.port}"


def proxy_for_playwright(proxy: Proxy) -> dict:
    """Playwright's proxy dict: server without creds, creds as separate fields."""
    server = f"{proxy.type or 'http'}://{proxy.host}:{proxy.port}"
    out = {"server": server}
    if proxy.username:
        out["username"] = proxy.username
        out["password"] = proxy.password or ""
    return out

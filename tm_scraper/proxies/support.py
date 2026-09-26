"""Composable ProxyProvider decorators (ported from axs)."""
import threading

from proxies import Proxy, ProxyNotAvailable, ProxyProvider
from proxies.templater import expand_template


class FallbackProxyProvider(ProxyProvider):
    """Use `primary`; if it's exhausted (ProxyNotAvailable), use `fallback`."""

    def __init__(self, primary: ProxyProvider, fallback: ProxyProvider):
        assert primary is not None and fallback is not None
        self._primary = primary
        self._fallback = fallback

    def next(self) -> Proxy:
        try:
            return self._primary.next()
        except ProxyNotAvailable:
            return self._fallback.next()


class ThreadSafeProxyProviderDecorator(ProxyProvider):
    """Serialise next() so concurrent callers don't race the underlying provider."""

    def __init__(self, delegate: ProxyProvider):
        self._lock = threading.Lock()
        self._delegate = delegate

    def next(self) -> Proxy:
        with self._lock:
            return self._delegate.next()

    def release(self, proxy: Proxy) -> None:
        with self._lock:
            self._delegate.release(proxy)


class CredsTemplateExpandingDecorator(ProxyProvider):
    """Expand ${random_...} placeholders in the username on each next()."""

    def __init__(self, delegate: ProxyProvider):
        self._delegate = delegate

    def next(self) -> Proxy:
        p = self._delegate.next()
        return Proxy(
            type=p.type, host=p.host, port=p.port,
            username=expand_template(p.username) if p.username else p.username,
            password=p.password, refresh_url=p.refresh_url, proxy_id=p.proxy_id,
        )

    def release(self, proxy: Proxy) -> None:
        self._delegate.release(proxy)

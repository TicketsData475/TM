"""A provider that always returns the same single proxy (ported from axs)."""
from proxies import Proxy, ProxyProvider


class SingleProxyProvider(ProxyProvider):
    def __init__(self, proxy: Proxy):
        if not isinstance(proxy, Proxy):
            raise ValueError(f"Expected a Proxy, got {type(proxy)}")
        self._proxy = proxy

    def next(self) -> Proxy:
        return self._proxy

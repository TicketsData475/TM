"""In-memory round-robin over a proxy list.

The axs version rotates through Redis so many pods share one pool. The TM
scraper is a single process, so a simple in-memory cycle is the faithful
equivalent (same ProxyProvider.next() interface).
"""
import random
from itertools import cycle

from proxies import Proxy, ProxyNotAvailable, ProxyProvider


class RoundRobinListProxyProvider(ProxyProvider):
    def __init__(self, proxies: list[Proxy], shuffle: bool = True):
        self._proxies = list(proxies or [])
        if shuffle:
            random.shuffle(self._proxies)
        self._cycle = cycle(self._proxies) if self._proxies else None

    def next(self) -> Proxy:
        if not self._cycle:
            raise ProxyNotAvailable()
        return next(self._cycle)

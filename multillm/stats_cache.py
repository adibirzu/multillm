"""Small TTL cache for expensive local stats scans."""

from __future__ import annotations

import copy
import time
from collections.abc import Callable
from functools import wraps
from threading import RLock
from typing import TypeVar

T = TypeVar("T")


def ttl_cache(seconds: float = 15.0, maxsize: int = 64):
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        cache: dict[tuple, tuple[float, T]] = {}
        lock = RLock()

        @wraps(func)
        def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            now = time.monotonic()
            with lock:
                entry = cache.get(key)
                if entry and now - entry[0] < seconds:
                    return copy.deepcopy(entry[1])

            value = func(*args, **kwargs)
            with lock:
                if len(cache) >= maxsize:
                    oldest_key = min(cache, key=lambda item: cache[item][0])
                    cache.pop(oldest_key, None)
                cache[key] = (time.monotonic(), copy.deepcopy(value))
            return value

        def cache_clear() -> None:
            with lock:
                cache.clear()

        wrapper.cache_clear = cache_clear  # type: ignore[attr-defined]
        return wrapper

    return decorator

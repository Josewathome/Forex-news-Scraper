
import time
import hashlib
import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class CacheEntry:
    __slots__ = ("data", "ts")

    def __init__(self, data: Any, ts: float) -> None:
        self.data = data
        self.ts   = ts


class CacheManager:
    """
    Thread-safe, in-memory TTL cache.
    Keys are stable hashes of (sorted currencies, date / date-range).
    """

    def __init__(self) -> None:
        self._store: dict[str, CacheEntry] = {}

    # ------------------------------------------------------------------ #

    @staticmethod
    def make_key(*parts: Any) -> str:
        raw = json.dumps(parts, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def get(self, key: str, ttl: float) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None:
            return None
        age = time.monotonic() - entry.ts
        if age > ttl:
            logger.debug("Cache MISS (expired) key=%s age=%.1fs", key, age)
            del self._store[key]
            return None
        logger.debug("Cache HIT key=%s age=%.1fs", key, age)
        return entry.data

    def set(self, key: str, data: Any) -> None:
        self._store[key] = CacheEntry(data, time.monotonic())
        logger.debug("Cache SET key=%s total_entries=%d", key, len(self._store))

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)

    def purge_expired(self, ttl: float) -> int:
        now   = time.monotonic()
        stale = [k for k, v in self._store.items() if (now - v.ts) > ttl]
        for k in stale:
            del self._store[k]
        return len(stale)
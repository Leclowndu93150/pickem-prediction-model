"""
Disk-backed JSON cache for the HLTV mobile API.

Keyed by ``(method, sorted_params_tuple)``. Each entry is stamped with a
timestamp; on read, entries older than the per-endpoint TTL are ignored
and re-fetched.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any


def _default_cache_dir() -> Path:
    override = os.environ.get("HLTV_CACHE_DIR")
    if override:
        return Path(override)
    return Path.home() / ".cache" / "hltv"


DEFAULT_CACHE_DIR = _default_cache_dir()

# Per-path TTL in seconds. Match against the API path; first prefix hit wins.
# Use 0 to bypass cache, -1 for "forever".
TTL_RULES: list[tuple[str, int]] = [
    # live / volatile feeds
    ("FrontpageV2", 60),
    ("MatchesV4", 60),
    ("matches/subscriptions", 30),
    ("matches/", 60),
    # match: caller decides finished-vs-live via the helper
    ("MatchScreen", 60),
    # event detail - refresh hourly while live, but most events are done
    ("EventDetails2", 3600),
    ("EventsData2", 600),
    ("FinishedEventsData", 86400),
    ("event/stats", 3600),
    # team / player / ranking
    ("TeamScreen", 3600),
    ("PlayerScreen", 3600),
    ("PlayerCompare", 3600),
    ("v2/ranking", 86400),
    # search
    ("v2/search", 300),
    ("search/", 300),
    ("searchEmptyState", 86400),
    # bootstrap
    ("startupCheck", 3600),
    ("Onboarding", 86400),
    # ads, fans
    ("ads", 300),
    ("topPlayersByFanCount", 3600),
    ("topTeamsByFanCount", 3600),
    # forum
    ("ForumContent", 60),
    ("ForumThread", 60),
    ("ForumCreateTopicData", 86400),
    # articles - once published, content rarely changes
    ("articleScreen", 86400),
]


def _ttl_for(path: str) -> int:
    p = path.lstrip("/")
    for prefix, ttl in TTL_RULES:
        if p.startswith(prefix.lstrip("/")):
            return ttl
    return 300  # conservative default


def _stable_key(method: str, path: str, params: dict | None) -> str:
    """Build a deterministic cache key from a request."""
    norm = (method.upper(), path.lstrip("/"), tuple(sorted((params or {}).items())))
    raw = json.dumps(norm, sort_keys=True, default=str)
    digest = hashlib.sha1(raw.encode()).hexdigest()[:16]
    safe_path = norm[1].replace("/", "_").replace("?", "_")[:60] or "_"
    return f"{norm[0]}_{safe_path}_{digest}"


class DiskCache:
    """
    Thread-safe JSON file cache. One file per request.

    Parameters
    ----------
    base_dir : str or Path, optional
        Cache root. Defaults to ``~/.cache/hltv``.
    enabled : bool, default ``True``
        Set False to bypass all reads/writes (e.g. for debugging).
    """

    def __init__(
        self,
        base_dir: str | Path | None = None,
        enabled: bool = True,
    ) -> None:
        self.base = Path(base_dir or _default_cache_dir())
        self.enabled = enabled
        self.base.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._writes = 0

    @property
    def stats(self) -> dict[str, int]:
        """Cumulative hit/miss/write counters."""
        return {"hits": self._hits, "misses": self._misses, "writes": self._writes}

    def _path_for(self, key: str) -> Path:
        return self.base / f"{key}.json"

    def get(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        ttl: int | None = None,
    ) -> Any:
        """
        Return cached value or None.

        Parameters
        ----------
        method, path, params : request identity
        ttl : int, optional
            Override the per-path TTL. ``-1`` means forever.
        """
        if not self.enabled:
            return None
        key = _stable_key(method, path, params)
        f = self._path_for(key)
        if not f.exists():
            self._misses += 1
            return None
        try:
            with self._lock:
                with open(f) as fh:
                    blob = json.load(fh)
        except Exception:
            self._misses += 1
            return None
        ts = blob.get("_ts", 0)
        actual_ttl = ttl if ttl is not None else _ttl_for(path)
        if actual_ttl == 0:
            self._misses += 1
            return None
        if actual_ttl != -1 and (time.time() - ts) > actual_ttl:
            self._misses += 1
            return None
        self._hits += 1
        return blob.get("value")

    def set(
        self,
        method: str,
        path: str,
        params: dict | None,
        value: Any,
    ) -> None:
        """Write a successful response to the cache."""
        if not self.enabled:
            return
        key = _stable_key(method, path, params)
        f = self._path_for(key)
        blob = {
            "_ts": time.time(),
            "_method": method,
            "_path": path,
            "_params": params,
            "value": value,
        }
        try:
            with self._lock:
                tmp = f.with_suffix(".tmp")
                with open(tmp, "w") as fh:
                    json.dump(blob, fh)
                os.replace(tmp, f)
            self._writes += 1
        except Exception:
            pass

    def clear(self, prefix: str | None = None) -> int:
        """
        Delete cached entries. Returns the count removed.

        Parameters
        ----------
        prefix : str, optional
            Only clear entries whose path starts with this prefix.
        """
        removed = 0
        for f in self.base.glob("*.json"):
            if prefix is None:
                f.unlink()
                removed += 1
                continue
            try:
                with open(f) as fh:
                    blob = json.load(fh)
                if blob.get("_path", "").lstrip("/").startswith(
                    prefix.lstrip("/")
                ):
                    f.unlink()
                    removed += 1
            except Exception:
                pass
        return removed

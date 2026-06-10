"""Thread-safe rotating HTTP proxy pool."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from urllib.parse import quote


class ProxyPool:
    def __init__(self, proxies: list[str], cooldown_seconds: float = 60.0) -> None:
        if not proxies:
            raise ValueError("proxy pool cannot be empty")
        self._proxies = proxies
        self._cooldown_seconds = cooldown_seconds
        self._failed_until: dict[str, float] = {}
        self._index = 0
        self._lock = threading.Lock()
        self._uses = 0
        self._failures = 0

    @classmethod
    def from_file(cls, path: str | Path) -> "ProxyPool":
        entries: list[str] = []
        with Path(path).open(encoding="utf-8") as fh:
            for raw in fh:
                value = raw.strip()
                if not value or value.startswith("#"):
                    continue
                entries.append(_normalize_proxy(value))
        return cls(list(dict.fromkeys(entries)))

    def acquire(self) -> str:
        now = time.monotonic()
        with self._lock:
            count = len(self._proxies)
            fallback: str | None = None
            fallback_until = float("inf")
            for _ in range(count):
                proxy = self._proxies[self._index % count]
                self._index += 1
                failed_until = self._failed_until.get(proxy, 0.0)
                if failed_until <= now:
                    self._uses += 1
                    return proxy
                if failed_until < fallback_until:
                    fallback = proxy
                    fallback_until = failed_until
            self._uses += 1
            return fallback or self._proxies[0]

    def mark_failed(self, proxy: str) -> None:
        with self._lock:
            self._failed_until[proxy] = time.monotonic() + self._cooldown_seconds
            self._failures += 1

    @property
    def size(self) -> int:
        return len(self._proxies)

    @property
    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "size": len(self._proxies),
                "uses": self._uses,
                "failures": self._failures,
                "cooling_down": sum(
                    until > time.monotonic()
                    for until in self._failed_until.values()
                ),
            }


def _normalize_proxy(value: str) -> str:
    if "://" in value:
        return value
    if "@" in value:
        return f"http://{value}"
    parts = value.split(":")
    if len(parts) == 4:
        host, port, username, password = parts
        return (
            f"http://{quote(username, safe='')}:{quote(password, safe='')}"
            f"@{host}:{port}"
        )
    if len(parts) == 2:
        return f"http://{value}"
    raise ValueError("unsupported proxy format")

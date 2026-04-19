"""Simple in-memory per-IP rate limiter."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque, DefaultDict, Optional


@dataclass
class LimitResult:
    limited: bool
    retry_after_seconds: int = 0


class InMemoryRateLimiter:
    """Sliding-window rate limiter for lightweight API protection."""

    def __init__(self, max_requests: int, window_seconds: int) -> None:
        if max_requests <= 0:
            raise ValueError("max_requests must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._hits: DefaultDict[str, Deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check(self, key: str) -> LimitResult:
        now = time.monotonic()
        async with self._lock:
            window = self._hits[key]
            cutoff = now - self._window_seconds
            while window and window[0] <= cutoff:
                window.popleft()

            if len(window) >= self._max_requests:
                retry_after = max(1, int(self._window_seconds - (now - window[0])))
                return LimitResult(limited=True, retry_after_seconds=retry_after)

            window.append(now)
            return LimitResult(limited=False)

    async def reset(self) -> None:
        async with self._lock:
            self._hits.clear()

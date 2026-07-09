"""
src/utils/rate_limiter.py
─────────────────────────
Thread-safe sliding-window rate limiter.

Enforces N calls per T seconds across any number of concurrent threads
without starving fast threads or over-blocking slow ones.

Algorithm:
  Maintain a deque of call timestamps, protected by a Lock.
  On acquire():
    1. Evict timestamps older than (now - window_seconds).
    2. If deque length < max_calls → append current timestamp and return.
    3. Else → compute minimum wait time until the oldest timestamp expires,
       release the lock, sleep, and retry.

Complexity: O(1) amortized per acquire() after eviction.

Usage:
    limiter = SlidingWindowRateLimiter(max_calls=2, window_seconds=1.0)

    # explicit
    limiter.acquire()
    api_call()

    # as decorator
    @limiter
    def api_call(): ...
"""

from __future__ import annotations

import functools
import threading
import time
from collections import deque
from typing import Callable, TypeVar

F = TypeVar("F", bound=Callable)


class SlidingWindowRateLimiter:
    """
    Sliding-window rate limiter safe for concurrent ThreadPoolExecutor use.

    Parameters
    ----------
    max_calls : int
        Maximum number of calls allowed within `window_seconds`.
    window_seconds : float
        Duration of the sliding window.
    """

    def __init__(self, max_calls: int, window_seconds: float) -> None:
        if max_calls < 1:
            raise ValueError("max_calls must be ≥ 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")

        self._max_calls  = max_calls
        self._window     = window_seconds
        self._timestamps: deque[float] = deque()
        self._lock       = threading.Lock()

    def acquire(self) -> None:
        """Block the calling thread until a call slot is available, then claim it."""
        while True:
            with self._lock:
                now    = time.monotonic()
                cutoff = now - self._window

                # Evict expired timestamps (older than the window)
                while self._timestamps and self._timestamps[0] <= cutoff:
                    self._timestamps.popleft()

                if len(self._timestamps) < self._max_calls:
                    self._timestamps.append(now)
                    return  # slot claimed — caller may proceed

                # Window full: compute precise minimum wait to avoid busy-spin
                wait = max(0.0, self._timestamps[0] - cutoff + 1e-4)   # tiny epsilon for float safety

            # Release the lock before sleeping so other threads can evict/check
            time.sleep(wait)

    def __call__(self, func: F) -> F:
        """
        Decorator form.

            @rate_limiter
            def call_api(...): ...
        """
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            self.acquire()
            return func(*args, **kwargs)
        return wrapper  # type: ignore[return-value]

    @property
    def current_count(self) -> int:
        """Number of calls currently inside the window (diagnostic)."""
        now = time.monotonic()
        with self._lock:
            return sum(1 for t in self._timestamps if t > now - self._window)

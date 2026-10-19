"""In-memory sliding-window rate limit.

The window lives in this process. Cloud Run runs more than one instance, so
the same IP can be under the limit on each instance at the same time. This is
not a global limit.

Keys and timestamps are capped so a scan of random IPs cannot grow memory
without bound. The lock makes allow() safe to call from worker threads.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque


class RateLimiter:
    def __init__(self, *, per_minute: int, per_hour: int, max_keys: int) -> None:
        self.per_minute = max(1, per_minute)
        self.per_hour = max(self.per_minute, per_hour)
        self.max_keys = max(1, max_keys)
        self._lock = threading.Lock()
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._hits)

    def allow(self, key: str, *, now: float | None = None) -> bool:
        moment = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                while len(self._hits) >= self.max_keys:
                    self._hits.popitem(last=False)
                hits = deque()
                self._hits[key] = hits
            else:
                self._hits.move_to_end(key)

            hour_cutoff = moment - 3600
            while hits and hits[0] < hour_cutoff:
                hits.popleft()

            in_minute = sum(1 for stamp in hits if stamp >= moment - 60)
            if in_minute >= self.per_minute or len(hits) >= self.per_hour:
                return False
            hits.append(moment)
            return True

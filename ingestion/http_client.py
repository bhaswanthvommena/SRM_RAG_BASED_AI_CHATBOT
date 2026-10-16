from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ingestion.config import Settings


class RateLimiter:
    """Simple token-bucket-ish rate limiter for polite crawling."""

    def __init__(self, rps: float) -> None:
        self._interval = 1.0 / max(rps, 0.1)
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._interval - (now - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


class CrawlClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.limiter = RateLimiter(settings.crawl_rps)
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> CrawlClient:
        self._client = httpx.AsyncClient(
            headers={
                "User-Agent": self.settings.crawl_user_agent,
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
            },
            follow_redirects=True,
            timeout=httpx.Timeout(60.0, connect=20.0),
            http2=True,
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("CrawlClient is not started; use async with")
        return self._client

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        await self.limiter.acquire()
        resp = await self.client.get(url, headers=headers, params=params)
        # Soft-retry on 429 / 503
        if resp.status_code in (429, 503):
            retry_after = float(resp.headers.get("Retry-After", "5"))
            await asyncio.sleep(min(retry_after, 30))
            await self.limiter.acquire()
            resp = await self.client.get(url, headers=headers, params=params)
        resp.raise_for_status()
        return resp

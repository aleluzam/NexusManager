"""Rate limiting: ventana deslizante en memoria por IP + ruta.

Suficiente para despliegues single-process; con varios workers/instancias
debe sustituirse por un backend compartido (Redis).
"""

import time
from collections import defaultdict, deque
from typing import Awaitable, Callable

from fastapi import HTTPException, Request, status

from app.config import Settings


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._last_cleanup = time.monotonic()
        self._cleanup_interval = 300.0

    def _prune(self, now: float) -> None:
        if now - self._last_cleanup < self._cleanup_interval:
            return
        self._last_cleanup = now
        for key in list(self._buckets):
            bucket = self._buckets[key]
            if not bucket or now - bucket[-1] > 3600 * 24:
                del self._buckets[key]

    def allow(self, key: str, max_requests: int, window_seconds: int) -> bool:
        now = time.monotonic()
        self._prune(now)
        bucket = self._buckets[key]
        while bucket and bucket[0] <= now - window_seconds:
            bucket.popleft()
        if len(bucket) >= max_requests:
            return False
        bucket.append(now)
        return True


def make_rate_limit_dependency(route: str) -> Callable[..., Awaitable[None]]:
    """Factory de dependencia que limita 'route' por IP usando la
    configuración de la aplicación (request.app.state.settings)."""

    async def limit_by_ip(request: Request) -> None:
        settings: Settings = request.app.state.settings
        limits = {
            "login": (settings.rate_limit_login_max, settings.rate_limit_login_window),
            "register": (
                settings.rate_limit_register_max,
                settings.rate_limit_register_window,
            ),
            "refresh": (
                settings.rate_limit_refresh_max,
                settings.rate_limit_refresh_window,
            ),
            "logout": (
                settings.rate_limit_logout_max,
                settings.rate_limit_logout_window,
            ),
            "forgot-password": (
                settings.rate_limit_forgot_max,
                settings.rate_limit_forgot_window,
            ),
            "reset-password": (
                settings.rate_limit_reset_max,
                settings.rate_limit_reset_window,
            ),
        }
        max_requests, window = limits[route]
        key = f"{request.client.host}:{request.url.path}"
        limiter: InMemoryRateLimiter = request.app.state.rate_limiter
        if not limiter.allow(key, max_requests, window):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Demasiados intentos. Espera un momento e inténtalo de nuevo.",
                headers={"Retry-After": str(window)},
            )

    return limit_by_ip
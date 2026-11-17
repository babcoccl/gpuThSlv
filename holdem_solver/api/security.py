"""API-key check, browser session cookies + CSRF, rate limiting, body limit."""

from __future__ import annotations

import hashlib
import hmac
import math
import threading
import time
from typing import Any, Awaitable, Callable

from fastapi import HTTPException, Request

DEFAULT_MAX_BODY_BYTES = 64 * 1024
DEFAULT_POST_RATE_PER_MINUTE = 120.0
DEFAULT_POST_BURST = 30
DEFAULT_SESSION_SECONDS = 12 * 3600
DEFAULT_LOGIN_RATE_PER_MINUTE = 10.0
DEFAULT_LOGIN_BURST = 5
SESSION_COOKIE = "holdem_session"
CSRF_COOKIE = "holdem_csrf"
CSRF_HEADER = "x-csrf-token"
_MUTATING = ("POST", "PUT", "PATCH", "DELETE")


def presented_key(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    token = header[len("Bearer "):] if header.startswith("Bearer ") else None
    return token or request.headers.get("x-api-key")


def key_matches(presented: str | None, expected: str) -> bool:
    """Constant-time comparison (no early exit on the first differing byte)."""
    if presented is None:
        return False
    return hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))


class SessionSigner:
    """Stateless session cookies: ``v1.<expiry_unix>.<hmac>`` signed with the
    session secret (``HOLDEM_SESSION_SECRET``, else derived from the API key)."""

    def __init__(self, api_key: str, *, secret: str | None = None,
                 ttl_seconds: int = DEFAULT_SESSION_SECONDS) -> None:
        material = secret or hashlib.sha256(("holdem-session:" + api_key).encode()).hexdigest()
        self._key = material.encode("utf-8")
        self.ttl_seconds = int(ttl_seconds)

    def issue(self, now: float | None = None) -> tuple[str, float]:
        exp = (now or time.time()) + self.ttl_seconds
        exp_i = int(exp)
        body = f"v1.{exp_i}"
        sig = hmac.new(self._key, body.encode(), hashlib.sha256).hexdigest()
        return f"{body}.{sig}", float(exp_i)

    def verify(self, token: str | None, now: float | None = None) -> bool:
        if not token:
            return False
        body, _, sig = token.rpartition(".")
        if not body.startswith("v1.") or not sig:
            return False
        try:
            exp = int(body.split(".", 1)[1])
        except ValueError:
            return False
        if exp < (now or time.time()):
            return False
        expected = hmac.new(self._key, body.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)


def csrf_token() -> str:
    import secrets

    return secrets.token_urlsafe(24)


def auth_dependency(
    api_key: str | None, signer: SessionSigner | None = None
) -> Callable[[Request], Awaitable[None]]:
    """Accepts the API key (``request.state.auth = 'key'``) or a valid session
    cookie (``'cookie'``). Cookie-authenticated mutating requests must also
    present the CSRF token (checked by ``csrf_dependency``)."""

    async def require(request: Request) -> None:
        if api_key is None:
            request.state.auth = "none"
            return
        if key_matches(presented_key(request), api_key):
            request.state.auth = "key"
            return
        if signer is not None and signer.verify(request.cookies.get(SESSION_COOKIE)):
            request.state.auth = "cookie"
            return
        raise HTTPException(
            status_code=401, detail="invalid or missing API key", headers={"WWW-Authenticate": "Bearer"}
        )

    return require


def csrf_dependency(request: Request) -> None:
    """Double-submit CSRF check on mutating cookie-authenticated requests."""
    if request.method not in _MUTATING:
        return
    if getattr(request.state, "auth", None) != "cookie":
        return
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get(CSRF_HEADER)
    if not cookie or not header or not hmac.compare_digest(cookie, header):
        raise HTTPException(status_code=403, detail="missing or mismatched CSRF token")


class RateLimiter:
    """Token bucket per client: ``burst`` requests at once, refilled at
    ``rate_per_minute``. The client is the presented API key (hashed) or,
    without one, the peer address; behind a reverse proxy every client
    shares the proxy's address, so limit there too."""

    def __init__(self, rate_per_minute: float, burst: int, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.rate = rate_per_minute / 60.0
        self.burst = max(1, int(burst))
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def take(self, client: str) -> float:
        """0 if allowed, else the seconds until the next token."""
        now = self._clock()
        with self._lock:
            tokens, last = self._buckets.get(client, (float(self.burst), now))
            tokens = min(float(self.burst), tokens + (now - last) * self.rate)
            if tokens >= 1.0:
                self._buckets[client] = (tokens - 1.0, now)
                if len(self._buckets) > 10000:
                    self._prune(now)
                return 0.0
            self._buckets[client] = (tokens, now)
            return (1.0 - tokens) / self.rate if self.rate > 0 else 60.0

    def _prune(self, now: float) -> None:
        full = self.burst / self.rate if self.rate > 0 else 0
        for key, (_, last) in list(self._buckets.items()):
            if now - last > full:
                del self._buckets[key]


def client_id(request: Request) -> str:
    key = presented_key(request)
    if key:
        return "key:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return "ip:" + (request.client.host if request.client else "unknown")


def rate_limit_dependency(
    limiter: RateLimiter | None, *, ip_only: bool = False
) -> Callable[[Request], Awaitable[None]]:
    async def limit(request: Request) -> None:
        if limiter is None:
            return
        ident = (
            "ip:" + (request.client.host if request.client else "unknown")
            if ip_only else client_id(request)
        )
        wait = limiter.take(ident)
        if wait > 0:
            raise HTTPException(
                status_code=429,
                detail="rate limit exceeded: %g POSTs/minute (burst %d)" % (limiter.rate * 60, limiter.burst),
                headers={"Retry-After": str(max(1, math.ceil(wait)))},
            )

    return limit


class BodySizeLimit:
    """ASGI middleware: 413 for bodies over ``max_bytes``, from Content-Length
    up front or by counting chunks of a streamed body."""

    def __init__(self, app: Any, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = int(max_bytes)

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http" or self.max_bytes <= 0:
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    too_big = int(value) > self.max_bytes
                except ValueError:
                    too_big = False
                if too_big:
                    await self._reject(send)
                    return
        seen = 0
        sent = False

        async def counting_receive() -> dict:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    raise _TooLarge()
            return message

        async def tracking_send(message: dict) -> None:
            nonlocal sent
            if message["type"] == "http.response.start":
                sent = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _TooLarge:
            if not sent:
                await self._reject(send)

    async def _reject(self, send: Callable) -> None:
        body = ('{"detail":"request body over %d bytes"}' % self.max_bytes).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


class _TooLarge(HTTPException):
    # An HTTPException so FastAPI's body parser re-raises it (as 413) rather
    # than wrapping it in a generic 400.
    def __init__(self) -> None:
        super().__init__(status_code=413, detail="request body too large")

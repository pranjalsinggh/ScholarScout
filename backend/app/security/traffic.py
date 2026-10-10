"""Bounded request bodies and fair admission before multipart parsing.

Run one application process per anonymous ephemeral deployment. The reverse
proxy must supply the client address through Uvicorn's trusted proxy handling;
this module never trusts a client-supplied forwarding header.
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import os
import re
import threading
import time
from collections import Counter

from starlette.responses import JSONResponse

from app.config import settings


class BodyLimitExceeded(Exception):
    pass


class RequestBudgetMiddleware:
    def __init__(self, app):
        self.app = app
        self._lock = threading.Lock()
        self._owners: Counter = Counter()
        self._ips: Counter = Counter()
        self._total = 0
        self._ingestions = 0
        self._special: Counter = Counter()
        self._seen_tokens: dict[str, float] = {}
        self._new_sessions: dict[str, list[float]] = {}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        if settings.production and settings.trust_render_edge and os.getenv("RENDER_EXTERNAL_HOSTNAME"):
            # Cloudflare overwrites this edge header. X-Forwarded-For can retain
            # client-supplied values, even though the backend peer is a proxy.
            # This trust applies only to the isolated Render deployment profile.
            try:
                client_ip = str(ipaddress.ip_address(headers.get(b"cf-connecting-ip", b"").decode("ascii")))
            except (ValueError, UnicodeDecodeError):
                client_ip = "unverified-render-edge"
            scope = dict(scope, client=(client_ip, 0))
        token = headers.get(b"x-owner-token", b"")[:130]
        valid_token = bool(re.fullmatch(rb"ss_[A-Za-z0-9_-]{43}", token))
        owner = hashlib.sha256(token).hexdigest() if token else "anonymous"
        ip = (scope.get("client") or ("unknown", 0))[0]
        path = scope.get("path", "")
        ingestion = path in {"/api/papers/upload", "/api/papers/demo", "/api/papers/ingest"}
        # Erasure and readiness remain available even when ordinary work is full.
        admitted = path not in {"/api/me/purge", "/api/health"}
        special_key = "health" if path == "/api/health" else "purge"
        if not admitted:
            with self._lock:
                full = self._special[special_key] >= (8 if special_key == "health" else 4)
                if not full:
                    self._special[special_key] += 1
            if full:
                return await self._reject(scope, receive, send, 429, "Please retry shortly.")
        if admitted:
            with self._lock:
                now = time.monotonic()
                session_full = False
                if settings.production and valid_token and owner not in self._seen_tokens:
                    recent = [t for t in self._new_sessions.get(ip, []) if now - t < 3600]
                    session_full = len(recent) >= settings.max_new_sessions_per_ip_hour
                    if not session_full:
                        recent.append(now)
                        self._new_sessions[ip] = recent
                        self._seen_tokens[owner] = now
                    if len(self._seen_tokens) > 2000:
                        self._seen_tokens = {key: stamp for key, stamp in self._seen_tokens.items() if now - stamp < 3600}
                        self._new_sessions = {key: [stamp for stamp in stamps if now - stamp < 3600] for key, stamps in self._new_sessions.items() if any(now - stamp < 3600 for stamp in stamps)}
                full = (self._total >= settings.max_concurrent_requests
                        or (ingestion and self._ingestions >= settings.max_concurrent_ingestions)
                        or session_full
                        or self._owners[owner] >= settings.max_concurrent_requests_per_owner
                        or self._ips[ip] >= max(2, settings.max_concurrent_requests // 2))
                if not full:
                    self._total += 1
                    self._owners[owner] += 1
                    self._ips[ip] += 1
                    if ingestion:
                        self._ingestions += 1
            if full:
                return await self._reject(scope, receive, send, 429,
                                          "This session or service is busy. Please retry shortly.")
        limit = settings.max_request_mb * 1024 * 1024 if path == "/api/papers/upload" else 65536
        received = 0
        started = False
        body_complete = scope.get("method", "GET") not in {"POST", "PUT", "PATCH"}

        async def limited_receive():
            nonlocal received, body_complete
            try:
                message = await receive() if body_complete else await asyncio.wait_for(receive(), timeout=30)
            except asyncio.TimeoutError as exc:
                raise BodyLimitExceeded("Upload timed out. Please try again.") from exc
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise BodyLimitExceeded("Request exceeds this service's size limit.")
                if not message.get("more_body", False):
                    body_complete = True
            return message

        async def tracked_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            declared = headers.get(b"content-length")
            if declared is not None:
                try:
                    length = int(declared)
                except ValueError:
                    return await self._reject(scope, receive, send, 400, "Invalid request length.")
                if length < 0 or length > limit:
                    return await self._reject(scope, receive, send, 413,
                                              "Request exceeds this service's size limit.")
            await self.app(scope, limited_receive, tracked_send)
        except BodyLimitExceeded as exc:
            if not started:
                await self._reject(scope, receive, send, 413, str(exc))
        finally:
            if not admitted:
                with self._lock:
                    self._special[special_key] -= 1
            if admitted:
                with self._lock:
                    self._total -= 1
                    if ingestion:
                        self._ingestions -= 1
                    for counts, key in ((self._owners, owner), (self._ips, ip)):
                        counts[key] -= 1
                        if counts[key] <= 0:
                            del counts[key]

    @staticmethod
    async def _reject(scope, receive, send, status, detail):
        response = JSONResponse({"detail": detail}, status_code=status,
                                headers={"Cache-Control": "no-store",
                                         "X-Content-Type-Options": "nosniff",
                                         "Retry-After": "5"} if status == 429 else
                                {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
        await response(scope, receive, send)

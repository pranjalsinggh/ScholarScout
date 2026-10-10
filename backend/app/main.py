"""ScholarScout FastAPI application entry point."""
from __future__ import annotations

from pathlib import Path
import json
import logging
import os
import re
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import Match
from starlette.concurrency import run_in_threadpool

from app.api.routes import router
from app.config import settings
from app.database.database import Base, engine

log = logging.getLogger("scholarscout.startup")
request_log = logging.getLogger("scholarscout.requests")


class SPAStaticFiles(StaticFiles):
    """Serve index.html for React routes while missing assets remain 404s."""

    async def get_response(self, path: str, scope):
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or path.startswith("api/") or Path(path).suffix:
                raise
            return await super().get_response("index.html", scope)
        if response.status_code == 404 and not path.startswith("api/") and not Path(path).suffix:
            return await super().get_response("index.html", scope)
        return response


def create_app() -> FastAPI:
    Base.metadata.create_all(bind=engine)

    app = FastAPI(
        title=settings.app_name,
        description="AI-powered research intelligence platform - don't just read the paper, "
                    "explore everything around it.",
        version="1.0.0",
        docs_url=None if settings.production else "/api/docs",
        redoc_url=None if settings.production else "/redoc",
        openapi_url=None if settings.production else "/api/openapi.json",
    )

    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    if settings.production and not os.getenv("CORS_ORIGINS"):
        origins = ["https://" + h.strip() for h in settings.allowed_hosts.split(",") if h.strip()]
        if os.getenv("RENDER_EXTERNAL_HOSTNAME"):
            origins.append("https://" + os.environ["RENDER_EXTERNAL_HOSTNAME"])
    if settings.production and "*" in origins:
        raise RuntimeError("Production CORS_ORIGINS must be explicit.")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-Owner-Token"],
    )

    # ── privacy gate ────────────────────────────────────────────────
    # Every /api data request must carry the owner's anonymous browser token.
    # The token never touches the database: only its keyed hash (owner id) is
    # kept, and it also derives the encryption key for user content.
    @app.middleware("http")
    async def privacy_gate(request: Request, call_next):
        path = request.url.path
        if settings.production and path != "/api/health" and request.url.scheme != "https":
            return JSONResponse(status_code=426, content={"detail": "HTTPS is required."})
        is_public = (
            not path.startswith("/api")
            or path == "/api/health"
            or (not settings.production and path in {"/api/docs", "/api/openapi.json"})
        )
        token = request.headers.get("X-Owner-Token", "")
        if is_public:
            request.state.owner = None
            request.state.token = ""
        elif not re.fullmatch(r"ss_[A-Za-z0-9_-]{43}" if settings.production else r"ss_[A-Za-z0-9_-]{3,125}", token):
            return JSONResponse(status_code=401, content={
                "detail": "A valid anonymous session token is required."
            })
        else:
            from app.security.owner import owner_hash
            from app.security.session import SessionCapacityError, SessionEnded, touch_session
            request.state.token = token
            request.state.owner = owner_hash(token)
            if path != "/api/me/purge":
                try:
                    await run_in_threadpool(touch_session, request.state.owner)
                except SessionEnded:
                    return JSONResponse(status_code=410, content={
                        "detail": "This research session has ended. Start a new session in the browser."
                    })
                except SessionCapacityError:
                    return JSONResponse(status_code=429, content={"detail": "The service is at session capacity. Please retry later."},
                                        headers={"Retry-After": "60"})
        return await call_next(request)

    @app.middleware("http")
    async def observe_requests(request: Request, call_next):
        start = time.monotonic()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        except Exception as exc:
            # SQL/native exception tracebacks can contain bound document data.
            request_log.error("unhandled request failure (%s)", type(exc).__name__)
            return JSONResponse(status_code=500, content={
                "detail": "Something went wrong while processing your request. Please try again."})
        finally:
            owner = getattr(request.state, "owner", None)
            # FastAPI may retain the router-local path after a prefixed include.
            # Resolve our known API templates even when privacy rejects before routing.
            if request.url.path.startswith(settings.api_prefix + "/"):
                local_scope = dict(request.scope, path=request.url.path[len(settings.api_prefix):], root_path="")
                route = next((settings.api_prefix + candidate.path for candidate in router.routes
                              if candidate.matches(request.scope)[0] == Match.FULL or
                              candidate.matches(local_scope)[0] == Match.FULL), "unmatched")
            else:
                route = "public"
            request_log.info(json.dumps({
                "event": "api_request", "route": route,
                "owner_prefix": owner[:8] if owner else None,
                "latency_ms": round((time.monotonic() - start) * 1000, 1),
                "status": status,
                "outcome": "ok" if status < 400 else "error",
            }))

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if settings.production:
            resp.headers["Strict-Transport-Security"] = "max-age=31536000"
        if request.url.path.startswith("/api"):
            resp.headers["Cache-Control"] = "no-store"
        # Restrict active content to the served application.
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com data:; img-src 'self' data: blob:; "
            "connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'")
        return resp

    app.include_router(router, prefix=settings.api_prefix)

    from app.security.session import SessionEnded

    @app.exception_handler(SessionEnded)
    async def ended_session(request: Request, exc: SessionEnded):
        return JSONResponse(status_code=410, content={
            "detail": "This research session has ended. Start a new session in the browser."})

    from app.security.traffic import RequestBudgetMiddleware
    app.add_middleware(RequestBudgetMiddleware)
    if settings.production:
        hosts = [h.strip() for h in settings.allowed_hosts.split(",") if h.strip()]
        render_host = os.getenv("RENDER_EXTERNAL_HOSTNAME", "")
        if render_host:
            hosts.append(render_host)
        if not hosts or "*" in hosts:
            raise RuntimeError("Production requires explicit ALLOWED_HOSTS or RENDER_EXTERNAL_HOSTNAME.")
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts + ["localhost", "127.0.0.1"],
                           www_redirect=False)

    @app.exception_handler(Exception)
    async def friendly_errors(request: Request, exc: Exception):
        log.error("unhandled route failure (%s)", type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content={"detail": "Something went wrong while processing your request. "
                               "Please try again - if it persists, re-upload the paper."},
        )

    @app.on_event("startup")
    async def warmup():
        # schema backfills + crash reconciliation + cache eviction, then vector store
        from app.database.maintenance import run as run_maintenance
        from app.services.vector_store import store
        try:
            run_maintenance()
        except Exception as exc:
            log.error("database maintenance failed (%s)", type(exc).__name__)
            raise
        from app.security.session import expire_abandoned_sessions
        try:
            expire_abandoned_sessions()
        except Exception as exc:
            log.error("session cleanup failed (%s)", type(exc).__name__)
            raise
        import threading as _threading
        stop_cleanup = _threading.Event()
        app.state.stop_cleanup = stop_cleanup

        def _cleanup_loop():
            while not stop_cleanup.wait(300):
                try:
                    expire_abandoned_sessions()
                except Exception as exc:
                    log.error("scheduled session cleanup failed (%s)", type(exc).__name__)

        _threading.Thread(target=_cleanup_loop, daemon=True,
                          name="session-cleanup").start()
        from app.services.jobs import start_watch_scheduler
        start_watch_scheduler()

        try:
            status = store.probe_integrity(repair=True)
            if not status["ok"]:
                log.error("search index startup probe failed: %s", status["issue"])
        except Exception as exc:
            log.error("search index startup probe failed (%s)", type(exc).__name__)

        dist = Path(os.getenv("FRONTEND_DIST_DIR", str(
            Path(__file__).resolve().parents[2] / "frontend" / "dist"))).resolve()
        if settings.serve_frontend and dist.is_dir():
            app.mount("/", SPAStaticFiles(directory=str(dist), html=True),
                      name="frontend")

    @app.on_event("shutdown")
    async def shutdown_cleanup():
        stop = getattr(app.state, "stop_cleanup", None)
        if stop is not None:
            stop.set()

    return app


app = create_app()

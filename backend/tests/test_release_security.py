"""Production admission, provider fairness and independent retrieval regressions."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException


def test_client_cannot_rotate_real_ip_header_to_bypass_limit(monkeypatch):
    from app.api import routes
    monkeypatch.setattr(routes, "_buckets", {})
    monkeypatch.setattr(routes.settings, "rate_limit_per_minute", 1)
    for i in range(5):
        request = SimpleNamespace(state=SimpleNamespace(owner=f"owner-{i}"),
                                  client=SimpleNamespace(host="203.0.113.1"),
                                  headers={"x-real-ip": f"spoofed-{i}"})
        routes.rate_limit(request, "test")
    with pytest.raises(HTTPException) as exc:
        request.state.owner = "owner-six"
        request.headers["x-real-ip"] = "different-spoof"
        routes.rate_limit(request, "test")
    assert exc.value.status_code == 429


def test_owner_provider_budget_does_not_use_another_owners_allowance(monkeypatch):
    from app.rag import llm_providers as llm
    monkeypatch.setattr(llm, "_hourly_calls", {})
    monkeypatch.setattr(llm, "_owner_hourly_calls", {})
    monkeypatch.setattr(llm.settings, "llm_calls_per_owner_hour", 1)
    monkeypatch.setattr(llm.settings, "llm_calls_per_provider_hour", 10)
    assert llm._reserve_provider_call("gemini", "alice")
    assert not llm._reserve_provider_call("groq", "alice")
    assert llm._reserve_provider_call("gemini", "bob")


def test_bad_answer_cooldown_is_scoped_to_its_owner(monkeypatch):
    from app.rag import llm_providers as llm
    monkeypatch.setattr(llm, "_cooldowns", {})
    monkeypatch.setattr(llm, "_hourly_calls", {})
    monkeypatch.setattr(llm, "_owner_hourly_calls", {})
    for _ in range(3):
        llm._record_failure("gemini", ValueError("bad citations"), "alice")
    assert not llm._provider_available("gemini", "alice")
    assert llm._provider_available("gemini", "bob")


def test_streamed_body_is_bounded_without_content_length(monkeypatch):
    from app.security.traffic import RequestBudgetMiddleware
    from app.security.traffic import settings
    monkeypatch.setattr(settings, "production", False)
    async def run():
        async def app(scope, receive, send):
            while (await receive()).get("more_body", False):
                pass
            raise AssertionError("oversized body reached the handler")
        middleware = RequestBudgetMiddleware(app)
        sent = []
        chunks = iter([{"type": "http.request", "body": b"x" * 40000, "more_body": True},
                       {"type": "http.request", "body": b"x" * 40000, "more_body": False}])
        async def receive():
            return next(chunks)
        async def send(message):
            sent.append(message)
        await middleware({"type": "http", "path": "/api/chat", "headers": [], "client": ("test", 0)}, receive, send)
        assert sent[0]["status"] == 413
        assert middleware._total == 0
    asyncio.run(run())


@pytest.mark.parametrize("edge_header,expected", [(b"203.0.113.45", "203.0.113.45"),
                                                  (b"invalid", "unverified-render-edge"),
                                                  (None, "unverified-render-edge")])
def test_render_admission_ignores_spoofable_forwarded_ip_chain(monkeypatch, edge_header, expected):
    from app.security.traffic import RequestBudgetMiddleware, settings
    monkeypatch.setattr(settings, "production", True)
    monkeypatch.setattr(settings, "trust_render_edge", True)
    monkeypatch.setenv("RENDER_EXTERNAL_HOSTNAME", "fixture.onrender.com")
    reached = []
    async def app(scope, receive, send):
        reached.append(scope["client"][0])
    async def run():
        middleware = RequestBudgetMiddleware(app)
        async def receive():
            return {"type": "http.request", "body": b""}
        async def send(message):
            pass
        for spoof in (b"1.1.1.1", b"8.8.8.8"):
            headers = [(b"x-forwarded-for", spoof), (b"x-real-ip", spoof)]
            if edge_header is not None:
                headers.append((b"cf-connecting-ip", edge_header))
            await middleware({"type": "http", "path": "/api/papers", "headers": headers,
                              "client": (spoof.decode(), 0)}, receive, send)
        assert reached == [expected, expected]
    asyncio.run(run())


def test_one_owners_busy_requests_leave_another_owner_admitted(monkeypatch):
    from app.security.traffic import RequestBudgetMiddleware
    from app.security.traffic import settings
    monkeypatch.setattr(settings, "production", False)
    monkeypatch.setattr(settings, "max_concurrent_requests_per_owner", 1)
    monkeypatch.setattr(settings, "max_concurrent_requests", 8)
    async def run():
        gate = asyncio.Event()
        entered = asyncio.Event()
        successful = []
        async def app(scope, receive, send):
            successful.append(dict(scope["headers"])[b"x-owner-token"])
            entered.set()
            await gate.wait()
        middleware = RequestBudgetMiddleware(app)
        async def request(token):
            sent = []
            async def send(message):
                sent.append(message)
            async def receive():
                return {"type": "http.request", "body": b""}
            await middleware({"type": "http", "path": "/api/search", "headers": [(b"x-owner-token", token)], "client": ("test", 0)}, receive, send)
            return sent
        first = asyncio.create_task(request(b"alice"))
        await asyncio.wait_for(entered.wait(), timeout=3)
        rejected = await asyncio.wait_for(request(b"alice"), timeout=3)
        assert rejected[0]["status"] == 429
        second = asyncio.create_task(request(b"bob"))
        await asyncio.sleep(0)
        assert successful == [b"alice", b"bob"]
        gate.set()
        await asyncio.gather(first, second)
        assert middleware._total == 0
    asyncio.run(run())


def test_full_ingestion_admission_leaves_read_and_erasure_available(monkeypatch):
    from app.security.traffic import RequestBudgetMiddleware
    from app.security.traffic import settings
    monkeypatch.setattr(settings, "production", False)
    monkeypatch.setattr(settings, "max_concurrent_ingestions", 1)
    async def run():
        entered, resume = asyncio.Event(), asyncio.Event()
        reached = []
        async def app(scope, receive, send):
            reached.append(scope["path"])
            if scope["path"] == "/api/papers/upload":
                entered.set()
                await resume.wait()
        middleware = RequestBudgetMiddleware(app)
        async def request(path, token):
            sent = []
            async def receive():
                return {"type": "http.request", "body": b""}
            async def send(message):
                sent.append(message)
            await middleware({"type": "http", "path": path, "headers": [(b"x-owner-token", token)], "client": ("test", 0)}, receive, send)
            return sent
        active = asyncio.create_task(request("/api/papers/upload", b"alice"))
        await asyncio.wait_for(entered.wait(), timeout=3)
        assert (await request("/api/papers/upload", b"bob"))[0]["status"] == 429
        await request("/api/papers", b"bob")
        await request("/api/me/purge", b"bob")
        assert reached == ["/api/papers/upload", "/api/papers", "/api/me/purge"]
        resume.set()
        await active
        assert middleware._ingestions == middleware._total == 0
    asyncio.run(run())


def test_foreign_uploads_do_not_change_private_retrieval_scores():
    from app.database.database import Base, SessionLocal, engine
    from app.models.entities import Chunk, Paper
    from app.security.session import touch_session, purge_owner
    from app.services.scoped_index import for_papers
    Base.metadata.create_all(engine)
    alice, bob = uuid.uuid4().hex, uuid.uuid4().hex
    touch_session(alice)
    touch_session(bob)
    aid, bid = uuid.uuid4().hex[:12], uuid.uuid4().hex[:12]
    try:
        with SessionLocal() as db:
            db.add(Paper(id=aid, owner=alice, title="Alice document", status="ready"))
            db.flush()
            for text in ["Attention mechanisms explain observations and validation.", "Validation uses a controlled sequence of observations.", "Unrelated groceries describe fruit and vegetables."]:
                db.add(Chunk(paper_id=aid, text=text, section_name="methods", page=1))
            db.commit()
        before = for_papers([aid])
        dense = before.dense("attention validation", 8, [aid])
        lexical = before.lexical("attention validation", 8, [aid])
        with SessionLocal() as db:
            db.add(Paper(id=bid, owner=bob, title="Bob private document", status="ready"))
            db.flush()
            for _ in range(50):
                db.add(Chunk(paper_id=bid, text="Attention validation quantum chamber secrets.", section_name="methods", page=1))
            db.commit()
        after = for_papers([aid])
        assert after is before
        assert after.dense("attention validation", 8, [aid]) == dense
        assert after.lexical("attention validation", 8, [aid]) == lexical
        assert for_papers([aid, bid]) is None
    finally:
        with SessionLocal() as db:
            purge_owner(db, alice)
            purge_owner(db, bob)

"""A visitor's explicit local choice must override previous cloud consent/cache."""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import routes
from app.database.database import Base, get_db
from app.main import app
from app.models.entities import ChatMessage, Chunk, OwnerSession, Paper, PaperSection
from app.rag import answerer, llm_providers
from app.rag.retriever import Retrieved
from app.security import session
from app.security.owner import owner_hash
from app.services import usage
from app.services.analysis import eli5
from app.services.scholarly import common


QUESTION = "How does calibration compare detector measurements?"
PASSAGE_TEXT = (
    "Calibration compares detector measurements using a stable quantum reference. "
    "The quantum detectors measure this reference before each observation."
)


@pytest.fixture
def research_sessions(tmp_path, monkeypatch):
    database = create_engine(f"sqlite:///{(tmp_path / 'answer-modes.db').as_posix()}",
                             connect_args={"check_same_thread": False})
    Base.metadata.create_all(database)
    factory = sessionmaker(database, autoflush=False, expire_on_commit=False)
    for module in (session, common, answerer, usage):
        monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setattr(routes, "_buckets", {})
    for field in ("_cooldowns", "_hourly_calls", "_owner_hourly_calls", "_flights"):
        monkeypatch.setattr(llm_providers, field, {})
    for field, value in {
        "production": False, "cloud_ai_enabled": True,
        "cloud_provider_data_approved": True, "gemini_api_key": "offline-test-key",
        "groq_api_key": "", "openai_api_key": "", "llm_provider_chain": "gemini,groq",
    }.items():
        monkeypatch.setattr(llm_providers.settings, field, value)

    tokens = {"alice": "ss_" + "A" * 43, "bob": "ss_" + "B" * 43}
    owners = {name: owner_hash(token) for name, token in tokens.items()}
    with factory() as db:
        for name, owner in owners.items():
            db.add(OwnerSession(owner=owner, cloud_ai_opt_in=name == "alice"))
            db.add(Paper(id=name, owner=owner, title="Quantum detector calibration",
                         abstract=PASSAGE_TEXT, status="ready"))
        db.commit()
        for name in owners:
            db.add(Chunk(paper_id=name, section_name="Abstract", text=PASSAGE_TEXT, page=1))
            db.add(PaperSection(paper_id=name, name="Abstract", text=PASSAGE_TEXT, page_start=1))
        db.commit()

    def dependency():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = dependency
    # The streaming route does its ownership check before constructing SSE
    # and opens this dependency directly rather than through FastAPI injection.
    monkeypatch.setattr(routes, "get_db", dependency)
    monkeypatch.setattr(answerer, "retrieve", lambda query, paper_ids=None, **kw: [
        Retrieved(7, paper_ids[0], "Quantum detector calibration", "Abstract", 1,
                  PASSAGE_TEXT, 0.9)])
    monkeypatch.setattr(answerer, "build_suggestions", lambda *a, **kw: [])
    clients = {name: TestClient(app, base_url="https://testserver", headers={"X-Owner-Token": token})
               for name, token in tokens.items()}
    yield factory, clients, owners
    for client in clients.values():
        client.close()
    app.dependency_overrides.pop(get_db, None)
    database.dispose()


def _request(client, name, path, *, mode="local", message=QUESTION):
    return client.post(path, json={"paper_id": name, "message": message, "answer_mode": mode})


def _answer(response, path):
    assert response.status_code == 200
    if path == "/api/chat":
        return response.json()
    events = [json.loads(line.removeprefix("data: "))
              for line in response.text.splitlines() if line.startswith("data: ")]
    assert not any(event["type"] == "error" for event in events)
    assert events[-1]["type"] == "done"
    return {**events[-1], "answer": "".join(event["text"] for event in events
                                           if event["type"] == "token")}


def _forbidden_remote(*args, **kwargs):
    raise AssertionError("A local answer entered a cloud provider or cache path")


@pytest.mark.parametrize("path", ["/api/chat", "/api/chat/stream"])
def test_explicit_local_ignores_opt_in_and_existing_cloud_answer(research_sessions, monkeypatch, path):
    factory, clients, owners = research_sessions
    passages = answerer.retrieve(QUESTION, paper_ids=["alice"])
    common.cache_set(llm_providers._cache_key(QUESTION, passages, owners["alice"]),
                     {"answer": "Cached cloud answer marker [1].", "provider": "gemini"}, hours=6)
    assert common.cache_get(llm_providers._cache_key(QUESTION, passages, owners["alice"]))
    monkeypatch.setattr(common, "cache_get", _forbidden_remote)
    monkeypatch.setattr(llm_providers, "synthesize", _forbidden_remote)
    monkeypatch.setattr(llm_providers, "synthesize_stream", _forbidden_remote)
    monkeypatch.setattr(answerer, "llm_configured", _forbidden_remote)
    result = _answer(_request(clients["alice"], "alice", path), path)
    assert result["mode"] == "extractive" and result["cached"] is False
    assert "detector measurements" in result["answer"]
    assert "Cached cloud answer marker" not in result["answer"]
    with factory() as db:
        assert db.get(OwnerSession, owners["alice"]).cloud_ai_opt_in is True
        assert db.query(ChatMessage).filter(ChatMessage.owner == owners["alice"]).count() == 2


@pytest.mark.parametrize("path", ["/api/chat", "/api/chat/stream"])
@pytest.mark.parametrize("question", ["What is this paper about?", "Explain it simply"])
def test_local_overview_and_simple_explanation_never_enter_cloud_paths(
        research_sessions, monkeypatch, path, question):
    _, clients, _ = research_sessions
    monkeypatch.setattr(eli5, "build_eli5", _forbidden_remote)
    monkeypatch.setattr(common, "cache_get", _forbidden_remote)
    monkeypatch.setattr(llm_providers, "synthesize", _forbidden_remote)
    monkeypatch.setattr(llm_providers, "synthesize_stream", _forbidden_remote)
    result = _answer(_request(clients["alice"], "alice", path, message=question), path)
    assert result["answer"].strip() and not result["mode"].startswith("synthesized")
    assert result["cached"] is False


@pytest.mark.parametrize("path", ["/api/chat", "/api/chat/stream"])
def test_cloud_requires_current_owner_consent_before_any_answer(research_sessions, monkeypatch, path):
    factory, clients, owners = research_sessions
    monkeypatch.setattr(routes, "answer_question", _forbidden_remote)
    monkeypatch.setattr(routes, "stream_answer_events", _forbidden_remote)
    response = _request(clients["bob"], "bob", path, mode="cloud")
    assert response.status_code == 403 and "session" in response.json()["detail"]
    with factory() as db:
        assert db.query(ChatMessage).filter(ChatMessage.owner == owners["bob"]).count() == 0


@pytest.mark.parametrize("path", ["/api/chat", "/api/chat/stream"])
def test_cloud_choice_uses_provider_but_other_owner_keeps_local_default(research_sessions, monkeypatch, path):
    _, clients, owners = research_sessions
    calls = []

    def cloud(*args, owner="", **kwargs):
        calls.append(owner)
        return "Cloud calibration answer [1].", "gemini"

    def cloud_stream(*args, owner="", **kwargs):
        calls.append(owner)
        return "gemini", iter(["Cloud calibration answer [1]."])

    monkeypatch.setattr(llm_providers, "synthesize", cloud)
    monkeypatch.setattr(llm_providers, "synthesize_stream", cloud_stream)
    alice = _answer(_request(clients["alice"], "alice", path, mode="cloud"), path)
    assert alice["mode"] == "synthesized:gemini"
    assert "Cloud calibration answer" in alice["answer"]
    bob_response = clients["bob"].post(path, json={"paper_id": "bob", "message": QUESTION})
    bob = _answer(bob_response, path)
    assert bob["mode"] == "extractive" and "Cloud calibration answer" not in bob["answer"]
    assert calls == [owners["alice"]]
    assert clients["bob"].get("/api/me/cloud-ai").json() == {"enabled": False, "available": True}


@pytest.mark.parametrize("configuration", ["no-key", "key-outside-chain", "operator-disabled", "unapproved"])
def test_unconfigured_cloud_is_not_advertised_or_accepted(research_sessions, monkeypatch, configuration):
    factory, clients, owners = research_sessions
    settings = llm_providers.settings
    if configuration == "no-key":
        monkeypatch.setattr(settings, "gemini_api_key", "")
    elif configuration == "key-outside-chain":
        monkeypatch.setattr(settings, "llm_provider_chain", "groq")
    elif configuration == "operator-disabled":
        monkeypatch.setattr(settings, "cloud_ai_enabled", False)
    else:
        monkeypatch.setattr(settings, "production", True)
        monkeypatch.setattr(settings, "cloud_provider_data_approved", False)
    assert clients["alice"].get("/api/me/cloud-ai").json() == {"enabled": False, "available": False}
    assert clients["alice"].get("/api/me/privacy").json()["cloud_ai_available"] is False
    assert clients["alice"].post("/api/me/cloud-ai", json={"enabled": True}).status_code == 409
    for path in ("/api/chat", "/api/chat/stream"):
        response = _request(clients["alice"], "alice", path, mode="cloud")
        assert response.status_code == 409 and "Local Evidence" in response.json()["detail"]
    with factory() as db:
        assert db.query(ChatMessage).filter(ChatMessage.owner == owners["alice"]).count() == 0


def test_static_cloud_availability_survives_temporary_provider_cooldown(research_sessions):
    _, clients, owners = research_sessions
    llm_providers._cooldowns["gemini"] = {"fails": [], "retry_until": time.time() + 600}
    assert llm_providers.build_chain(owners["alice"]) == []
    assert clients["alice"].get("/api/me/cloud-ai").json() == {"enabled": True, "available": True}
    assert clients["alice"].get("/api/me/privacy").json()["cloud_ai_available"] is True


def test_invalid_answer_mode_is_rejected(research_sessions):
    _, clients, _ = research_sessions
    assert _request(clients["alice"], "alice", "/api/chat", mode="automatic").status_code == 422

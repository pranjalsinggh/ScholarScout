"""Real transaction and API regressions for anonymous isolation and erasure.

Every database and file belongs to tmp_path, never the application's data.
Providers and native parser jobs are not called by these tests.
"""
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import json
from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from app.api import routes
from app.database.database import Base, get_db
from app.main import app
from app.models.entities import (ApiCache, ChatMessage, Chunk, EndedSession,
                                 OwnerSession, Paper, ProviderUsage)
from app.rag import answerer
from app.security import session
from app.security.owner import owner_hash
from app.services import jobs, scoped_index, usage
from app.services.analysis import topics
from app.services.scholarly import common


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    database = create_engine(f"sqlite:///{(tmp_path / 'private.db').as_posix()}",
                             connect_args={"check_same_thread": False, "timeout": 10})

    @event.listens_for(database, "connect")
    def sqlite_settings(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA secure_delete=ON")

    Base.metadata.create_all(database)
    factory = sessionmaker(database, autoflush=False, expire_on_commit=False)
    for module in (session, common, answerer, scoped_index, usage):
        monkeypatch.setattr(module, "SessionLocal", factory)
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setattr(session, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(jobs, "cancel_owner_jobs", lambda owner: None)
    monkeypatch.setattr(session, "_finish_erasure", lambda counts, hashes, **kw: {
        **counts, "embedding_vectors_removed": len(hashes)})
    from app.services.vector_store import store
    monkeypatch.setattr(store, "rebuild", lambda: 0)
    scoped_index._cache.clear()

    def dependency():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = dependency
    yield factory, upload_dir
    app.dependency_overrides.pop(get_db, None)
    scoped_index._cache.clear()
    database.dispose()


def seed(factory, token, ids):
    owner = owner_hash(token)
    with factory() as db:
        db.add(OwnerSession(owner=owner, cloud_ai_opt_in=True, research_lookup_opt_in=True))
        for pid in ids:
            db.add(Paper(id=pid, owner=owner, title=f"{pid} quantum optics research",
                         abstract=f"{pid} local detector measurement", status="ready"))
        db.commit()
        for pid in ids:
            db.add(Chunk(paper_id=pid, text=f"{pid} quantum optics detector measurement",
                         section_name="Abstract", page=1))
        db.commit()
    return owner


def client(token):
    result = TestClient(app)
    result.headers["X-Owner-Token"] = token
    return result


def test_remote_enrichment_never_holds_shared_write_lock_or_survives_purge(isolated, monkeypatch):
    from app.models.entities import Citation
    from app.services.scholarly import aggregator
    factory, _ = isolated
    owner = seed(factory, "ss_" + "q" * 43, ["enrich-a"])
    bob = seed(factory, "ss_" + "r" * 43, ["enrich-b"])
    entered, resume = Event(), Event()

    def collect(staged, paper, consent):
        staged.add(Citation(paper_id=paper.id, title="Delayed private citation", direction="citing"))
        staged.flush()  # even an uncommitted write locks only private memory
        entered.set()
        assert resume.wait(10)
        staged.commit()

    monkeypatch.setattr(aggregator, "_collect_ecosystem", collect)

    def run():
        with factory() as db:
            aggregator.enrich_ecosystem(db, db.get(Paper, "enrich-a"))

    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(run)
        assert entered.wait(10)
        with factory() as db:
            with session.owner_write(db, bob):
                db.get(Paper, "enrich-b").title = "Bob can keep working"
        with factory() as db:
            session.purge_owner(db, owner)
        resume.set()
        with pytest.raises(session.SessionEnded):
            future.result(timeout=10)
    with factory() as db:
        assert db.get(Paper, "enrich-a") is None
        assert db.query(Citation).filter(Citation.paper_id == "enrich-a").count() == 0
        assert db.get(Paper, "enrich-b").title == "Bob can keep working"


def test_enrichment_publishes_verified_rows_atomically(isolated, monkeypatch):
    from app.models.entities import Author, Citation
    from app.services.scholarly import aggregator
    factory, _ = isolated
    seed(factory, "ss_" + "s" * 43, ["enrich-ready"])

    def collect(staged, paper, consent):
        consent()
        paper.meta = {"match_status": "accepted"}
        staged.add(Citation(paper_id=paper.id, title="Verified follow-up", direction="citing"))
        staged.add(Author(paper_id=paper.id, name="Verified Author"))
        staged.commit()

    monkeypatch.setattr(aggregator, "_collect_ecosystem", collect)
    with factory() as db:
        aggregator.enrich_ecosystem(db, db.get(Paper, "enrich-ready"))
        assert db.get(Paper, "enrich-ready").meta["match_status"] == "accepted"
        assert db.query(Citation).filter(Citation.paper_id == "enrich-ready").count() == 1
        assert db.query(Author).filter(Author.paper_id == "enrich-ready").count() == 1


def test_duplicate_private_index_builds_leave_capacity_for_other_owner(isolated, monkeypatch):
    factory, _ = isolated
    alice = seed(factory, "ss_" + "t" * 43, ["index-alice"])
    seed(factory, "ss_" + "u" * 43, ["index-bob"])
    entered, resume, second_started = Event(), Event(), Event()
    original = scoped_index.Snapshot
    calls = []

    def delayed(owner, signature, rows):
        calls.append(owner)
        if owner == alice:
            entered.set()
            assert resume.wait(10)
        return original(owner, signature, rows)

    monkeypatch.setattr(scoped_index, "Snapshot", delayed)
    def second():
        second_started.set()
        return scoped_index.for_papers(["index-alice"])
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(scoped_index.for_papers, ["index-alice"])
        assert entered.wait(10)
        duplicate = pool.submit(second)
        assert second_started.wait(10)
        other = pool.submit(scoped_index.for_papers, ["index-bob"])
        try:
            assert other.result(timeout=5).pids == ["index-bob"]
        finally:
            resume.set()
        assert duplicate.result(timeout=10) is first.result(timeout=10)
    assert calls.count(alice) == 1


def test_topics_never_return_foreign_ids_or_foreign_cluster_share(isolated):
    factory, _ = isolated
    alice, bob = "ss_" + "a" * 43, "ss_" + "b" * 43
    seed(factory, alice, ["alice-1", "alice-2", "alice-3"])
    seed(factory, bob, ["bob-secret"])
    response = client(alice).get("/api/topics")
    assert response.status_code == 200
    payload = response.json()
    returned = {pid for cluster in payload["topics"] for pid in cluster["paper_ids"]}
    assert returned == {"alice-1", "alice-2", "alice-3"}
    assert sum(cluster["share"] for cluster in payload["topics"]) == pytest.approx(100, abs=0.2)
    assert "bob-secret" not in response.text


def test_topic_document_fallback_keeps_sparse_rows(isolated, monkeypatch):
    factory, _ = isolated
    owner = seed(factory, "ss_" + "c" * 43, ["one-document"])
    with factory() as db:
        for i in range(5):
            db.add(Chunk(paper_id="one-document", text=f"quantum detector measurement variation {i}"))
        db.commit()
    original = topics.KMeans.fit_predict

    def sparse_fit(model, matrix, *args, **kwargs):
        from scipy import sparse
        assert sparse.issparse(matrix)
        return original(model, matrix, *args, **kwargs)

    monkeypatch.setattr(topics.KMeans, "fit_predict", sparse_fit)
    with factory() as db:
        assert topics.build_topics(db, owner=owner).topics


def test_inflight_sync_chat_cannot_recreate_messages_after_purge(isolated, monkeypatch):
    factory, _ = isolated
    token = "ss_" + "d" * 43
    owner = seed(factory, token, ["chat-paper"])
    entered, resume = Event(), Event()

    def delayed_answer(*args, **kwargs):
        entered.set()
        assert resume.wait(10)
        return answerer.Answer(text="Private delayed answer", evidence=[])

    monkeypatch.setattr(routes, "answer_question", delayed_answer)
    browser = client(token)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(browser.post, "/api/chat", json={
            "paper_id": "chat-paper", "message": "Private delayed question"})
        assert entered.wait(10)
        try:
            assert browser.delete("/api/me/purge").status_code == 200
        finally:
            resume.set()
        assert future.result(timeout=10).status_code == 410
    with factory() as db:
        assert db.query(ChatMessage).filter(ChatMessage.owner == owner).count() == 0
        assert db.get(OwnerSession, owner) is None
        assert db.get(EndedSession, owner) is not None
    assert browser.get("/api/papers").status_code == 410


def test_stream_final_commit_is_blocked_after_midstream_erasure(isolated, monkeypatch):
    factory, _ = isolated
    token = "ss_" + "e" * 43
    owner = seed(factory, token, ["stream-paper"])
    monkeypatch.setattr(answerer, "llm_configured", lambda owner: False)
    monkeypatch.setattr(answerer, "_resolve_meta", lambda *a, **kw: (
        answerer.Answer(text="Private stream answer " * 20, evidence=[]), None, None))
    stream = answerer.stream_answer_events("Private question", paper_ids=["stream-paper"],
                                           owner=owner, token=token)
    first = json.loads(next(stream).removeprefix("data: "))
    assert first["type"] == "token"
    with factory() as db:
        session.purge_owner(db, owner, rebuild=False)
    remaining = [json.loads(item.removeprefix("data: ")) for item in stream]
    assert remaining[-1]["type"] == "done"
    assert remaining[-1]["mode"] == "ended"
    with factory() as db:
        assert db.query(ChatMessage).filter(ChatMessage.owner == owner).count() == 0


@pytest.mark.parametrize("prefix", ["synth", "eli5::v4", "research"])
def test_all_private_caches_reject_a_late_write_after_purge(isolated, prefix):
    factory, _ = isolated
    token = "ss_" + "f" * 43
    owner = seed(factory, token, [])
    key = f"{prefix}::{owner}::late"
    common.cache_set(key, {"private": "content"})
    assert common.cache_get(key) == {"private": "content"}
    with factory() as db:
        session.purge_owner(db, owner, rebuild=False)
    common.cache_set(key, {"private": "late content"})
    assert common.cache_get(key) is None
    with factory() as db:
        assert db.get(ApiCache, key) is None


def test_purge_serializes_with_an_already_admitted_write(isolated):
    factory, _ = isolated
    owner = seed(factory, "ss_" + "g" * 43, ["race-paper"])
    entered, finish = Event(), Event()

    def admitted_write():
        with factory() as db:
            with session.owner_write(db, owner):
                db.add(ChatMessage(owner=owner, paper_id="race-paper", role="user", content="fixture"))
                entered.set()
                assert finish.wait(10)

    def purge():
        with factory() as db:
            return session.purge_owner(db, owner, rebuild=False)

    with ThreadPoolExecutor(max_workers=2) as executor:
        writer = executor.submit(admitted_write)
        assert entered.wait(10)
        erasure = executor.submit(purge)
        finish.set()
        writer.result(timeout=10)
        erasure.result(timeout=10)
    with factory() as db:
        assert db.query(ChatMessage).filter(ChatMessage.owner == owner).count() == 0


def test_revocation_survives_new_engine_and_unknown_purge_is_bounded(isolated):
    factory, _ = isolated
    owner = seed(factory, "ss_" + "h" * 43, [])
    with factory() as db:
        session.purge_owner(db, owner, rebuild=False)
    independent = create_engine(str(factory.kw["bind"].url))
    with independent.connect() as connection:
        assert connection.execute(text("SELECT owner FROM ended_sessions")).scalar() == owner
    independent.dispose()
    with pytest.raises(session.SessionEnded):
        session.touch_session(owner)
    with factory() as db:
        session.purge_owner(db, "0" * 32, rebuild=False)
    with factory() as db:
        assert db.query(EndedSession).count() == 1


def test_orphan_sweep_and_late_usage_do_not_keep_private_content(isolated):
    factory, _ = isolated
    owner = "9" * 32
    with factory() as db:
        db.add(ChatMessage(owner=owner, role="assistant", content="old orphan fixture"))
        db.add(ApiCache(key=f"synth::{owner}::old", payload={"answer": "orphan"},
                        expires_at=dt.datetime.utcnow() + dt.timedelta(hours=1)))
        db.commit()
    assert session.expire_abandoned_sessions() == 1
    usage.record_usage(owner, "test", "local fixture", "private input", "private output")
    with factory() as db:
        assert db.query(ChatMessage).count() == 0
        assert db.query(ApiCache).count() == 0
        assert db.query(ProviderUsage).count() == 0

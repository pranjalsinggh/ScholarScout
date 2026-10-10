from types import SimpleNamespace
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api import routes
from app.database.database import Base
from app.models.entities import OwnerSession, Paper
from app.schemas.api import ResearchLookupRequest
from app.services import jobs


def test_enabling_connected_research_refreshes_already_uploaded_papers(monkeypatch, tmp_path):
    queued = []
    monkeypatch.setattr(jobs, "submit_enrichment", lambda pid, **kwargs: queued.append(pid) or True,
                        raising=False)
    engine = create_engine(f"sqlite:///{(tmp_path / 'refresh.db').as_posix()}")
    Base.metadata.create_all(engine)
    try:
        with Session(engine, autoflush=False) as db:
            db.add(OwnerSession(owner="owner-a", research_lookup_opt_in=False))
            db.add(Paper(id="report-a", owner="owner-a", status="ready", stages=[]))
            db.commit()
            result = routes.set_research_lookup_preference(
                SimpleNamespace(state=SimpleNamespace(owner="owner-a")),
                ResearchLookupRequest(enabled=True), db)
            assert queued == ["report-a"]
            assert result["papers_queued"] == 1 and result["refreshing"]
            paper = db.get(Paper, "report-a")
            assert paper.status == "ready"
            assert any(s["key"] == "ecosystem" and s["state"] == "active" for s in paper.stages)
            assert db.get(OwnerSession, "owner-a").research_lookup_opt_in
    finally:
        engine.dispose()


def test_duplicate_context_refreshes_share_one_background_job(monkeypatch):
    submitted = []
    monkeypatch.setattr(jobs._executor, "submit", lambda *args, **kwargs: submitted.append((args, kwargs)))
    jobs._enrichment_pending.clear()
    try:
        assert jobs.submit_enrichment("paper-a", owner="owner-a")
        assert not jobs.submit_enrichment("paper-a", owner="owner-a")
        assert len(submitted) == 1
    finally:
        jobs._enrichment_pending.clear()

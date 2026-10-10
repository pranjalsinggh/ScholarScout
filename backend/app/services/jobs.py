"""Background job runner + scheduled maintenance.

A bounded thread pool replaces FastAPI BackgroundTasks so paper analysis:
  - never blocks request worker threads
  - runs with bounded concurrency (2 analyses at a time)
  - has one place to add retries/observability later

Also hosts the watchlist scheduler: saved papers are polled for newly indexed
citations every 6 hours without any manual "Check now".
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass

from app.config import settings

log = logging.getLogger("scholarscout.jobs")

class CapacityError(RuntimeError):
    """Admission is refused before an upload is persisted or parsed."""


class AnalysisPermit:
    def __init__(self, scheduler, owner: str):
        self.scheduler, self.owner = scheduler, owner
        self.released = False
        self.submitted = False

    def release(self) -> None:
        self.scheduler.release(self)


@dataclass
class _Job:
    permit: AnalysisPermit
    callback: object
    args: tuple
    on_cancel: object = None


class FairJobScheduler:
    """Bound running + reserved + queued work; run at most one job per owner."""
    def __init__(self, workers=2, capacity=12, owner_capacity=2, *, start=True):
        self.workers, self.capacity, self.owner_capacity = workers, capacity, owner_capacity
        self._condition = threading.Condition(threading.RLock())
        self._owners = deque()
        self._queues: dict[str, deque] = {}
        self._admitted: dict[str, int] = {}
        self._active: set[str] = set()
        self._closed = False
        if start:
            for index in range(workers):
                threading.Thread(target=self._worker, daemon=True,
                                 name=f"analysis-{index}").start()

    def reserve(self, owner: str) -> AnalysisPermit:
        with self._condition:
            if (self._closed or sum(self._admitted.values()) >= self.capacity
                    or self._admitted.get(owner, 0) >= self.owner_capacity):
                raise CapacityError("Analysis capacity is busy. Wait for a current analysis to finish and retry.")
            self._admitted[owner] = self._admitted.get(owner, 0) + 1
            return AnalysisPermit(self, owner)

    def release(self, permit: AnalysisPermit) -> None:
        with self._condition:
            if permit.released:
                return
            permit.released = True
            remaining = self._admitted.get(permit.owner, 0) - 1
            if remaining > 0:
                self._admitted[permit.owner] = remaining
            else:
                self._admitted.pop(permit.owner, None)
            self._condition.notify_all()

    def submit(self, callback, *args, owner="", permit=None, on_cancel=None):
        permit = permit or self.reserve(owner)
        with self._condition:
            if permit.scheduler is not self or permit.released or permit.submitted:
                raise CapacityError("The analysis reservation is no longer available. Please retry.")
            permit.submitted = True
            queue = self._queues.setdefault(permit.owner, deque())
            if not queue:
                self._owners.append(permit.owner)
            queue.append(_Job(permit, callback, args, on_cancel))
            self._condition.notify_all()
        return permit

    def _next_job(self):
        for _ in range(len(self._owners)):
            owner = self._owners.popleft()
            queue = self._queues[owner]
            if owner in self._active:
                self._owners.append(owner)
                continue
            job = queue.popleft()
            if queue:
                self._owners.append(owner)
            else:
                self._queues.pop(owner, None)
            self._active.add(owner)
            return job
        return None

    def _worker(self):
        while True:
            with self._condition:
                job = self._next_job()
                while job is None:
                    if self._closed:
                        return
                    self._condition.wait()
                    job = self._next_job()
            try:
                job.callback(*job.args)
            except Exception as error:
                log.warning("background analysis operation failed (%s)", type(error).__name__)
            finally:
                with self._condition:
                    self._active.discard(job.permit.owner)
                    job.permit.release()

    def cancel_owner(self, owner):
        with self._condition:
            queue = self._queues.pop(owner, deque())
            self._owners = deque(item for item in self._owners if item != owner)
            for job in queue:
                job.permit.release()
                if job.on_cancel:
                    job.on_cancel()
            self._condition.notify_all()


_executor = FairJobScheduler(settings.analysis_workers, settings.max_pending_analyses,
                             settings.max_pending_analyses_per_owner)
_scheduler_started = False
_enrichment_lock = threading.Lock()
_enrichment_pending: set[str] = set()


def reserve_analysis(owner: str) -> AnalysisPermit:
    return _executor.reserve(owner)


def submit_analysis(paper_id: str, owner_token: str = "", *, owner: str = "",
                    permit: AnalysisPermit | None = None) -> bool:
    """Queue a paper for the full analysis pipeline."""
    if not owner and permit is None:
        from app.security.owner import owner_hash
        owner = owner_hash(owner_token) if owner_token else "legacy"
    _executor.submit(_run_analysis, paper_id, owner_token, owner=owner, permit=permit)
    return True


def cancel_owner_jobs(owner: str) -> None:
    _executor.cancel_owner(owner)
    from app.ingestion.pdf_extractor import cancel_owner_parsers
    cancel_owner_parsers(owner)


def _run_analysis(paper_id: str, owner_token: str) -> None:
    from app.services.pipeline import process_paper
    try:
        process_paper(paper_id, owner_token)
    except Exception as error:
        # pipeline already records failure state on the paper row
        log.warning("analysis failed for paper %s (%s)", paper_id, type(error).__name__)


def mark_context_stage(paper, state: str, detail: str) -> None:
    stages = [dict(stage) for stage in (paper.stages or [])]
    stage = next((s for s in stages if s.get("key") == "ecosystem"), None)
    if stage is None:
        stage = {"key": "ecosystem", "label": "Connected research"}
        stages.append(stage)
    stage.update(state=state, detail=detail)
    paper.stages = stages


def submit_enrichment(paper_id: str, *, owner: str = "") -> bool:
    """Refresh public context without hiding the already analyzed PDF."""
    with _enrichment_lock:
        if paper_id in _enrichment_pending:
            return False
        _enrichment_pending.add(paper_id)
    try:
        if not owner:
            from app.database.database import SessionLocal
            from app.models.entities import Paper
            with SessionLocal() as db:
                paper = db.get(Paper, paper_id)
                owner = paper.owner if paper else "legacy"
        _executor.submit(_run_enrichment, paper_id, owner=owner,
                         on_cancel=lambda: _forget_enrichment(paper_id))
    except Exception:
        with _enrichment_lock:
            _enrichment_pending.discard(paper_id)
        raise
    return True


def _forget_enrichment(paper_id):
    with _enrichment_lock:
        _enrichment_pending.discard(paper_id)


def _run_enrichment(paper_id: str) -> None:
    from app.database.database import SessionLocal
    from app.models.entities import Paper
    from app.security.session import research_lookup_allowed
    from app.services.pipeline import _require_live_session, _write_paper
    from app.security.session import SessionEnded
    from app.services.scholarly.aggregator import enrich_ecosystem
    from app.services.analysis.timeline import build_timeline

    db = SessionLocal()
    owner = ""
    try:
        paper = db.get(Paper, paper_id)
        if paper is None or paper.status != "ready":
            return
        owner = paper.owner
        _require_live_session(db, paper)
        if not research_lookup_allowed(paper.owner):
            _write_paper(db, paper_id, owner, lambda current:
                         mark_context_stage(current, "skipped", "Connected research lookups are off"))
            return
        enrich_ecosystem(db, paper)
        _require_live_session(db, paper)
        build_timeline(db, paper)
        _write_paper(db, paper_id, owner, lambda current:
                     mark_context_stage(current, "done", current.ecosystem_note or "Connected research refreshed"))
    except SessionEnded:
        db.rollback()
    except Exception:
        db.rollback()
        if owner:
            allowed = research_lookup_allowed(owner)
            def failed(current):
                if allowed:
                    current.ecosystem_note = (
                        "Connected research could not be refreshed. The uploaded document remains "
                        "available; retry the lookup when the research providers respond.")
                    log.warning("context refresh failed for paper %s", paper_id)
                else:
                    current.ecosystem_note = "Connected research lookups are off for this session."
                current.ecosystem_ok = False
                mark_context_stage(current, "skipped", current.ecosystem_note)
            try:
                _write_paper(db, paper_id, owner, failed)
            except SessionEnded:
                pass
    finally:
        db.close()
        with _enrichment_lock:
            _enrichment_pending.discard(paper_id)


def start_watch_scheduler(interval_hours: int = 6) -> None:
    """Poll every saved paper for newly indexed citations on a daemon loop."""
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    def loop() -> None:
        while True:
            time.sleep(interval_hours * 3600)
            try:
                _check_all_watchlists()
            except Exception as error:
                log.warning("watchlist poll failed (%s)", type(error).__name__)

    threading.Thread(target=loop, daemon=True, name="watch-scheduler").start()


def _check_all_watchlists() -> None:
    from app.database.database import SessionLocal
    from app.models.entities import Paper, WatchItem
    from app.services.analysis import watchlist
    from app.security.session import research_lookup_allowed

    db = SessionLocal()
    try:
        items = (db.query(WatchItem)
                 .join(Paper, WatchItem.paper_id == Paper.id)
                 .filter(Paper.openalex_id.isnot(None))
                 .all())
        checked = 0
        for item in items:
            if not research_lookup_allowed(item.owner):
                continue
            try:
                added = watchlist.check_updates(db, item)
                checked += 1
                if added:
                    log.info("watchlist: %d new citations for paper %s", added, item.paper_id)
            except Exception:
                continue
        if checked:
            log.info("watchlist poll: checked %d saved papers", checked)
    finally:
        db.close()

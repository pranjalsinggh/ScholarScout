"""Session-local sparse retrieval snapshots.

Vocabulary, IDF, BM25 statistics and diversity vectors are fitted only to one
owner. Another owner's upload cannot alter these scores. Sparse matrices keep
long documents bounded without allocating a corpus-wide dense TF-IDF matrix.
Snapshots are memory-only, bounded by LRU and byte budgets, and erased on purge.
"""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import threading

import numpy as np
from scipy import sparse
from sklearn.preprocessing import normalize

from app.database.database import SessionLocal
from app.config import settings
from app.models.entities import Chunk, Paper
from app.services.embeddings import TfidfEmbeddings
from app.services.vector_store import BM25, _tokenize


class Snapshot:
    def __init__(self, owner, signature, rows):
        self.owner, self.signature = owner, signature
        self.ids = [r[0] for r in rows]
        self.pids = [r[1] for r in rows]
        self.positions = {cid: i for i, cid in enumerate(self.ids)}
        texts = [r[2] or "" for r in rows]
        self.encoder = TfidfEmbeddings()
        self.matrix = normalize(self.encoder.union.fit_transform(texts), norm="l2").astype("float32").tocsr()
        self.bm25 = BM25([_tokenize(text) for text in texts])
        # Include conservative Python-object estimates, not only CSR buffers.
        self.bytes = (self.matrix.data.nbytes + self.matrix.indices.nbytes + self.matrix.indptr.nbytes
                      + sum(len(text.encode("utf-8")) for text in texts) * 8
                      + sum(len(tf) for tf in self.bm25.tfs) * 96)
        self.centroids = {}
        for pid in sorted(set(self.pids)):
            indices = [i for i, value in enumerate(self.pids) if value == pid]
            centroid = sparse.csr_matrix(self.matrix[indices].mean(axis=0))
            self.centroids[pid] = normalize(centroid, norm="l2")

    def dense(self, query, k, wanted):
        query_vector = normalize(self.encoder.union.transform([query]), norm="l2").astype("float32")
        scores = (self.matrix @ query_vector.T).toarray().ravel()
        return self._hits(scores, k, wanted)

    def lexical(self, query, k, wanted):
        return self._hits(self.bm25.scores(_tokenize(query)), k, wanted, positive=True)

    def _hits(self, scores, k, wanted, positive=False):
        allowed = set(wanted)
        candidates = [i for i, pid in enumerate(self.pids) if pid in allowed and (not positive or scores[i] > 0)]
        candidates.sort(key=lambda i: (-float(scores[i]), self.ids[i]))
        return [(self.ids[i], self.pids[i], float(scores[i])) for i in candidates[:k]]

    def similarity(self, first, second):
        i, j = self.positions.get(first), self.positions.get(second)
        return float((self.matrix[i] @ self.matrix[j].T)[0, 0]) if i is not None and j is not None else 0.0

    def paper_matrix(self, wanted=None):
        ids = [pid for pid in self.centroids if wanted is None or pid in set(wanted)]
        if not ids:
            return None
        return ids, sparse.vstack([self.centroids[pid] for pid in ids]).toarray().astype("float32")


_lock = threading.RLock()
_cache: OrderedDict[str, Snapshot] = OrderedDict()
_building: dict[str, threading.Event] = {}
_builders = threading.BoundedSemaphore(settings.index_build_workers)
_MAX_BYTES = settings.index_cache_mb * 1024 * 1024
_MAX_OWNERS = settings.index_cache_owners


def for_papers(paper_ids):
    if not paper_ids:
        return None
    with SessionLocal() as db:
        owners = {row[0] for row in db.query(Paper.owner).filter(Paper.id.in_(paper_ids)).all()}
        if len(owners) != 1:
            return None
        owner = next(iter(owners))
        rows = db.query(Chunk.id, Chunk.paper_id, Chunk.text).join(Paper, Paper.id == Chunk.paper_id).filter(
            Paper.owner == owner, Paper.status == "ready").order_by(Chunk.id).all()
    if not rows:
        return None
    digest = hashlib.sha256()
    for cid, pid, text in rows:
        digest.update(f"{cid}:{pid}:".encode())
        digest.update(hashlib.sha256((text or "").encode()).digest())
    signature = digest.hexdigest()
    with _lock:
        existing = _cache.get(owner)
        if existing and existing.signature == signature:
            _cache.move_to_end(owner)
            return existing
    # Coalesce concurrent rebuilds for one owner. It cannot occupy both CPU
    # build slots while another visitor needs an index.
    while True:
        with _lock:
            existing = _cache.get(owner)
            if existing and existing.signature == signature:
                return existing
            pending = _building.get(owner)
            if pending is None:
                pending = threading.Event()
                _building[owner] = pending
                break
        if not pending.wait(timeout=25):
            return None
    try:
        with _builders:
            with _lock:
                existing = _cache.get(owner)
                if existing and existing.signature == signature:
                    return existing
            candidate = Snapshot(owner, signature, rows)
            # Never publish a candidate which raced session erasure.
            from app.security.session import SessionEnded, owner_write
            with SessionLocal() as db:
                try:
                    with owner_write(db, owner):
                        with _lock:
                            _cache[owner] = candidate
                            _cache.move_to_end(owner)
                            while len(_cache) > _MAX_OWNERS or sum(value.bytes for value in _cache.values()) > _MAX_BYTES:
                                _cache.popitem(last=False)
                except SessionEnded:
                    return None
            return candidate
    finally:
        with _lock:
            _building.pop(owner, None)
            pending.set()


def forget_owner(owner):
    with _lock:
        _cache.pop(owner, None)


def paper_matrix(owner):
    with SessionLocal() as db:
        ids = [r[0] for r in db.query(Paper.id).filter(Paper.owner == owner, Paper.status == "ready").all()]
    snapshot = for_papers(ids)
    return snapshot.paper_matrix(ids) if snapshot else None

"""In-memory vector store over all uploaded papers.

Rows map to Chunk rows in the DB. Rebuilds are transactional: a complete
encoder/matrix/metadata snapshot replaces the old snapshot under one lock.
Stateless encoder vectors are reused by content hash; corpus-fitted TF-IDF
vectors are refreshed when vocabulary/IDF changes. Uses FAISS when available,
otherwise exact numpy dot-product search.
"""
from __future__ import annotations

import hashlib
import logging
import pickle
import re
import threading
import time

import numpy as np

from app.config import DATA_DIR, settings

from app.database.database import SessionLocal
from app.models.entities import Chunk, Paper
from app.security.owner import server_decrypt_blob, server_encrypt_blob
from app.services.embeddings import get_embeddings

_TOKEN_RE = re.compile(r"[a-z0-9]{2,}")
_BM25_STOP = set("""a an the of in on for to with and or is are was were be been this that these those we
our they their it its as by from at into using used use can could may might will would shall should not
no than then also such which who whom what when where how why there here more most other some any each
both paper study work approach method methods results result section propose proposed show shows
shown using based novel data""".split())


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall((text or "").lower()) if t not in _BM25_STOP]


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _corpus_signature(chunk_ids: list, texts: list, paper_ids: list | None = None) -> str:
    """Include content, ownership and row order, not just text lengths."""
    h = hashlib.sha256()
    for cid, pid, value in zip(chunk_ids, paper_ids or [""] * len(chunk_ids), texts):
        for part in (str(cid), str(pid), _text_hash(value)):
            encoded = part.encode("utf-8")
            h.update(len(encoded).to_bytes(8, "big"))
            h.update(encoded)
    return h.hexdigest()


class BM25:
    """Compact BM25 over the chunk corpus (corpora here are small)."""

    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.N = len(docs)
        self.avgdl = sum(len(d) for d in docs) / max(self.N, 1) or 1.0
        self.tfs: list[dict[str, int]] = [{} for _ in docs]
        self.df: dict[str, int] = {}
        for i, doc in enumerate(docs):
            for t in doc:
                self.tfs[i][t] = self.tfs[i].get(t, 0) + 1
            for t in set(doc):
                self.df[t] = self.df.get(t, 0) + 1
        self.idf = {
            t: float(np.log(1 + (self.N - n + 0.5) / (n + 0.5)))
            for t, n in self.df.items()
        }

    def scores(self, query_tokens: list[str]) -> np.ndarray:
        out = np.zeros(self.N, dtype="float32")
        if not query_tokens:
            return out
        for i in range(self.N):
            tf = self.tfs[i]
            dl = sum(tf.values()) or 1
            s = 0.0
            for t in query_tokens:
                f = tf.get(t)
                if not f:
                    continue
                s += self.idf.get(t, 0.0) * (f * (self.k1 + 1)) / (
                    f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            out[i] = s
        return out


_INDEX_DIR = DATA_DIR / "index"
_INDEX_FILE = _INDEX_DIR / "store.pkl"
INDEX_VERSION = 3
ENCODE_BATCH_SIZE = 100
log = logging.getLogger("scholarscout.vector_store")


def _embedding_fingerprint(emb) -> str:
    """A persisted matrix is reusable only with the same encoder contract."""
    return f"{emb.name}:{getattr(emb, 'model_name', '')}:v{INDEX_VERSION}"


def _paper_centroids(matrix: np.ndarray, paper_ids: list[str]) -> dict[str, np.ndarray]:
    by_paper: dict[str, list[int]] = {}
    for i, pid in enumerate(paper_ids):
        by_paper.setdefault(pid, []).append(i)
    result = {}
    for pid, rows in by_paper.items():
        centroid = matrix[rows].mean(axis=0)
        norm = np.linalg.norm(centroid)
        result[pid] = centroid / norm if norm > 0 else centroid
    return result


def _faiss_for(matrix: np.ndarray):
    try:
        import faiss  # type: ignore
        index = faiss.IndexFlatIP(matrix.shape[1])
        index.add(matrix)
        return index
    except Exception:
        return None


class VectorStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._matrix: np.ndarray | None = None      # (N, D), L2-normalized
        self._chunk_ids: list[int] = []
        self._chunk_row: dict[int, int] = {}        # chunk_id -> matrix row
        self._chunk_hashes: list[str] = []
        self._paper_ids: list[str] = []
        self._paper_vec: dict[str, np.ndarray] = {}  # paper-level centroid
        self._backend: str | None = None
        self._embedding = None
        self._fingerprint: str | None = None
        self._loaded = False
        self._rebuilding = False
        self._disk_stale = False
        self._faiss_index = None
        self._bm25: BM25 | None = None

    @property
    def backend(self) -> str:
        with self._lock:
            return self._backend or ""

    def rebuild(self) -> int:
        """Build a complete candidate, then publish it while holding one lock.

        A failed fit/encode leaves the previous matrix, encoder, ids and BM25
        untouched. Searchers block behind the rebuild and see one snapshot.
        """
        if settings.production:
            # Public requests use session-local sparse snapshots. Never fit or
            # persist a shared dense vocabulary over all visitors on Render.
            with self._lock:
                from app.services.embeddings import TfidfEmbeddings
                emb = TfidfEmbeddings()
                self._publish(None, [], [], [], None, {}, emb, None)
                _INDEX_FILE.unlink(missing_ok=True)
                return 0
        with self._lock:
            self._rebuilding = True
            self._disk_stale = False
            try:
                return self._rebuild_locked()
            except Exception:
                # A failed rebuild after a deletion must not leave a persisted
                # index containing the deleted owner's vectors on disk.
                if self._disk_stale:
                    _INDEX_FILE.unlink(missing_ok=True)
                raise
            finally:
                self._rebuilding = False

    def _rebuild_locked(self) -> int:
        emb = get_embeddings()
        db = SessionLocal()
        try:
            chunks = (
                db.query(Chunk, Paper.title)
                .join(Paper, Chunk.paper_id == Paper.id)
                .filter(Paper.status.in_(["ready", "processing"]))
                .all()
            )
        finally:
            db.close()
        texts = [c.Chunk.text or "" for c in chunks]
        ids = [c.Chunk.id for c in chunks]
        pids = [c.Chunk.paper_id for c in chunks]
        hashes = [_text_hash(t) for t in texts]
        if not texts:
            self._matrix = None
            self._chunk_ids = []
            self._chunk_row = {}
            self._chunk_hashes = []
            self._paper_ids = []
            self._paper_vec = {}
            self._bm25 = None
            self._faiss_index = None
            self._backend = emb.name
            self._embedding = emb
            self._fingerprint = _embedding_fingerprint(emb)
            self._loaded = True
            _INDEX_FILE.unlink(missing_ok=True)
            return 0

        signature = _corpus_signature(ids, texts, pids)
        fingerprint = _embedding_fingerprint(emb)
        saved = self._read_saved(emb, fingerprint)
        self._disk_stale = saved is not None and saved["signature"] != signature
        if saved is not None and saved["signature"] == signature and \
                saved["chunk_ids"] == ids and saved["paper_ids"] == pids and \
                saved["chunk_hashes"] == hashes:
            active_emb = saved["vectorizer"] if emb.needs_fit else emb
            self._publish(saved["matrix"], ids, pids, hashes, saved["bm25"],
                          saved["paper_vec"], active_emb, _faiss_for(saved["matrix"]))
            if emb.needs_fit:
                from app.services.embeddings import set_service
                set_service(active_emb)
            return len(ids)

        # Corpus-fitted encoders must be new instances: fitting a live TF-IDF
        # object changes query dimensionality before the new matrix exists.
        candidate = emb.new_instance() if emb.needs_fit and hasattr(emb, "new_instance") else emb
        if candidate is emb and emb.needs_fit:
            import copy
            candidate = copy.deepcopy(emb)
        if candidate.needs_fit:
            candidate.fit(texts)

        try:
            matrix = self._encode_corpus(candidate, texts, hashes, saved)
        except Exception:
            if candidate.name == "tfidf":
                raise
            from app.services.embeddings import TfidfEmbeddings
            candidate = TfidfEmbeddings()
            candidate.fit(texts)
            matrix = self._encode_corpus(candidate, texts, hashes, None)
            saved = None

        self._validate_matrix(matrix, len(ids), candidate)
        bm25 = BM25([_tokenize(t) for t in texts])
        paper_vec = _paper_centroids(matrix, pids)
        faiss_index = _faiss_for(matrix)
        payload = {
            "version": INDEX_VERSION,
            "signature": signature,
            "backend": candidate.name,
            "fingerprint": _embedding_fingerprint(candidate),
            "chunk_ids": ids,
            "paper_ids": pids,
            "chunk_hashes": hashes,
            "matrix": matrix,
            "bm25": bm25,
            "paper_vec": paper_vec,
            "vectorizer": candidate if candidate.needs_fit else None,
        }
        if not self._write_saved(payload) and self._disk_stale:
            _INDEX_FILE.unlink(missing_ok=True)
        self._publish(matrix, ids, pids, hashes, bm25, paper_vec, candidate, faiss_index)
        if candidate.needs_fit:
            if candidate.name == "tfidf" and emb.name != "tfidf":
                from app.services.embeddings import fallback_to_tfidf
                fallback_to_tfidf(candidate)
            else:
                from app.services.embeddings import set_service
                set_service(candidate)
        return len(ids)

    def _publish(self, matrix, ids, pids, hashes, bm25, paper_vec, emb, faiss_index) -> None:
        self._matrix = matrix
        self._chunk_ids = ids
        self._chunk_row = {cid: i for i, cid in enumerate(ids)}
        self._chunk_hashes = hashes
        self._paper_ids = pids
        self._paper_vec = paper_vec
        self._bm25 = bm25
        self._faiss_index = faiss_index
        self._backend = emb.name
        self._embedding = emb
        self._fingerprint = _embedding_fingerprint(emb)
        self._loaded = True

    @staticmethod
    def _validate_matrix(matrix, n: int, emb) -> None:
        if not isinstance(matrix, np.ndarray) or matrix.dtype != np.dtype("float32") or matrix.ndim != 2 or \
                matrix.shape[0] != n or matrix.shape[1] == 0 or \
                not np.issubdtype(matrix.dtype, np.number) or \
                not np.isfinite(matrix).all():
            raise ValueError("Invalid embedding matrix")
        if getattr(emb, "dim", 0) and matrix.shape[1] != int(emb.dim):
            raise ValueError("Encoder and matrix dimensions differ")

    def _read_saved(self, emb, fingerprint: str):
        if not _INDEX_FILE.exists():
            return None
        try:
            saved = pickle.loads(server_decrypt_blob(_INDEX_FILE.read_bytes()))
            if not isinstance(saved, dict) or saved.get("version") != INDEX_VERSION or \
                    saved.get("backend") != emb.name or saved.get("fingerprint") != fingerprint:
                raise ValueError("Index version or backend mismatch")
            ids, pids, hashes = (saved[key] for key in ("chunk_ids", "paper_ids", "chunk_hashes"))
            if not isinstance(ids, list) or not isinstance(pids, list) or \
                    not isinstance(hashes, list) or not ids or len(set(ids)) != len(ids) or \
                    len(ids) != len(pids) or len(ids) != len(hashes) or \
                    not all(isinstance(x, str) and len(x) == 64 for x in hashes):
                raise ValueError("Index row metadata invalid")
            fitted = saved.get("vectorizer") if emb.needs_fit else emb
            if fitted is None or fitted.name != emb.name or \
                    _embedding_fingerprint(fitted) != fingerprint:
                raise ValueError("Index encoder invalid")
            self._validate_matrix(saved["matrix"], len(ids), fitted)
            bm25 = saved["bm25"]
            if not isinstance(bm25, BM25) or bm25.N != len(ids) or len(bm25.tfs) != len(ids):
                raise ValueError("Index BM25 invalid")
            centroids = saved["paper_vec"]
            if not isinstance(centroids, dict) or set(centroids) != set(pids) or any(
                not isinstance(v, np.ndarray) or v.shape != (saved["matrix"].shape[1],) or
                not np.isfinite(v).all() for v in centroids.values()
            ):
                raise ValueError("Index paper vectors invalid")
            return saved
        except Exception as exc:
            log.warning("Discarding invalid encrypted vector index: %s", type(exc).__name__)
            _INDEX_FILE.unlink(missing_ok=True)
            return None

    @staticmethod
    def _write_saved(payload) -> bool:
        try:
            _INDEX_DIR.mkdir(parents=True, exist_ok=True)
            pending = _INDEX_FILE.with_suffix(".tmp")
            pending.write_bytes(server_encrypt_blob(pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)))
            pending.replace(_INDEX_FILE)
            return True
        except Exception:
            log.exception("Could not persist vector index; in-memory index remains usable")
            return False

    def _encode_corpus(self, emb, texts: list[str], hashes: list[str], saved) -> np.ndarray:
        # Corpus-fitted TF-IDF vectors change whenever IDF/vocabulary changes.
        # Reuse is safe only for stateless encoders with a fixed fingerprint.
        cache: dict[str, np.ndarray] = {}
        if not emb.needs_fit:
            if self._matrix is not None and self._backend == emb.name and \
                    self._fingerprint == _embedding_fingerprint(emb):
                cache.update(zip(self._chunk_hashes, self._matrix))
            if saved is not None:
                cache.update(zip(saved["chunk_hashes"], saved["matrix"]))
        missing = []
        seen = set()
        for h, text in zip(hashes, texts):
            if h not in cache and h not in seen:
                missing.append((h, text))
                seen.add(h)
        for start in range(0, len(missing), ENCODE_BATCH_SIZE):
            batch = missing[start:start + ENCODE_BATCH_SIZE]
            began = time.perf_counter()
            vectors = np.asarray(emb.encode([text for _, text in batch]), dtype="float32")
            self._validate_matrix(vectors, len(batch), emb)
            cache.update((h, v) for (h, _), v in zip(batch, vectors))
            log.info("Encoded %d chunks with %s in %.2fs", len(batch), emb.name,
                     time.perf_counter() - began)
        return np.stack([cache[h] for h in hashes]).astype("float32", copy=False)

    def search_lexical(self, query: str, k: int = 12, paper_ids: list[str] | None = None):
        """BM25 lexical search: [(chunk_id, paper_id, raw_score), ...] best first."""
        if k <= 0:
            return []
        try:
            self.rebuild_if_needed()
        except Exception:
            log.exception("Lexical index refresh failed; trying prior intact snapshot")
        with self._lock:
            if self._integrity_issue() or self._bm25 is None:
                return []
            allowed = set(paper_ids) if paper_ids is not None else None
            scores = self._bm25.scores(_tokenize(query))
            chunk_ids = list(self._chunk_ids)
            paper_ids_list = list(self._paper_ids)
        order = np.argsort(-scores)
        out = []
        for j in order:
            if scores[j] <= 0:
                break
            pid = paper_ids_list[j]
            if allowed is not None and pid not in allowed:
                continue
            out.append((chunk_ids[j], pid, float(scores[j])))
            if len(out) >= k:
                break
        return out

    def known_chunk_ids(self) -> set:
        """Chunk ids currently in the index - used to detect staleness."""
        with self._lock:
            return set(self._chunk_ids)

    def vector_by_chunk(self, chunk_id: int) -> np.ndarray | None:
        with self._lock:
            row = self._chunk_row.get(chunk_id)
            if row is None or self._matrix is None:
                return None
            return self._matrix[row].copy()

    def search_chunks(self, query: str, k: int = 6, paper_ids: list[str] | None = None,
                      _retried: bool = False):
        """Returns [(chunk_id, paper_id, score), ...] sorted by cosine similarity.

        The query always uses the encoder published with this matrix. If a
        refresh fails, the last complete snapshot remains available."""
        if k <= 0:
            return []
        try:
            self.rebuild_if_needed()
        except Exception:
            log.exception("Dense index refresh failed; trying prior intact snapshot")

        try:
            with self._lock:
                if self._integrity_issue() or self._matrix is None:
                    return []
                emb = self._embedding
                qv = emb.encode([query])[0]
                matrix = self._matrix
                chunk_ids = self._chunk_ids
                paper_ids_list = self._paper_ids
                faiss_index = self._faiss_index

                if qv.shape[0] != matrix.shape[1]:
                    raise ValueError("query/matrix dimension mismatch - store desynced")

                if paper_ids is not None:
                    allowed = set(paper_ids)
                    mask = [i for i, p in enumerate(paper_ids_list) if p in allowed]
                    if not mask:
                        return []
                    sub = matrix[mask]
                    sims = sub @ qv
                    order = np.argsort(-sims)[:k]
                    return [(chunk_ids[mask[j]], paper_ids_list[mask[j]], float(sims[j])) for j in order]

                if faiss_index is not None:
                    sims, idxs = faiss_index.search(qv.reshape(1, -1), min(k, len(chunk_ids)))
                    return [(chunk_ids[j], paper_ids_list[j], float(s)) for s, j in zip(sims[0], idxs[0]) if j >= 0]
                sims = matrix @ qv
                order = np.argsort(-sims)[:k]
                return [(chunk_ids[j], paper_ids_list[j], float(sims[j])) for j in order]
        except Exception:
            if _retried:
                log.exception("Dense search unavailable after one repair attempt")
                return []  # lexical retrieval still provides evidence
            try:
                self.rebuild()
            except Exception:
                log.exception("Dense index repair failed")
                return []
            return self.search_chunks(query, k=k, paper_ids=paper_ids, _retried=True)

    def similar_papers(self, paper_id: str, k: int = 6):
        """[(other_paper_id, cosine), ...] based on centroid similarity."""
        try:
            self.rebuild_if_needed()
        except Exception:
            log.exception("Paper similarity index refresh failed")
        with self._lock:
            if self._integrity_issue():
                return []
            base = self._paper_vec.get(paper_id)
            if base is None:
                return []
            out = [(pid, float(vec @ base)) for pid, vec in self._paper_vec.items()
                   if pid != paper_id]
        out.sort(key=lambda x: -x[1])
        return out[:k]

    def paper_matrix(self) -> tuple[list[str], np.ndarray] | None:
        """(paper_ids, vectors) for clustering across the library."""
        try:
            self.rebuild_if_needed()
        except Exception:
            log.exception("Paper matrix refresh failed")
        with self._lock:
            if self._integrity_issue() or not self._paper_vec:
                return None
            ids = list(self._paper_vec.keys())
            return ids, np.stack([self._paper_vec[i] for i in ids])

    def rebuild_if_needed(self) -> None:
        # Waiting on this lock is essential: returning while another thread
        # rebuilds lets a search see stale or partially replaced state.
        with self._lock:
            emb = get_embeddings()
            if self._integrity_issue() or self._backend != emb.name or \
                    self._fingerprint != _embedding_fingerprint(emb) or \
                    (emb.needs_fit and self._embedding is not emb):
                self.rebuild()

    def _integrity_issue(self, *, deep: bool = False) -> str | None:
        if not self._loaded:
            return "index has not been loaded"
        if self._embedding is None or self._backend != self._embedding.name or \
                self._fingerprint != _embedding_fingerprint(self._embedding):
            return "encoder identity differs from index"
        if self._matrix is None:
            return None if not self._chunk_ids and not self._paper_ids and self._bm25 is None else \
                "empty index has stale row metadata"
        if not isinstance(self._matrix, np.ndarray) or self._matrix.dtype != np.dtype("float32"):
            return "matrix type invalid"
        n, d = self._matrix.shape if self._matrix.ndim == 2 else (0, 0)
        if not n or not d or n != len(self._chunk_ids) or n != len(self._paper_ids) or \
                n != len(self._chunk_hashes) or len(set(self._chunk_ids)) != n or \
                len(self._chunk_row) != n or any(self._chunk_row.get(cid) != i
                                                  for i, cid in enumerate(self._chunk_ids)):
            return "matrix rows and chunk metadata differ"
        if (deep and not np.isfinite(self._matrix).all()) or \
                (getattr(self._embedding, "dim", 0) and int(self._embedding.dim) != d):
            return "matrix values or encoder dimension invalid"
        if self._bm25 is None or self._bm25.N != n or len(self._bm25.tfs) != n:
            return "BM25 rows differ from matrix"
        if set(self._paper_vec) != set(self._paper_ids) or any(
            not isinstance(v, np.ndarray) or v.shape != (d,) or not np.isfinite(v).all()
            for v in self._paper_vec.values()
        ):
            return "paper centroids differ from matrix"
        return None

    def probe_integrity(self, repair: bool = False) -> dict[str, object]:
        """Startup/health probe. A repair rebuilds or restores a valid index."""
        with self._lock:
            issue = self._integrity_issue(deep=True)
            if issue and repair:
                try:
                    self.rebuild()
                    issue = self._integrity_issue(deep=True)
                except Exception as exc:
                    issue = f"rebuild failed: {type(exc).__name__}"
            return {"ok": issue is None, "issue": issue,
                    "backend": self._backend or "", "chunk_count": len(self._chunk_ids)}

    def integrity_status(self) -> dict[str, object]:
        """Cheap, read-only status for health checks (no DB or full matrix scan)."""
        with self._lock:
            issue = self._integrity_issue()
            return {"ok": issue is None, "issue": issue,
                    "backend": self._backend or "", "chunk_count": len(self._chunk_ids)}


store = VectorStore()

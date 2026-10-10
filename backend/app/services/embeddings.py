"""Embedding backends with graceful degradation.

1. sentence-transformers (all-MiniLM-L6-v2)  → true semantic embeddings
2. scikit-learn TF-IDF (word 1-2 grams + char 3-5 grams)
   → lexical-fuzzy retrieval; still powers useful semantic-ish search and
   requires no model download.

Both produce L2-normalized vectors so cosine similarity is a dot product.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time

import numpy as np
import httpx
from sklearn.feature_extraction.text import TfidfVectorizer
from app.config import settings

_lock = threading.Lock()
_service = None


class TransformerEmbeddings:
    name = "sentence-transformers"
    model_name = "all-MiniLM-L6-v2"
    needs_fit = False

    def __init__(self) -> None:
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(self.model_name)
        # sanity check tuned to our retrieval use case: a trained model must
        # rank a question against a relevant statement well above an unrelated
        # one. Corrupt/stub weight files produce random-space embeddings -
        # reject them so we fall back to TF-IDF.
        probe = self.model.encode(
            [
                "What is this paper about?",
                "We propose the Transformer, a new model architecture based solely on attention mechanisms.",
                "The experiment was conducted in a laboratory in Berlin.",
            ],
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        related = float(probe[0] @ probe[1])
        unrelated = float(probe[0] @ probe[2])
        if related < 0.2 or related <= unrelated + 0.08:
            raise RuntimeError(
                f"transformer embeddings failed sanity check "
                f"(related={related:.3f}, unrelated={unrelated:.3f})"
            )

    @property
    def dim(self) -> int:
        return int(self.model.get_sentence_embedding_dimension())

    def encode(self, texts: list[str]) -> np.ndarray:
        vecs = self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return np.asarray(vecs, dtype="float32")


class TfidfEmbeddings:
    name = "tfidf"
    needs_fit = True

    def __init__(self) -> None:
        from sklearn.pipeline import FeatureUnion
        self.union = FeatureUnion([
            ("word", TfidfVectorizer(ngram_range=(1, 2), max_features=settings.tfidf_word_max_features, sublinear_tf=True)),
            ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), max_features=settings.tfidf_char_max_features, sublinear_tf=True)),
        ])
        self._dim: int | None = None
        self._fitted = False

    def new_instance(self) -> "TfidfEmbeddings":
        """Build a corpus-fitted candidate without mutating the live encoder."""
        return TfidfEmbeddings()

    def fit(self, texts: list[str]) -> None:
        if not texts:
            return
        self.union.fit(texts)
        self._fitted = True
        self._dim = self.union.transform(texts[:1]).shape[1]

    @property
    def dim(self) -> int:
        return self._dim or 0

    def encode(self, texts: list[str]) -> np.ndarray:
        import scipy.sparse as sp
        m = self.union.transform(texts)
        if sp.issparse(m):
            m = m.toarray()
        m = np.asarray(m, dtype="float32")
        norms = np.linalg.norm(m, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return m / norms


class GeminiEmbeddings:
    """Legacy implementation, intentionally excluded from backend selection.

    Anonymous session consent cannot currently cover shared search indexing.
    """

    name = "gemini-embed"
    needs_fit = False

    def __init__(self) -> None:
        if not settings.gemini_api_key:
            raise RuntimeError("no GEMINI_API_KEY")
        self.dim = 768
        cache_dir = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "..", "data", "index")
        cache_dir = os.path.normpath(cache_dir)
        os.makedirs(cache_dir, exist_ok=True)
        self._db = sqlite3.connect(os.path.join(cache_dir, "emb_cache.db"),
                                   check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS emb_cache "
                         "(k TEXT PRIMARY KEY, v BLOB)")
        self._db.commit()
        self._lock = threading.Lock()

    @staticmethod
    def _hash(text: str) -> str:
        import hashlib
        return hashlib.sha1(text.strip().lower().encode()).hexdigest()

    model = "gemini-embedding-001"

    def _embed_batch(self, texts: list[str]) -> list:
        r = httpx.post(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:batchEmbedContents",
            headers={"x-goog-api-key": settings.gemini_api_key or ""},
            json={"requests": [
                {"model": f"models/{self.model}",
                 "content": {"parts": [{"text": t[:6000]}]}} for t in texts
            ]},
            timeout=60,
        )
        r.raise_for_status()
        embs = r.json().get("embeddings") or []
        if len(embs) != len(texts):
            raise ValueError(f"gemini embeddings: got {len(embs)} of {len(texts)}")
        vecs = [e.get("values") for e in embs]
        if any(not v for v in vecs):
            raise ValueError("gemini embeddings: empty vector in batch")
        out = []
        for v in vecs:
            arr = np.asarray(v, dtype="float32")
            n = np.linalg.norm(arr)
            out.append(arr / n if n > 0 else arr)
        return out

    def encode(self, texts: list) -> np.ndarray:
        keys = [self._hash(t) for t in texts]
        vecs: dict[int, np.ndarray] = {}
        missing: list[int] = []
        with self._lock:
            cur = self._db
            for i, k in enumerate(keys):
                row = cur.execute("SELECT v FROM emb_cache WHERE k = ?", (k,)).fetchone()
                if row:
                    vecs[i] = np.frombuffer(row[0], dtype="float32")
                else:
                    missing.append(i)
        # fetch uncached vectors in batches of 16, paced to respect the
        # free-tier rate limit (~100 requests/min) - ~1.4s between batches
        for start in range(0, len(missing), 16):
            batch_idx = missing[start:start + 16]
            batch_texts = [texts[i] for i in batch_idx]
            if start > 0:
                time.sleep(1.4)
            got = self._embed_batch(batch_texts)
            with self._lock:
                for i, v in zip(batch_idx, got):
                    vecs[i] = v
                    self._db.execute("INSERT OR REPLACE INTO emb_cache VALUES (?, ?)",
                                     (keys[i], v.tobytes()))
            self._db.commit()
        out = np.stack([vecs[i] for i in range(len(texts))])
        return out


def embedding_backend_name() -> str:
    return get_embeddings().name


def purge_embedding_cache_by_hash(hashes: list) -> int:
    """Delete cached Gemini embedding vectors derived from a user's chunk
    texts - called when that user deletes their papers. Hashes are the same
    sha1(text.strip().lower()) used when storing."""
    d = os.path.normpath(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "..", "data", "index"))
    path = os.path.join(d, "emb_cache.db")
    if not os.path.exists(path) or not hashes:
        return 0
    con = sqlite3.connect(path)
    n = 0
    for h in hashes:
        n += con.execute("DELETE FROM emb_cache WHERE k = ?", (h,)).rowcount
    con.commit()
    con.close()
    return n


def emb_hash(text: str) -> str:
    import hashlib
    import hashlib as _h
    return _h.sha1(text.strip().lower().encode()).hexdigest()


def set_service(instance) -> None:
    """Restore a persisted fitted instance (used by the vector store cache)."""
    global _service
    with _lock:
        _service = instance


def fallback_to_tfidf(fitted_instance) -> None:
    """Called when a live encode fails (rate limit/network): pin TF-IDF as the
    active service so the store and service agree, and start the retry timer
    so the preferred backend is re-probed after 10 minutes."""
    global _service, _fallback_since
    with _lock:
        _service = fitted_instance
        _fallback_since = time.time()
        _save_choice(_service.name)


_fallback_since: float | None = None  # when TF-IDF took over as fallback
_RETRY_AFTER_S = 600  # re-probe the preferred backend every 10 minutes


class SanityError(RuntimeError):
    """Deterministic backend failure (bad weights/outputs) - worth remembering.
    Transient errors (network/429/quota) must NOT be remembered."""


def get_embeddings():
    """Use local sentence-transformers when available, otherwise local TF-IDF."""
    global _service, _fallback_since
    with _lock:
        if settings.production:
            if not isinstance(_service, TfidfEmbeddings):
                _service = TfidfEmbeddings()
            return _service
        if _service is not None and _fallback_since is not None:
            if time.time() - _fallback_since > _RETRY_AFTER_S:
                _service = None  # time to re-probe the preferred backends
                _fallback_since = None
        if _service is None:
            failures = _load_failures()

            def _try(builder):
                inst = builder()
                if hasattr(inst, "sanity_check"):
                    inst.sanity_check()
                return inst

            if "sentence-transformers" not in failures:
                try:
                    inst = TransformerEmbeddings()
                    # __init__ runs its own internal probe; a pass lands here
                    _service = inst
                except Exception as e:
                    if isinstance(e, SanityError) or "sanity" in str(e).lower():
                        failures.add("sentence-transformers")
                        _save_failures(failures)
            if _service is None:
                _service = TfidfEmbeddings()
                _fallback_since = time.time()
            else:
                _fallback_since = None
            _save_choice(_service.name)
        return _service


def _failures_path():
    d = os.path.normpath(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "..", "data"))
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "embedding_failures.txt")


def _load_failures() -> set:
    try:
        with open(_failures_path()) as f:
            return {line.strip() for line in f if line.strip()}
    except Exception:
        return set()


def _save_failures(failures: set) -> None:
    try:
        with open(_failures_path(), "w") as f:
            f.write("\n".join(sorted(failures)))
    except Exception:
        pass


def _save_choice(name: str) -> None:
    try:
        d = os.path.normpath(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "..", "data"))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "embedding_backend.txt"), "w") as f:
            f.write(name)
    except Exception:
        pass


SIMILARITY_FLOOR = {"sentence-transformers": 0.22, "tfidf": 0.05}

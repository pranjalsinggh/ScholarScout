"""Fault injection for vector-index transactions and incremental encoding."""
from __future__ import annotations

import pickle
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from app.security.owner import server_decrypt_blob, server_encrypt_blob
from app.services import embeddings as embedding_module
from app.services import vector_store as vs


class FakeSession:
    def __init__(self, rows):
        self.rows = rows

    def query(self, *args):
        return self

    def join(self, *args):
        return self

    def filter(self, *args):
        return self

    def all(self):
        return [SimpleNamespace(Chunk=SimpleNamespace(id=cid, paper_id=pid, text=text),
                                title=pid) for cid, pid, text in self.rows]

    def close(self):
        pass


class CountingEmbeddings:
    name = "counting-test"
    needs_fit = False
    dim = 3

    def __init__(self):
        self.encoded = []
        self.fail = False
        self.entered = None
        self.release = None

    def encode(self, texts):
        if self.entered is not None and any("beta findings" in t for t in texts):
            self.entered.set()
            assert self.release.wait(5)
        if self.fail and any("beta findings" in t for t in texts):
            raise RuntimeError("injected batch failure")
        self.encoded.extend(texts)
        out = []
        for t in texts:
            a = np.array([t.lower().count("alpha"), t.lower().count("beta"), 1], dtype="float32")
            out.append(a / np.linalg.norm(a))
        return np.stack(out)


@pytest.fixture
def isolated_store(monkeypatch, tmp_path):
    rows = [(1, "paper-a", "alpha findings")]
    emb = CountingEmbeddings()
    monkeypatch.setattr(vs, "SessionLocal", lambda: FakeSession(rows))
    monkeypatch.setattr(vs, "get_embeddings", lambda: emb)
    monkeypatch.setattr(vs, "_INDEX_DIR", tmp_path)
    monkeypatch.setattr(vs, "_INDEX_FILE", tmp_path / "store.pkl")
    return vs.VectorStore(), rows, emb, tmp_path / "store.pkl"


def test_failed_rebuild_preserves_searchable_old_snapshot(isolated_store):
    store, rows, emb, path = isolated_store
    emb.name = "tfidf"  # fitted-backend failure must not silently change engines
    assert store.rebuild() == 1
    assert store.search_chunks("alpha", paper_ids=["paper-a"])[0][0] == 1
    rows.append((2, "paper-b", "beta findings"))
    emb.fail = True
    with pytest.raises(RuntimeError, match="injected"):
        store.rebuild()
    assert not path.exists()  # old corpus must not persist after a failed refresh
    assert store.probe_integrity(repair=False)["ok"]
    assert store.search_chunks("alpha", paper_ids=["paper-a"])[0][0] == 1


def test_search_waits_for_rebuild_and_uses_complete_new_index(isolated_store):
    store, rows, emb, _ = isolated_store
    store.rebuild()
    rows.append((2, "paper-b", "beta findings"))
    emb.entered, emb.release = threading.Event(), threading.Event()
    rebuilt = threading.Thread(target=store.rebuild)
    rebuilt.start()
    assert emb.entered.wait(5)
    output = []
    searched = threading.Thread(target=lambda: output.extend(store.search_chunks("beta", paper_ids=["paper-b"])))
    searched.start()
    emb.release.set()
    rebuilt.join(5)
    searched.join(5)
    assert not rebuilt.is_alive() and not searched.is_alive()
    assert output and output[0][0] == 2
    assert store.probe_integrity(repair=False)["ok"]


def test_corrupt_disk_index_is_discarded(isolated_store):
    store, _, emb, path = isolated_store
    path.write_bytes(b"corrupt and not encrypted")
    assert store.rebuild() == 1
    assert emb.encoded == ["alpha findings"]
    assert server_decrypt_blob(path.read_bytes())
    fresh = vs.VectorStore()
    fresh.rebuild()
    assert emb.encoded == ["alpha findings"]  # validated disk restore
    assert fresh.probe_integrity(repair=False)["ok"]


def test_index_version_mismatch_forces_reencode(isolated_store):
    store, _, emb, path = isolated_store
    store.rebuild()
    payload = pickle.loads(server_decrypt_blob(path.read_bytes()))
    payload["version"] = -1
    path.write_bytes(server_encrypt_blob(pickle.dumps(payload)))
    vs.VectorStore().rebuild()
    assert emb.encoded.count("alpha findings") == 2


def test_unchanged_stateless_chunks_are_not_reencoded(isolated_store):
    store, rows, emb, _ = isolated_store
    store.rebuild()
    rows.append((2, "paper-b", "beta findings"))
    store.rebuild()
    assert emb.encoded == ["alpha findings", "beta findings"]
    assert store.probe_integrity(repair=False)["ok"]


def test_startup_probe_repairs_inconsistent_memory(isolated_store):
    store, _, _, _ = isolated_store
    store.rebuild()
    store._matrix = np.zeros((2, 3), dtype="float32")
    assert not store.probe_integrity(repair=False)["ok"]
    assert store.probe_integrity(repair=True)["ok"]


def test_tfidf_failed_refit_does_not_mutate_live_encoder(isolated_store, monkeypatch):
    store, rows, _, _ = isolated_store
    active = [embedding_module.TfidfEmbeddings()]
    monkeypatch.setattr(vs, "get_embeddings", lambda: active[0])
    monkeypatch.setattr(embedding_module, "set_service", lambda instance: active.__setitem__(0, instance))
    store.rebuild()
    prior_encoder = active[0]
    prior_shape = store._matrix.shape
    rows.append((2, "paper-b", "beta findings"))
    original_encode = embedding_module.TfidfEmbeddings.encode

    def fail_new_batch(self, texts):
        if "beta findings" in texts:
            raise RuntimeError("injected TF-IDF failure")
        return original_encode(self, texts)

    monkeypatch.setattr(embedding_module.TfidfEmbeddings, "encode", fail_new_batch)
    with pytest.raises(RuntimeError, match="injected TF-IDF"):
        store.rebuild()
    assert active[0] is prior_encoder
    assert store._matrix.shape == prior_shape
    assert store.search_chunks("alpha", paper_ids=["paper-a"])[0][0] == 1


def test_same_length_text_edit_invalidates_index(isolated_store):
    store, rows, emb, _ = isolated_store
    store.rebuild()
    rows[0] = (1, "paper-a", "omega findings")  # same character count
    vs.VectorStore().rebuild()
    assert emb.encoded == ["alpha findings", "omega findings"]


def test_persisted_stateless_vectors_reused_on_next_upload(isolated_store):
    store, rows, emb, _ = isolated_store
    store.rebuild()
    rows.append((2, "paper-b", "beta findings"))
    vs.VectorStore().rebuild()  # simulate process restart
    assert emb.encoded == ["alpha findings", "beta findings"]


def test_hybrid_retrieval_runs_one_search_per_leg(monkeypatch):
    from app.rag import retriever

    class FakeStore:
        dense = lexical = 0

        def rebuild_if_needed(self):
            pass

        def search_chunks(self, *args, **kwargs):
            self.dense += 1
            return [(1, "paper-a", 0.9)]

        def search_lexical(self, *args, **kwargs):
            self.lexical += 1
            return [(1, "paper-a", 1.2)]

        def vector_by_chunk(self, *args):
            return None

    fake = FakeStore()
    monkeypatch.setattr(retriever, "store", fake)
    monkeypatch.setattr(retriever, "learned_adjustments", lambda paper_id: ({}, {}))
    monkeypatch.setattr(retriever, "_load_rows", lambda ids: {
        1: ("alpha findings", "Abstract", 1, "Paper A", "paper-a")})
    hits = retriever.retrieve("alpha", k=1)
    assert hits and hits[0].chunk_id == 1
    assert (fake.dense, fake.lexical) == (1, 1)


def test_real_database_rows_build_searchable_index(isolated_store, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.database.database import Base
    from app.models.entities import Chunk, Paper

    store, _, emb, _ = isolated_store
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)
    monkeypatch.setattr(vs, "SessionLocal", session)
    db = session()
    try:
        db.add(Paper(id="paper-a", title="Paper A", status="ready"))
        db.add(Chunk(id=1, paper_id="paper-a", text="alpha findings", section_name="Abstract"))
        db.commit()
        assert store.rebuild() == 1
        assert store.search_chunks("alpha", paper_ids=["paper-a"])[0][0] == 1
        assert store.search_lexical("alpha", paper_ids=["paper-a"])[0][0] == 1
        db.add(Chunk(id=2, paper_id="paper-a", text="beta findings", section_name="Results"))
        db.commit()
        store.rebuild()
        assert [value for value in emb.encoded if value.endswith(" findings")] == \
            ["alpha findings", "beta findings"]
    finally:
        db.close()
        engine.dispose()

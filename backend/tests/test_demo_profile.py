"""Demo loading remains bounded, private and available without a bundled PDF."""
from __future__ import annotations

from pathlib import Path
import secrets

import pymupdf
import pytest

from fastapi.testclient import TestClient

from app.api import routes
from app.config import settings
from app.database.database import SessionLocal
from app.main import app
from app.models.entities import Paper
from app.security import outbound
from app.security.owner import decrypt_bytes_from_raw, owner_hash, unwrap_file_key
from app.services import jobs


@pytest.fixture
def demo_client(monkeypatch, tmp_path):
    """Keep every demo test offline and release each fake worker reservation."""
    monkeypatch.setattr(routes, "_DEMO_PDF_PATH", tmp_path / "missing-demo.pdf")
    monkeypatch.setattr(settings, "demo_autoload", False)
    monkeypatch.setattr(routes, "rate_limit", lambda *args: None)
    def no_network(*args, **kwargs):
        raise AssertionError("Demo tests must mock the public download.")
    monkeypatch.setattr(outbound, "download_public_pdf", no_network)
    permits, submissions = [], []

    class Permit:
        releases = 0

        def release(self):
            self.releases += 1

    def reserve(owner):
        permit = Permit()
        permits.append(permit)
        return permit

    def submit(paper_id, token, **kwargs):
        submissions.append((paper_id, token, kwargs["owner"]))
        kwargs["permit"].release()
        return True

    monkeypatch.setattr(jobs, "reserve_analysis", reserve)
    monkeypatch.setattr(jobs, "submit_analysis", submit)
    token = "ss_" + secrets.token_urlsafe(32)
    client = TestClient(app, headers={"X-Owner-Token": token})
    try:
        yield client, token, permits, submissions
    finally:
        client.delete("/api/me/purge")
        client.close()


@pytest.fixture
def demo_pdf():
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), "Synthetic ScholarScout demo test")
        return doc.tobytes()


def test_demo_autoload_is_scoped_to_anonymous_owner(demo_client, demo_pdf, monkeypatch):
    client, _, permits, submissions = demo_client
    monkeypatch.setattr(settings, "demo_autoload", True)
    downloaded = []

    def download(url, max_bytes):
        downloaded.append(url)
        return demo_pdf

    monkeypatch.setattr(outbound, "download_public_pdf", download)
    first = client.get("/api/papers")
    assert first.status_code == 200, first.text
    assert len(first.json()) == 1
    assert first.json()[0]["title"] == "Attention Is All You Need"
    assert len(client.get("/api/papers").json()) == 1
    other = TestClient(app, headers={"X-Owner-Token": "ss_" + secrets.token_urlsafe(32)})
    try:
        second = other.get("/api/papers")
        assert second.status_code == 200, second.text
        assert len(second.json()) == 1
        assert second.json()[0]["id"] != first.json()[0]["id"]
        assert other.get(f"/api/papers/{first.json()[0]['id']}").status_code == 404
    finally:
        other.delete("/api/me/purge")
        other.close()
    assert downloaded == [routes._DEMO_PDF_URL] * 2
    assert len(submissions) == 2
    assert all(permit.releases == 1 for permit in permits)


def test_missing_demo_downloads_after_reservation_encrypts_and_reuses(demo_client, demo_pdf, monkeypatch):
    client, token, permits, submissions = demo_client
    calls = []

    def download(url, max_bytes):
        assert len(permits) == 1 and permits[0].releases == 0
        calls.append((url, max_bytes))
        return demo_pdf

    monkeypatch.setattr(outbound, "download_public_pdf", download)
    first = client.post("/api/papers/demo")
    assert first.status_code == 200, first.text
    paper_id = first.json()["id"]
    with SessionLocal() as db:
        paper = db.get(Paper, paper_id)
        assert paper.owner == owner_hash(token)
        encrypted = Path(paper.file_path)
        assert encrypted.suffix == ".enc" and encrypted.read_bytes() != demo_pdf
        key = unwrap_file_key(token, paper.file_key_w1, paper.file_key_w2)
        assert decrypt_bytes_from_raw(key, str(encrypted)) == demo_pdf
    second = client.post("/api/papers/demo")
    assert second.status_code == 200 and second.json()["id"] == paper_id
    assert calls == [(routes._DEMO_PDF_URL, settings.max_upload_mb * 1024 * 1024)]
    assert submissions == [(paper_id, token, owner_hash(token))]
    assert len(permits) == 1 and permits[0].releases == 1


def test_demo_download_failure_releases_reservation_without_publishing(demo_client, monkeypatch):
    client, _, permits, submissions = demo_client

    def fail(*args):
        raise TimeoutError("private internal diagnostic")

    monkeypatch.setattr(outbound, "download_public_pdf", fail)
    response = client.post("/api/papers/demo")
    assert response.status_code == 502
    assert "upload" in response.json()["detail"]
    assert "private internal" not in response.text
    assert client.get("/api/papers").json() == []
    assert submissions == [] and permits[0].releases == 1


@pytest.mark.parametrize("content", [b"<html>Not a PDF</html>", b"%PDF" + b"x" * (1024 * 1024)],
                         ids=["invalid-format", "too-large"])
def test_demo_rejects_invalid_or_oversized_source(demo_client, monkeypatch, content):
    client, _, permits, submissions = demo_client
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    monkeypatch.setattr(outbound, "download_public_pdf", lambda *args: content)
    response = client.post("/api/papers/demo")
    assert response.status_code == 502
    assert client.get("/api/papers").json() == []
    assert submissions == [] and permits[0].releases == 1


def test_local_demo_still_works_without_a_download(demo_client, demo_pdf):
    client, _, permits, submissions = demo_client
    routes._DEMO_PDF_PATH.write_bytes(demo_pdf)
    response = client.post("/api/papers/demo")
    assert response.status_code == 200, response.text
    assert len(submissions) == 1 and permits[0].releases == 1


def test_demo_admission_failure_never_downloads(demo_client, monkeypatch):
    client, _, permits, submissions = demo_client

    def refuse(*args):
        raise jobs.CapacityError("Analysis capacity is busy.")

    monkeypatch.setattr(jobs, "reserve_analysis", refuse)
    response = client.post("/api/papers/demo")
    assert response.status_code == 429 and response.headers["Retry-After"] == "10"
    assert permits == [] and submissions == []

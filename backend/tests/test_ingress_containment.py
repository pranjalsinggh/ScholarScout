"""Admission fairness, native-parser containment, and DNS-pinned downloads."""
import socket
import ssl
import subprocess
from types import SimpleNamespace

import pytest

from app.config import settings
from app.ingestion import pdf_extractor
from app.security import outbound
from app.services.jobs import CapacityError, FairJobScheduler


def test_reservations_bound_running_pending_and_each_owner():
    scheduler = FairJobScheduler(capacity=3, owner_capacity=2, start=False)
    first, second = scheduler.reserve("owner-a"), scheduler.reserve("owner-a")
    with pytest.raises(CapacityError):
        scheduler.reserve("owner-a")
    third = scheduler.reserve("owner-b")
    with pytest.raises(CapacityError):
        scheduler.reserve("owner-c")
    first.release()
    first.release()
    replacement = scheduler.reserve("owner-c")
    assert sum(scheduler._admitted.values()) == 3
    for permit in (second, third, replacement):
        permit.release()
    assert scheduler._admitted == {}


def test_round_robin_keeps_another_worker_available_for_other_owner():
    scheduler = FairJobScheduler(capacity=4, owner_capacity=2, start=False)
    scheduler.submit(lambda: None, owner="owner-a")
    scheduler.submit(lambda: None, owner="owner-a")
    scheduler.submit(lambda: None, owner="owner-b")
    first = scheduler._next_job()
    second = scheduler._next_job()
    assert first.permit.owner == "owner-a"
    assert second.permit.owner == "owner-b"
    assert scheduler._next_job() is None  # A's second job waits for A's first
    scheduler._active.discard("owner-a")
    first.permit.release()
    assert scheduler._next_job().permit.owner == "owner-a"


def test_cancel_queued_owner_frees_capacity_without_cancelling_other_owner():
    scheduler = FairJobScheduler(capacity=2, owner_capacity=2, start=False)
    cancelled = []
    scheduler.submit(lambda: None, owner="owner-a", on_cancel=lambda: cancelled.append("a"))
    scheduler.submit(lambda: None, owner="owner-b")
    scheduler.cancel_owner("owner-a")
    assert cancelled == ["a"]
    assert scheduler._admitted == {"owner-b": 1}
    assert scheduler._next_job().permit.owner == "owner-b"


def test_timeout_kills_and_reaps_parser_without_inheriting_provider_keys(monkeypatch, tmp_path):
    captured = {}
    class Child:
        killed = False
        waits = 0
        def __init__(self, command, **kwargs):
            captured.update(kwargs)
        def wait(self, timeout=None):
            self.waits += 1
            if not self.killed:
                raise subprocess.TimeoutExpired("native PDF parser", timeout)
            return -1
        def kill(self):
            self.killed = True
        def poll(self):
            return -1 if self.killed else None
    monkeypatch.setenv("GEMINI_API_KEY", "must-not-inherit")
    monkeypatch.setattr(pdf_extractor.subprocess, "Popen", Child)
    import app.config as config
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path)
    with pytest.raises(pdf_extractor.PDFLimitError, match="too long"):
        pdf_extractor.extract_pdf(str(tmp_path / "paper.pdf"))
    assert "GEMINI_API_KEY" not in captured["env"]
    assert pdf_extractor._parsers == {}
    assert list(tmp_path.iterdir()) == []


def test_session_erasure_kills_and_reaps_only_that_owners_active_parser():
    class Child:
        killed = False
        reaped = False
        def poll(self): return -1 if self.killed else None
        def kill(self): self.killed = True
        def wait(self, timeout=None): self.reaped = True
    first, second = Child(), Child()
    pdf_extractor._parsers["owner-a"] = {first}
    pdf_extractor._parsers["owner-b"] = {second}
    try:
        pdf_extractor.cancel_owner_parsers("owner-a")
        assert first.killed and first.reaped
        assert not second.killed and not second.reaped
    finally:
        pdf_extractor._parsers.pop("owner-a", None)
        pdf_extractor._parsers.pop("owner-b", None)


def test_real_isolated_parser_preserves_text_and_rejects_page_budget(monkeypatch, tmp_path):
    path = tmp_path / "document.pdf"
    document = pdf_extractor.fitz.open()
    document.new_page().insert_text((60, 60), "A useful research document about safe scientific evidence.", fontsize=14)
    document.new_page().insert_text((60, 60), "The second page contains another sufficiently substantial source passage.")
    document.save(path)
    document.close()
    import app.config as config
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path)
    extracted = pdf_extractor.extract_pdf(str(path))
    assert extracted.page_count == 2
    assert "safe scientific evidence" in extracted.full_text
    monkeypatch.setattr(settings, "max_pdf_pages", 1)
    with pytest.raises(pdf_extractor.PDFLimitError, match="page limit"):
        pdf_extractor.extract_pdf(str(path))
    assert not list(tmp_path.glob("parse-*-result-*"))


def test_native_extraction_enforces_total_text_budget(tmp_path):
    path = tmp_path / "many-lines.pdf"
    document = pdf_extractor.fitz.open()
    page = document.new_page()
    for number in range(30):
        page.insert_text((40, 40 + number * 15), "Evidence from the source document supports careful research conclusions.", fontsize=9)
    document.save(path)
    document.close()
    # A single oversized page may trip the page-text guard before the total
    # document guard; both enforce this same bounded text allowance.
    with pytest.raises(pdf_extractor.PDFLimitError, match="text"):
        pdf_extractor._extract_pdf_in_process(str(path), max_text_chars=1000)


def test_https_connection_pins_checked_address_with_hostname_tls(monkeypatch):
    connected, verified = [], []
    class Raw:
        def settimeout(self, value): pass
        def connect(self, address): connected.append(address)
        def close(self): pass
    class TLS:
        check_hostname = True
        verify_mode = ssl.CERT_REQUIRED
        def wrap_socket(self, raw, server_hostname):
            verified.append(server_hostname)
            return raw
    monkeypatch.setattr(outbound.socket, "socket", lambda *_args: Raw())
    monkeypatch.setattr(outbound.ssl, "create_default_context", lambda: TLS())
    checked = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443))]
    connection = outbound._PinnedHTTPSConnection("controlled.example", checked, 5)
    connection.connect()
    assert connected == [("93.184.216.34", 443)]
    assert verified == ["controlled.example"]


def test_redirect_to_private_destination_is_rejected_before_connection(monkeypatch):
    calls = []
    def dns(host, *_args, **_kwargs):
        address = "127.0.0.1" if host == "127.0.0.1" else "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 443))]
    class Connection:
        sock = None
        def __init__(self, host, *_args, **_kwargs): calls.append(host)
        def request(self, *_args, **_kwargs): pass
        def getresponse(self):
            return SimpleNamespace(status=302, getheader=lambda _: "https://127.0.0.1/private.pdf")
        def close(self): pass
    monkeypatch.setattr(outbound.socket, "getaddrinfo", dns)
    monkeypatch.setattr(outbound, "_PinnedHTTPSConnection", Connection)
    with pytest.raises(ValueError, match="not public"):
        outbound.download_public_pdf("https://public.example/document.pdf", 100)
    assert calls == ["public.example"]


def test_streamed_download_stops_at_limit_even_without_content_length(monkeypatch):
    class Response:
        status = 200
        def getheader(self, _name): return None
        def read1(self, count): return b"x" * count
    class Connection:
        sock = None
        def __init__(self, *_args, **_kwargs): pass
        def request(self, *_args, **_kwargs): pass
        def getresponse(self): return Response()
        def close(self): pass
    checked = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443))]
    monkeypatch.setattr(outbound, "_resolve_public_https", lambda url, **_: (outbound.urlsplit(url), checked))
    monkeypatch.setattr(outbound, "_PinnedHTTPSConnection", Connection)
    with pytest.raises(ValueError, match="upload limit"):
        outbound.download_public_pdf("https://public.example/document.pdf", 10)

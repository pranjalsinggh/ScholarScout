"""Bounded HTTPS downloads for PDF ingestion."""
from __future__ import annotations

import ipaddress
import http.client
import socket
import ssl
import time
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from urllib.parse import urljoin, urlsplit


_dns_workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pdf-dns")
_dns_slots = threading.BoundedSemaphore(2)


def _resolve_public_https(url: str, *, deadline=None):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Remote PDF URL must be public HTTPS.")
    if parsed.port not in (None, 443):
        raise ValueError("Remote PDF URL uses an unsupported port.")
    if not _dns_slots.acquire(blocking=False):
        raise ValueError("Remote PDF host resolution is busy. Please retry.")
    try:
        lookup = _dns_workers.submit(socket.getaddrinfo, parsed.hostname, 443,
                                     type=socket.SOCK_STREAM)
    except BaseException:
        _dns_slots.release()
        raise
    lookup.add_done_callback(lambda _: _dns_slots.release())
    try:
        remaining = 5 if deadline is None else max(.01, min(5, deadline - time.monotonic()))
        addresses = lookup.result(timeout=remaining)
    except (OSError, FutureTimeout) as exc:
        raise ValueError("Could not resolve the remote PDF host.") from exc
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("Remote PDF host is not public.")
    return parsed, addresses


def _check_public_https(url: str) -> None:
    _resolve_public_https(url)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect only to the exact checked IPs; retain hostname TLS verification."""
    def __init__(self, hostname, addresses, timeout, deadline=None):
        super().__init__(hostname, 443, timeout=timeout, context=ssl.create_default_context())
        self._addresses = addresses
        self._deadline = deadline or time.monotonic() + timeout

    def connect(self):
        failure = None
        for family, socktype, protocol, _, sockaddr in self._addresses:
            # No hostname lookup is performed here. A changed DNS answer cannot
            # redirect this connection to a local network or metadata endpoint.
            raw = socket.socket(family, socktype, protocol)
            try:
                remaining = self._deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Remote PDF connection timed out")
                raw.settimeout(min(self.timeout, remaining))
                raw.connect(sockaddr)
                remaining = self._deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Remote PDF connection timed out")
                raw.settimeout(min(self.timeout, remaining))
                self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
                return
            except OSError as error:
                failure = error
                raw.close()
        if failure:
            raise failure
        raise ValueError("Could not connect to the validated remote PDF host.")


def download_public_pdf(url: str, max_bytes: int, *, timeout_seconds: float = 25) -> bytes:
    """Validate every redirect and stop reading before a large body is buffered."""
    deadline = time.monotonic() + timeout_seconds
    for _ in range(5):
        parsed, addresses = _resolve_public_https(url, deadline=deadline)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("Remote PDF download timed out.")
        connection = _PinnedHTTPSConnection(parsed.hostname, addresses, min(10, remaining), deadline=deadline)
        try:
            target = parsed.path or "/"
            if parsed.query:
                target += "?" + parsed.query
            connection.request("GET", target, headers={"Accept": "application/pdf",
                                                       "Accept-Encoding": "identity"})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("location")
                if not location:
                    raise ValueError("Remote PDF redirect has no destination.")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise ValueError("The remote PDF source did not return a successful download.")
            encoding = (response.getheader("content-encoding") or "identity").lower()
            if encoding != "identity":
                raise ValueError("The remote PDF source returned an unsupported transfer encoding.")
            declared = response.getheader("content-length")
            if declared and int(declared) > max_bytes:
                raise ValueError("Remote PDF exceeds the upload limit.")
            content = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("Remote PDF download timed out.")
                if connection.sock:
                    connection.sock.settimeout(min(5, remaining))
                part = response.read1(min(65536, max_bytes + 1 - len(content)))
                if not part:
                    return bytes(content)
                content.extend(part)
                if len(content) > max_bytes:
                    raise ValueError("Remote PDF exceeds the upload limit.")
        finally:
            connection.close()
    raise ValueError("Remote PDF redirected too many times.")

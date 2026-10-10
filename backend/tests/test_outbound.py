"""Remote PDF ingestion must reject local network destinations."""
import pytest

from app.security.outbound import _check_public_https


@pytest.mark.parametrize("url", [
    "http://example.com/paper.pdf",
    "https://127.0.0.1/paper.pdf",
    "https://10.0.0.4/paper.pdf",
    "https://[::1]/paper.pdf",
    "https://user:pass@example.com/paper.pdf",
    "https://example.com:8443/paper.pdf",
])
def test_non_public_pdf_urls_are_rejected(url):
    with pytest.raises(ValueError):
        _check_public_https(url)

"""Container entrypoint: restrictive files, Render PORT, one trusted-proxy worker."""
from __future__ import annotations

import os


def main() -> None:
    import uvicorn

    os.umask(0o077)
    port = int(os.getenv("PORT", "8000"))
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be between 1 and 65535")
    # Shared index/lifecycle state deliberately supports only one server process.
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, workers=1,
                proxy_headers=True,
                forwarded_allow_ips=os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1"),
                access_log=False, timeout_keep_alive=5,
                timeout_graceful_shutdown=30, limit_concurrency=64)


if __name__ == "__main__":
    main()

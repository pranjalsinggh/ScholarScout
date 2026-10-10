"""Local container readiness probe without tokens, prompts or diagnostic output."""
from __future__ import annotations

import json
import os
import sys
import urllib.request


def main() -> None:
    host = os.getenv("RENDER_EXTERNAL_HOSTNAME") or os.getenv("ALLOWED_HOSTS", "localhost").split(",")[0].strip()
    port = int(os.getenv("PORT", "8000"))
    request = urllib.request.Request(f"http://127.0.0.1:{port}/api/health", headers={"Host": host})
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=5) as response:
            healthy = response.status == 200 and json.load(response).get("status") == "ok"
    except Exception:
        healthy = False
    sys.exit(0 if healthy else 1)


if __name__ == "__main__":
    main()

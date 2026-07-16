from __future__ import annotations

import os


async def get_headers() -> dict[str, str]:
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Missing required environment variable: GITHUB_TOKEN")
    return {"Authorization": f"Bearer {token}"}

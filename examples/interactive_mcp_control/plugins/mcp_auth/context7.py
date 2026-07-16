from __future__ import annotations

import os


async def get_headers() -> dict[str, str]:
    key = os.getenv("CONTEXT7_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Missing required environment variable: CONTEXT7_API_KEY")
    return {"CONTEXT7_API_KEY": key}

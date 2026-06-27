"""Static web assets: the embeddable chat widget and its demo page.

Factored out of `app.py` so it can be mounted on a bare FastAPI app in tests
without building the engine.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from agent_manager.config import Settings

STATIC_DIR = Path(__file__).resolve().parent / "static"


def mount_web(app: FastAPI, settings: Settings) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/widget.js")
    def widget_js() -> FileResponse:
        return FileResponse(STATIC_DIR / "widget.js", media_type="application/javascript")

    @app.get("/demo")
    def demo() -> FileResponse:
        return FileResponse(STATIC_DIR / "demo.html", media_type="text/html")

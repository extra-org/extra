from __future__ import annotations

import dataclasses
import json
import logging
import re
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent_engine.core.validator import SystemSpecValidator
from agent_engine.engine.engine import Engine
from agent_engine.engine.langgraph.engine import LangGraphEngine
from agent_engine.logging_config import configure_logging, log, request_id_var
from agent_engine.parsers.yaml.parser import YAMLParser
from agent_engine.runtime.hooks import RunContext

logger = logging.getLogger(__name__)


_RID_OK = re.compile(r"[^A-Za-z0-9._-]")


def _begin_request(x_request_id: str | None) -> str:
    """Adopt the caller's request id or mint one, and bind it for log correlation.

    The inbound id is untrusted (it lands in logs and a response header), so we
    sanitize to safe chars and cap length before trusting it.
    """
    rid = _RID_OK.sub("", x_request_id or "")[:64] if x_request_id else ""
    rid = rid or uuid4().hex[:12]
    request_id_var.set(rid)
    return rid


def _preview(text: str, limit: int = 120) -> str:
    """Truncated, single-line message preview for audit logs (privacy-safe).

    Collapsing whitespace keeps a user message from injecting newlines into the
    log stream.
    """
    collapsed = " ".join(text.split())
    return collapsed[:limit] + ("…" if len(collapsed) > limit else "")


def _run_context(x_session_id: str | None) -> RunContext | None:
    """Build a RunContext carrying the caller's session id, or None if absent.

    The id flows into tracing metadata (the Langfuse session). It is untrusted,
    so sanitize to safe characters and cap length before trusting it.
    """
    if not x_session_id:
        return None
    session_id = _RID_OK.sub("", x_session_id)[:64]
    return RunContext(conversation_id=session_id) if session_id else None


class InvokeRequest(BaseModel):
    message: str


class ToolRecord(BaseModel):
    name: str
    provider: str
    status: str
    agent_id: str | None = None
    server_id: str | None = None
    error: str | None = None


class InvokeResponse(BaseModel):
    system_name: str
    answer: str
    visited: list[str]
    used_tools: list[ToolRecord]


def create_app(config_path: str) -> FastAPI:
    _engine: Engine | None = None
    _system_name: str = ""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Any:
        nonlocal _engine, _system_name

        configure_logging()
        base_dir = Path(config_path).resolve().parent
        if str(base_dir) not in sys.path:
            sys.path.insert(0, str(base_dir))

        spec = YAMLParser().parse(config_path)
        errors = SystemSpecValidator().validate(spec, base_dir)
        if errors:
            raise RuntimeError(f"Invalid config: {'; '.join(str(e) for e in errors)}")

        _system_name = spec.meta.name

        async with LangGraphEngine(base_dir) as e:
            await e.build(spec)
            _engine = e
            yield

    app = FastAPI(lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "system": _system_name}

    @app.post("/invoke", response_model=InvokeResponse)
    async def invoke(
        body: InvokeRequest,
        response: Response,
        x_request_id: str | None = Header(default=None),
        x_session_id: str | None = Header(default=None),
    ) -> InvokeResponse:
        assert _engine is not None
        rid = _begin_request(x_request_id)
        response.headers["X-Request-ID"] = rid
        started = time.perf_counter()
        log(
            logger,
            logging.INFO,
            "request start",
            endpoint="invoke",
            chars=len(body.message),
            message=_preview(body.message),
        )
        try:
            result = await _engine.run(body.message, context=_run_context(x_session_id))
        except Exception as exc:
            log(logger, logging.ERROR, "request end", status="error", error=str(exc))
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        log(
            logger,
            logging.INFO,
            "request end",
            status="ok",
            duration_ms=int((time.perf_counter() - started) * 1000),
            route=" → ".join(result.visited),
            tools=len(result.used_tools),
            answer_chars=len(result.answer),
        )
        return InvokeResponse(
            system_name=result.system_name,
            answer=result.answer,
            visited=result.visited,
            used_tools=[ToolRecord(**dataclasses.asdict(t)) for t in result.used_tools],
        )

    @app.post("/stream")
    async def stream(
        body: InvokeRequest,
        x_request_id: str | None = Header(default=None),
        x_session_id: str | None = Header(default=None),
    ) -> StreamingResponse:
        assert _engine is not None
        rid = _begin_request(x_request_id)
        context = _run_context(x_session_id)
        started = time.perf_counter()
        log(
            logger,
            logging.INFO,
            "request start",
            endpoint="stream",
            chars=len(body.message),
            message=_preview(body.message),
        )

        async def event_stream():
            try:
                async for event in _engine.stream(body.message, context=context):
                    payload = {k: v for k, v in dataclasses.asdict(event).items() if v is not None}
                    yield f"data: {json.dumps(payload)}\n\n"
                log(
                    logger,
                    logging.INFO,
                    "request end",
                    status="ok",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            except Exception as exc:
                log(logger, logging.ERROR, "request end", status="error", error=str(exc))
                yield f"data: {json.dumps({'type': 'error', 'detail': str(exc)})}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"X-Request-ID": rid},
        )

    return app

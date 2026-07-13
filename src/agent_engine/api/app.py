from __future__ import annotations

import dataclasses
import json
import logging
import re
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent_engine.approvals.decision import ApprovalDecision, parse_decision
from agent_engine.approvals.errors import (
    ApprovalAlreadyProcessed,
    ApprovalError,
    ApprovalNotFound,
    ApprovalRunMismatch,
    InvalidDecision,
    RunNotFound,
    UnauthorizedApprover,
)
from agent_engine.core.validator import SystemSpecValidator
from agent_engine.engine.engine import Engine
from agent_engine.engine.langgraph.engine import LangGraphEngine
from agent_engine.engine.types import PendingApproval
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


def _run_context(x_session_id: str | None, *, run_id: str) -> RunContext:
    """Build a RunContext carrying a run id and the caller's session id.

    The run id is the resumable identifier returned to the client (also the
    LangGraph checkpoint thread id). The session id flows into tracing metadata
    (the Langfuse session); it is untrusted, so sanitize and cap it.
    """
    session_id = _RID_OK.sub("", x_session_id)[:64] if x_session_id else ""
    return RunContext(run_id=run_id, conversation_id=session_id or None)


def _pending_model(pa: PendingApproval | None) -> PendingApprovalModel | None:
    if pa is None:
        return None
    return PendingApprovalModel(
        run_id=pa.run_id,
        approval_id=pa.approval_id,
        agent_id=pa.agent_id,
        tool_name=pa.tool_name,
        description=pa.description,
        provider=pa.provider,
        server_id=pa.server_id,
        arguments=pa.arguments,
    )


def _map_approval_error(exc: ApprovalError) -> HTTPException:
    """Map approval-lifecycle errors to stable HTTP responses (no internals)."""
    status = {
        RunNotFound: 404,
        ApprovalNotFound: 404,
        ApprovalRunMismatch: 404,
        UnauthorizedApprover: 403,
        ApprovalAlreadyProcessed: 409,
        InvalidDecision: 400,
    }.get(type(exc), 409)
    return HTTPException(status_code=status, detail=str(exc))


class InvokeRequest(BaseModel):
    message: str


class ToolRecord(BaseModel):
    name: str
    provider: str
    status: str
    agent_id: str | None = None
    server_id: str | None = None
    error: str | None = None


class PendingApprovalModel(BaseModel):
    """Sanitized pending-approval payload returned to the client/UI."""

    run_id: str
    approval_id: str
    agent_id: str
    tool_name: str
    description: str
    provider: str
    server_id: str | None = None
    arguments: dict[str, Any] = {}


class InvokeResponse(BaseModel):
    system_name: str
    answer: str
    visited: list[str]
    used_tools: list[ToolRecord]
    run_id: str
    status: str = "completed"
    pending_approval: PendingApprovalModel | None = None


class ApprovalDecisionRequest(BaseModel):
    """Decide a pending tool call. ``user_id`` must match the run's authorized
    approver when one was recorded."""

    user_id: str | None = None


class ApprovalDecisionBody(ApprovalDecisionRequest):
    """A free-text decision from a UI/CLI, parsed to a typed decision at this
    boundary. Accepts values like ``allow``, ``allow for this session``, or
    ``deny``; an unrecognized value yields a 400."""

    decision: str


class RunStatusResponse(BaseModel):
    run_id: str
    status: str
    pending_approval: PendingApprovalModel | None = None


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
        run_id = uuid4().hex
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
            result = await _engine.run(
                body.message, context=_run_context(x_session_id, run_id=run_id)
            )
        except Exception as exc:
            log(logger, logging.ERROR, "request end", status="error", error=str(exc))
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        log(
            logger,
            logging.INFO,
            "request end",
            status=result.status,
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
            run_id=run_id,
            status=result.status,
            pending_approval=_pending_model(result.pending_approval),
        )

    @app.post("/stream")
    async def stream(
        body: InvokeRequest,
        x_request_id: str | None = Header(default=None),
        x_session_id: str | None = Header(default=None),
    ) -> StreamingResponse:
        assert _engine is not None
        rid = _begin_request(x_request_id)
        context = _run_context(x_session_id, run_id=uuid4().hex)
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

    def _hitl_engine() -> LangGraphEngine:
        assert _engine is not None
        # The stateless engine app always runs LangGraphEngine; the HITL methods
        # live there, not on the abstract Engine base.
        return cast(LangGraphEngine, _engine)

    @app.get("/runs/{run_id}", response_model=RunStatusResponse)
    async def run_status(run_id: str) -> RunStatusResponse:
        engine = _hitl_engine()
        try:
            status = await engine.get_run_status(run_id)
            pending = await engine.get_pending_approval(run_id)
        except ApprovalError as exc:
            raise _map_approval_error(exc) from exc
        return RunStatusResponse(
            run_id=run_id, status=status, pending_approval=_pending_model(pending)
        )

    async def _decide(
        run_id: str, approval_id: str, decision: ApprovalDecision, user_id: str | None
    ) -> InvokeResponse:
        engine = _hitl_engine()
        try:
            result = await engine.resume(run_id, approval_id, decision, caller_user_id=user_id)
        except ApprovalError as exc:
            raise _map_approval_error(exc) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return InvokeResponse(
            system_name=result.system_name,
            answer=result.answer,
            visited=result.visited,
            used_tools=[ToolRecord(**dataclasses.asdict(t)) for t in result.used_tools],
            run_id=run_id,
            status=result.status,
            pending_approval=_pending_model(result.pending_approval),
        )

    @app.post("/runs/{run_id}/approvals/{approval_id}/decision", response_model=InvokeResponse)
    async def decide(run_id: str, approval_id: str, body: ApprovalDecisionBody) -> InvokeResponse:
        # The single free-text → typed decision boundary for the API.
        try:
            decision = parse_decision(body.decision)
        except InvalidDecision as exc:
            raise _map_approval_error(exc) from exc
        return await _decide(run_id, approval_id, decision, body.user_id)

    @app.post("/runs/{run_id}/approvals/{approval_id}/approve", response_model=InvokeResponse)
    async def approve(
        run_id: str, approval_id: str, body: ApprovalDecisionRequest
    ) -> InvokeResponse:
        return await _decide(run_id, approval_id, ApprovalDecision.ALLOW_ONCE, body.user_id)

    @app.post("/runs/{run_id}/approvals/{approval_id}/reject", response_model=InvokeResponse)
    async def reject(
        run_id: str, approval_id: str, body: ApprovalDecisionRequest
    ) -> InvokeResponse:
        return await _decide(run_id, approval_id, ApprovalDecision.DENY, body.user_id)

    return app

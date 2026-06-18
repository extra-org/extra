from __future__ import annotations

import dataclasses
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent_engine.core.validator import SystemSpecValidator
from agent_engine.engine.engine import Engine
from agent_engine.engine.langgraph.engine import LangGraphEngine
from agent_engine.parsers.yaml.parser import YAMLParser


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
    async def invoke(body: InvokeRequest) -> InvokeResponse:
        assert _engine is not None
        try:
            result = await _engine.run(body.message)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return InvokeResponse(
            system_name=result.system_name,
            answer=result.answer,
            visited=result.visited,
            used_tools=[ToolRecord(**dataclasses.asdict(t)) for t in result.used_tools],
        )

    @app.post("/stream")
    async def stream(body: InvokeRequest) -> StreamingResponse:
        assert _engine is not None

        async def event_stream():
            try:
                async for event in _engine.stream(body.message):
                    payload = {k: v for k, v in dataclasses.asdict(event).items() if v is not None}
                    yield f"data: {json.dumps(payload)}\n\n"
            except Exception as exc:
                yield f"data: {json.dumps({'type': 'error', 'detail': str(exc)})}\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    return app

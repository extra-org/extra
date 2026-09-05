"""Provider-agnostic normalized representation for tool execution outcomes."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any

from langchain_core.messages import ToolMessage

logger = logging.getLogger(__name__)


def _as_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and "text" in item:
                parts.append(str(item["text"]))
        return "".join(parts)
    return str(content) if content is not None else ""


@dataclass(frozen=True)
class NormalizedToolResult:
    """Provider-agnostic representation of a tool execution outcome.

    Separates model-facing text content from machine-readable structured output
    and optional artifact metadata, preserving MCP structuredContent without
    collapsing everything to a plain string.
    """

    text: str
    structured: Any | None = None
    artifact: Any | None = None


def normalize_tool_result(raw_result: Any) -> NormalizedToolResult:
    """Normalize a raw tool execution return value into a NormalizedToolResult.

    Supports:
    - String output (local tools)
    - LangChain ToolMessage (carrying MCP content blocks and artifact.structuredContent)
    - Dicts with content / structuredContent
    - Local Python objects / dicts / Pydantic models
    """
    if isinstance(raw_result, NormalizedToolResult):
        return raw_result

    if isinstance(raw_result, str):
        return NormalizedToolResult(text=raw_result)

    text = ""
    structured: Any | None = None
    artifact: Any | None = None

    try:
        if isinstance(raw_result, ToolMessage):
            text = _as_text(raw_result.content)
            artifact = getattr(raw_result, "artifact", None)
            if artifact is not None:
                if isinstance(artifact, dict) and "structuredContent" in artifact:
                    structured = artifact["structuredContent"]
                elif hasattr(artifact, "structuredContent"):
                    structured = artifact.structuredContent
        elif isinstance(raw_result, dict):
            artifact = raw_result.get("artifact")
            if "content" in raw_result:
                text = _as_text(raw_result["content"])
            if "structuredContent" in raw_result:
                structured = raw_result["structuredContent"]
            elif isinstance(artifact, dict) and "structuredContent" in artifact:
                structured = artifact["structuredContent"]
            elif "content" not in raw_result and "artifact" not in raw_result:
                structured = raw_result
        elif is_dataclass(raw_result) and not isinstance(raw_result, type):
            structured = asdict(raw_result)
        elif hasattr(raw_result, "model_dump") and callable(raw_result.model_dump):
            structured = raw_result.model_dump()
        elif hasattr(raw_result, "dict") and callable(raw_result.dict):
            structured = raw_result.dict()
        elif isinstance(raw_result, (bytes, bytearray)):
            text = raw_result.decode("utf-8", errors="replace")
        else:
            text = ""
    except Exception as exc:
        logger.warning("Failed to extract structured content from tool result: %s", exc)
        text = ""
        structured = None
        artifact = None

    if not text.strip() and structured is not None:
        try:
            text = json.dumps(structured, default=str)
        except Exception:
            text = ""

    return NormalizedToolResult(text=text, structured=structured, artifact=artifact)

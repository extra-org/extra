from __future__ import annotations

import contextvars
import logging
import os

from pydantic import ValidationError

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")

_NOISY = ("httpx", "httpcore", "anthropic", "openai", "urllib3")
_ERROR_LIMIT = 200


def validation_problems(exc: ValidationError) -> str:
    """Which fields were rejected and why, without the values that were sent."""
    return "; ".join(
        f"{'.'.join(str(part) for part in err['loc']) or 'input'}: {err['msg']}"
        for err in exc.errors()
    )


def safe_error(exc: BaseException, limit: int = _ERROR_LIMIT) -> str:
    """An exception rendered for a log field: bounded, single-line, no payload.

    ``str()`` on a pydantic error appends the ``input_value`` it rejected — the
    caller's own arguments — so validation failures are reduced to their field
    reasons instead.
    """
    detail = validation_problems(exc) if isinstance(exc, ValidationError) else str(exc)
    return " ".join(detail.split())[:limit]


def _kv(key: str, value: object) -> str:
    if isinstance(value, str) and any(c in value for c in ' ="'):
        return f'{key}="{value}"'
    return f"{key}={value}"


class StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        pairs: list[str] = []
        rid = request_id_var.get()
        if rid:
            pairs.append(_kv("request_id", rid))
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            pairs.extend(_kv(k, v) for k, v in fields.items())
        return f"{base} {' '.join(pairs)}" if pairs else base


def log(logger: logging.Logger, level: int, event: str, **fields: object) -> None:
    """Emit one structured line. Exception values are rendered by ``safe_error``,
    so no call site can leak a rejected payload into a log field by passing the
    exception straight through."""
    safe = {k: safe_error(v) if isinstance(v, BaseException) else v for k, v in fields.items()}
    logger.log(level, event, extra={"fields": safe})


def configure_logging(level: str | None = None) -> None:
    name = (level or os.getenv("LOG_LEVEL") or "INFO").upper()
    resolved = getattr(logging, name, logging.INFO)
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(resolved)
    for noisy in _NOISY:
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # Any logger a library already configured (e.g. uvicorn) gets its own
    # handlers stripped and routed through ours, so every log line shares
    # one format regardless of which library emitted it.
    for existing in logging.Logger.manager.loggerDict.values():
        if isinstance(existing, logging.Logger):
            existing.handlers.clear()
            existing.propagate = True


def current_request_id() -> str:
    return request_id_var.get()

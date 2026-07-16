"""Interactive simulation console for local development.

One command, two modes:

* **Local engine** (``--config``): build a :class:`LangGraphEngine` *once* and
  send every question through the same instance — the same path ``agentctl run``
  uses, minus the per-message process startup.
* **Remote server** (``--url``): talk to a running ``agentctl serve`` over its
  existing ``/invoke`` and ``/stream`` HTTP contract.

The loop is deliberately small and side-effect-light: reading input, building
the engine, and the HTTP client are all injectable so the behavior can be
tested without a real terminal, LLM, or network.

Security: this is a developer simulation tool. It never injects hidden context
into prompts, never prints engine internals (route/tool detail stays in logs at
the configured ``--log-level``), and only sends the user's own text.
"""

from __future__ import annotations

import json
import sys
import uuid
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import click

from agent_engine.approvals.decision import ApprovalDecision, parse_decision
from agent_engine.approvals.errors import InvalidDecision
from agent_engine.engine.types import PendingApproval, RunResult
from agentctl.session import SpecError, load_and_validate, load_env

if TYPE_CHECKING:
    import httpx

    from agent_engine.engine.engine import Engine
    from agent_engine.runtime.hooks import RunContext

#: Header carrying the Langfuse session id to a remote ``agentctl serve``.
SESSION_HEADER = "X-Session-ID"

BANNER = "Agent Engine interactive chat\nType 'exit' or 'quit' to stop."
PROMPT = "You > "
ANSWER_PREFIX = "Agent > "
_EXIT_WORDS = {"exit", "quit", "q"}

ReadLine = Callable[[str], str]


class _StopChat(Exception):
    """Internal control flow for leaving the console from a nested prompt."""


@runtime_checkable
class _ApprovalEngine(Protocol):
    """HITL operations exposed by the concrete local engine."""

    async def get_pending_approval(self, run_id: str) -> PendingApproval | None: ...

    async def resume(
        self,
        run_id: str,
        approval_id: str,
        decision: ApprovalDecision | str,
        *,
        caller_user_id: str | None = None,
    ) -> RunResult: ...


# click.echo's signature is broad; we only use (message, *, err). Keep a simple wrapper.
def _default_echo(message: str = "", *, err: bool = False) -> None:
    click.echo(message, err=err)


def _iter_questions(read_line: ReadLine) -> Iterator[str]:
    """Yield trimmed, non-empty questions until an exit word, EOF, or Ctrl-C.

    Empty input is ignored. ``exit``/``quit``/``q`` (case-insensitive), EOF
    (Ctrl-D), and KeyboardInterrupt (Ctrl-C) all end the loop cleanly without
    raising or printing a traceback.
    """
    while True:
        try:
            raw = read_line(PROMPT)
        except (EOFError, KeyboardInterrupt):
            return
        text = raw.strip()
        if not text:
            continue
        if text.lower() in _EXIT_WORDS:
            return
        yield text


async def run_local_chat(
    config: str,
    env: str | None,
    stream: bool,
    *,
    session_id: str | None = None,
    read_line: ReadLine = input,
    echo: Callable[..., None] = _default_echo,
) -> None:
    """Build the engine once, then loop questions through it (the ``run`` path).

    Every question in this console shares one ``conversation_id`` so the run is
    grouped as a single Langfuse session. Pass ``session_id`` to set it
    explicitly; otherwise a short id is generated for the console session.
    """
    from agent_engine.approvals.session_store import InMemorySessionApprovalRepository
    from agent_engine.engine.langgraph.engine import LangGraphEngine
    from agent_engine.runtime.hooks import RunContext

    load_env(config, env)
    try:
        spec, base_dir = load_and_validate(config)
    except SpecError as exc:
        for message in exc.messages:
            echo(f"✗ {message}", err=True)
        raise SystemExit(1) from exc

    context = RunContext(conversation_id=session_id or uuid.uuid4().hex[:16])
    session_approvals = InMemorySessionApprovalRepository()
    async with LangGraphEngine(
        base_dir,
        session_approval_repository=session_approvals,
    ) as engine:
        await engine.build(spec)
        await run_chat_loop(engine, stream=stream, context=context, read_line=read_line, echo=echo)


async def run_chat_loop(
    engine: Engine,
    *,
    stream: bool,
    context: RunContext | None = None,
    read_line: ReadLine = input,
    echo: Callable[..., None] = _default_echo,
) -> None:
    """Drive an already-built engine: print the banner, then answer each question.

    The engine is reused across questions; only per-request state lives in each
    call to ``engine.run``/``engine.stream``. ``context`` (if given) is reused for
    every question, grouping them under one session.
    """
    echo(BANNER, err=True)
    if context is not None and context.conversation_id:
        echo(f"Session: {context.conversation_id}", err=True)
    for question in _iter_questions(read_line):
        try:
            await _answer_local(
                engine,
                question,
                stream=stream,
                context=context,
                read_line=read_line,
                echo=echo,
            )
        except _StopChat:
            return


async def _answer_local(
    engine: Engine,
    question: str,
    *,
    stream: bool,
    context: RunContext | None = None,
    read_line: ReadLine = input,
    echo: Callable[..., None],
) -> None:
    """Answer one question. A failure is reported but never kills the loop."""
    try:
        if stream:
            await _answer_local_stream(
                engine, question, context=context, read_line=read_line, echo=echo
            )
        else:
            result = await engine.run(question, context=context)
            result = await _resolve_local_approvals(
                engine, result, context=context, read_line=read_line, echo=echo
            )
            echo(f"{ANSWER_PREFIX}{result.answer}")
    except _StopChat:
        raise
    except Exception as exc:
        echo(f"✗ {exc}", err=True)


async def _answer_local_stream(
    engine: Engine,
    question: str,
    *,
    context: RunContext | None,
    read_line: ReadLine,
    echo: Callable[..., None],
) -> None:
    """Stream a response, switching to HITL resume if execution suspends."""
    pending: PendingApproval | None = None
    answer_started = False
    async for event in engine.stream(question, context=context):
        if event.type == "answer_delta" and event.content:
            if not answer_started:
                sys.stdout.write(ANSWER_PREFIX)
                answer_started = True
            sys.stdout.write(event.content)
            sys.stdout.flush()
        elif event.type == "pending_approval":
            approval_engine = _require_approval_engine(engine)
            if event.run_id is None:
                raise RuntimeError("The engine returned an approval without a run id.")
            pending = await approval_engine.get_pending_approval(event.run_id)
            if pending is None:
                raise RuntimeError(
                    "The engine returned an approval event without approval details."
                )

    if answer_started:
        sys.stdout.write("\n")
    if pending is None:
        if not answer_started:
            echo(ANSWER_PREFIX)
        return

    result = RunResult(
        system_name="",
        visited=[],
        answer="",
        status="pending_approval",
        pending_approval=pending,
    )
    result = await _resolve_local_approvals(
        engine, result, context=context, read_line=read_line, echo=echo
    )
    echo(f"{ANSWER_PREFIX}{result.answer}")


async def _resolve_local_approvals(
    engine: Engine,
    result: RunResult,
    *,
    context: RunContext | None,
    read_line: ReadLine,
    echo: Callable[..., None],
) -> RunResult:
    """Prompt and resume until a run completes or stops requesting approvals."""
    while result.status == "pending_approval":
        approval_engine = _require_approval_engine(engine)
        pending = result.pending_approval
        if pending is None:
            raise RuntimeError("The engine paused for approval without approval details.")
        decision = _prompt_for_approval(pending, read_line=read_line, echo=echo)
        result = await approval_engine.resume(
            pending.run_id,
            pending.approval_id,
            decision,
            caller_user_id=context.user_id if context is not None else None,
        )
    return result


def _require_approval_engine(engine: Engine) -> _ApprovalEngine:
    if not isinstance(engine, _ApprovalEngine):
        raise RuntimeError("This engine does not support resuming approval requests.")
    return engine


def _prompt_for_approval(
    pending: PendingApproval,
    *,
    read_line: ReadLine,
    echo: Callable[..., None],
) -> ApprovalDecision:
    """Show one sanitized tool request and return a typed human decision."""
    tool_identity = (
        f"{pending.server_id}.{pending.tool_name}" if pending.server_id else pending.tool_name
    )
    echo("", err=True)
    echo("Approval required", err=True)
    echo(f"  Agent     : {pending.agent_id}", err=True)
    echo(f"  Tool      : {tool_identity} ({pending.provider})", err=True)
    echo(f"  Request   : {pending.description}", err=True)
    echo(
        f"  Arguments : {json.dumps(pending.arguments, sort_keys=True, default=str)}",
        err=True,
    )

    prompt = "Allow? [o]nce / [s]ession / [d]eny / [q]uit: "
    shortcuts = {
        "o": ApprovalDecision.ALLOW_ONCE,
        "s": ApprovalDecision.ALLOW_FOR_SESSION,
        "d": ApprovalDecision.DENY,
    }
    while True:
        try:
            raw = read_line(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            raise _StopChat from None

        normalized = raw.lower()
        if normalized in _EXIT_WORDS:
            raise _StopChat
        shortcut = shortcuts.get(normalized)
        if shortcut is not None:
            return shortcut
        try:
            return parse_decision(raw)
        except InvalidDecision:
            echo(
                "Enter 'o' to allow once, 's' for this session, 'd' to deny, or 'q' to quit.",
                err=True,
            )


# ---------------------------------------------------------------------------
# remote server mode
# ---------------------------------------------------------------------------


async def run_remote_chat(
    url: str,
    stream: bool,
    *,
    session_id: str | None = None,
    read_line: ReadLine = input,
    echo: Callable[..., None] = _default_echo,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Loop questions against a running ``agentctl serve`` over HTTP.

    The session id is sent on every request as the ``X-Session-ID`` header so the
    server can group the conversation as one Langfuse session.
    """
    import httpx

    base = url.rstrip("/")
    headers = {SESSION_HEADER: session_id or uuid.uuid4().hex[:16]}
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))
    try:
        echo(BANNER, err=True)
        echo(f"Session: {headers[SESSION_HEADER]}", err=True)
        for question in _iter_questions(read_line):
            await _answer_remote(client, base, question, stream=stream, headers=headers, echo=echo)
    finally:
        if owns_client:
            await client.aclose()


async def _answer_remote(
    client: httpx.AsyncClient,
    base: str,
    question: str,
    *,
    stream: bool,
    headers: dict[str, str],
    echo: Callable[..., None],
) -> None:
    """Send one question to the server. Network/server errors never kill the loop."""
    import httpx

    try:
        if stream:
            await _remote_stream(client, base, question, headers=headers, echo=echo)
        else:
            await _remote_invoke(client, base, question, headers=headers, echo=echo)
    except httpx.HTTPError as exc:
        echo(f"✗ request failed: {exc}", err=True)


async def _remote_invoke(
    client: httpx.AsyncClient,
    base: str,
    question: str,
    *,
    headers: dict[str, str],
    echo: Callable[..., None],
) -> None:
    resp = await client.post(f"{base}/invoke", json={"message": question}, headers=headers)
    if resp.status_code != 200:
        echo(f"✗ server error {resp.status_code}: {_error_detail(resp)}", err=True)
        return
    data = resp.json()
    echo(f"{ANSWER_PREFIX}{data.get('answer', '')}")


async def _remote_stream(
    client: httpx.AsyncClient,
    base: str,
    question: str,
    *,
    headers: dict[str, str],
    echo: Callable[..., None],
) -> None:
    sys.stdout.write(ANSWER_PREFIX)
    sys.stdout.flush()
    async with client.stream(
        "POST", f"{base}/stream", json={"message": question}, headers=headers
    ) as resp:
        if resp.status_code != 200:
            await resp.aread()
            sys.stdout.write("\n")
            echo(f"✗ server error {resp.status_code}: {_error_detail(resp)}", err=True)
            return
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            payload = json.loads(line[len("data: ") :])
            if payload.get("type") == "answer_delta" and payload.get("content"):
                sys.stdout.write(payload["content"])
                sys.stdout.flush()
            elif payload.get("type") == "error":
                sys.stdout.write("\n")
                echo(f"✗ {payload.get('detail', 'stream error')}", err=True)
                return
    sys.stdout.write("\n")


def _error_detail(resp: httpx.Response) -> str:
    """Pull a clean error message out of a non-200 response body."""
    try:
        body = resp.json()
    except (json.JSONDecodeError, ValueError):
        return resp.text[:200] or resp.reason_phrase
    if isinstance(body, dict) and "detail" in body:
        return str(body["detail"])
    return str(body)[:200]

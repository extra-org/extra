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
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING

import click

from agentctl.session import SpecError, load_and_validate, load_env

if TYPE_CHECKING:
    import httpx

    from agent_engine.engine.engine import Engine

BANNER = "Agent Engine interactive chat\nType 'exit' or 'quit' to stop."
PROMPT = "You > "
ANSWER_PREFIX = "Agent > "
_EXIT_WORDS = {"exit", "quit", "q"}

ReadLine = Callable[[str], str]


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


# ---------------------------------------------------------------------------
# local engine mode
# ---------------------------------------------------------------------------


async def run_local_chat(
    config: str,
    env: str | None,
    stream: bool,
    *,
    read_line: ReadLine = input,
    echo: Callable[..., None] = _default_echo,
) -> None:
    """Build the engine once, then loop questions through it (the ``run`` path)."""
    from agent_engine.engine.langgraph.engine import LangGraphEngine

    load_env(config, env)
    try:
        spec, base_dir = load_and_validate(config)
    except SpecError as exc:
        for message in exc.messages:
            echo(f"✗ {message}", err=True)
        raise SystemExit(1) from exc

    async with LangGraphEngine(base_dir) as engine:
        await engine.build(spec)
        await run_chat_loop(engine, stream=stream, read_line=read_line, echo=echo)


async def run_chat_loop(
    engine: Engine,
    *,
    stream: bool,
    read_line: ReadLine = input,
    echo: Callable[..., None] = _default_echo,
) -> None:
    """Drive an already-built engine: print the banner, then answer each question.

    The engine is reused across questions; only per-request state lives in each
    call to ``engine.run``/``engine.stream``.
    """
    echo(BANNER, err=True)
    for question in _iter_questions(read_line):
        await _answer_local(engine, question, stream=stream, echo=echo)


async def _answer_local(
    engine: Engine, question: str, *, stream: bool, echo: Callable[..., None]
) -> None:
    """Answer one question. A failure is reported but never kills the loop."""
    try:
        if stream:
            sys.stdout.write(ANSWER_PREFIX)
            sys.stdout.flush()
            async for event in engine.stream(question):
                if event.type == "answer_delta" and event.content:
                    sys.stdout.write(event.content)
                    sys.stdout.flush()
            sys.stdout.write("\n")
        else:
            result = await engine.run(question)
            echo(f"{ANSWER_PREFIX}{result.answer}")
    except Exception as exc:
        echo(f"✗ {exc}", err=True)


# ---------------------------------------------------------------------------
# remote server mode
# ---------------------------------------------------------------------------


async def run_remote_chat(
    url: str,
    stream: bool,
    *,
    read_line: ReadLine = input,
    echo: Callable[..., None] = _default_echo,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Loop questions against a running ``agentctl serve`` over HTTP."""
    import httpx

    base = url.rstrip("/")
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))
    try:
        echo(BANNER, err=True)
        for question in _iter_questions(read_line):
            await _answer_remote(client, base, question, stream=stream, echo=echo)
    finally:
        if owns_client:
            await client.aclose()


async def _answer_remote(
    client: httpx.AsyncClient,
    base: str,
    question: str,
    *,
    stream: bool,
    echo: Callable[..., None],
) -> None:
    """Send one question to the server. Network/server errors never kill the loop."""
    import httpx

    try:
        if stream:
            await _remote_stream(client, base, question, echo=echo)
        else:
            await _remote_invoke(client, base, question, echo=echo)
    except httpx.HTTPError as exc:
        echo(f"✗ request failed: {exc}", err=True)


async def _remote_invoke(
    client: httpx.AsyncClient, base: str, question: str, *, echo: Callable[..., None]
) -> None:
    resp = await client.post(f"{base}/invoke", json={"message": question})
    if resp.status_code != 200:
        echo(f"✗ server error {resp.status_code}: {_error_detail(resp)}", err=True)
        return
    data = resp.json()
    echo(f"{ANSWER_PREFIX}{data.get('answer', '')}")


async def _remote_stream(
    client: httpx.AsyncClient, base: str, question: str, *, echo: Callable[..., None]
) -> None:
    sys.stdout.write(ANSWER_PREFIX)
    sys.stdout.flush()
    async with client.stream("POST", f"{base}/stream", json={"message": question}) as resp:
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

"""Shared spec-loading helpers for the ``run`` and ``chat`` commands.

Both commands need the same pre-flight before they can touch the engine: load
the ``.env`` next to the spec (or an explicit one), parse the YAML, and run the
offline validator. Keeping this in one place means ``chat`` reuses the exact
path ``run`` uses instead of copy/pasting it.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

from agent_engine.core.spec import SystemSpec
from agent_engine.core.validator import SystemSpecValidator
from agent_engine.parsers.yaml.parser import YAMLParser


class SpecError(Exception):
    """A spec failed to parse or validate. Carries one or more human messages."""

    def __init__(self, messages: list[str]) -> None:
        self.messages = messages
        super().__init__("; ".join(messages))


def load_env(config: str, env: str | None) -> None:
    """Load env vars from an explicit ``env`` file, else ``.env`` beside the spec."""
    env_path = Path(env) if env else Path(config).resolve().parent / ".env"
    load_dotenv(env_path, override=True)


def load_and_validate(config: str) -> tuple[SystemSpec, Path]:
    """Parse and validate a spec. Raise :class:`SpecError` on any problem.

    Returns the parsed spec and the base directory (the spec's parent) the
    engine resolves plugins and prompts against.
    """
    try:
        spec = YAMLParser().parse(config)
    except Exception as exc:  # parse / schema / tool_tags / hook-ref errors
        raise SpecError([str(exc)]) from exc

    base_dir = Path(config).resolve().parent
    errors = SystemSpecValidator().validate(spec, base_dir)
    if errors:
        raise SpecError([str(e) for e in errors])
    return spec, base_dir

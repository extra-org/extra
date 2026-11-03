"""Extra's default system prompt: composition, opt-out, and delegation (issue #100)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from langchain_core.language_models import BaseChatModel

from agent_engine.core.spec import (
    AgentSpec,
    BasePromptSet,
    GraphNode,
    ModelConfig,
    NodeSpec,
    OrchestratorPromptSet,
    OrchestratorSpec,
    SystemMeta,
    SystemSpec,
)
from agent_engine.engine.langgraph.engine import LangGraphEngine
from agent_engine.engine.langgraph.nodes.orchestrator_node import _ORCHESTRATOR_CONTRACT
from agent_engine.engine.langgraph.prompting import EXTRA_DEFAULT_SYSTEM_PROMPT, assemble_prompt
from agent_engine.parsers.errors import ParseError
from agent_engine.parsers.yaml.parser import YAMLParser
from tests.fixtures.utils import FakeChatModel

_MODEL = ModelConfig(provider="fake", name="fake")
_CONTRACT = _ORCHESTRATOR_CONTRACT.strip()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class RecordingModel(FakeChatModel):
    """A FakeChatModel that records the system prompt of every model call."""

    def __init__(self, log: list[str], tool_names: list[str] | None = None) -> None:
        super().__init__(answer="ok", tool_names=tool_names)
        self._log = log

    def bind_tools(self, tools: list[Any]) -> RecordingModel:
        return RecordingModel(self._log, [t.name for t in tools])

    async def ainvoke(self, messages: list[Any], config: Any = None, **kwargs: Any) -> Any:
        self._log.append(str(messages[0].content))
        return await super().ainvoke(messages, config, **kwargs)


def _agent(node_id: str, *, system: str | None = None, extra_default: bool = True) -> GraphNode:
    return GraphNode(
        node=AgentSpec(
            id=node_id,
            name=node_id,
            description=f"{node_id} agent",
            model=_MODEL,
            prompts=BasePromptSet(system=system, extra_default=extra_default),
        )
    )


def _orchestrator(
    node_id: str, children: list[GraphNode], *, extra_default: bool = True
) -> GraphNode:
    return GraphNode(
        node=OrchestratorSpec(
            id=node_id,
            name=node_id,
            description=f"{node_id} orchestrator",
            model=_MODEL,
            prompts=OrchestratorPromptSet(orchestrator="router.md", extra_default=extra_default),
        ),
        children=tuple(children),
    )


async def _system_prompts(base_dir: Path, graph: GraphNode, message: str) -> list[str]:
    """Run one message through a built engine; return every system prompt sent."""
    log: list[str] = []
    spec = SystemSpec(meta=SystemMeta(name="t"), defaults=None, graph=graph)

    def factory(*_: Any) -> BaseChatModel:
        return cast(BaseChatModel, RecordingModel(log))

    async with LangGraphEngine(base_dir, model_factory=factory) as engine:
        await engine.build(spec)
        await engine.run(message)
    return log


def _write(base_dir: Path, name: str, text: str) -> None:
    (base_dir / name).write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# assemble_prompt
# ---------------------------------------------------------------------------


def test_assemble_prompt_joins_in_order_and_skips_empty_fragments() -> None:
    assert assemble_prompt("first", None, "", "  \n", " second \n", "third") == (
        "first\n\nsecond\n\nthird"
    )
    assert assemble_prompt() == ""
    assert assemble_prompt(None, "") == ""


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


async def test_agent_gets_default_prompt_before_custom_prompt(tmp_path: Path) -> None:
    _write(tmp_path, "system.md", "You are the user management agent.")

    prompts = await _system_prompts(tmp_path, _agent("solo", system="system.md"), "hi")

    assert prompts == [f"{EXTRA_DEFAULT_SYSTEM_PROMPT}\n\nYou are the user management agent."]


async def test_agent_without_prompt_file_gets_default_plus_description(tmp_path: Path) -> None:
    prompts = await _system_prompts(tmp_path, _agent("solo"), "hi")

    assert prompts == [f"{EXTRA_DEFAULT_SYSTEM_PROMPT}\n\nsolo agent"]


async def test_agent_opt_out_keeps_only_custom_prompt(tmp_path: Path) -> None:
    _write(tmp_path, "system.md", "You are the user management agent.")

    prompts = await _system_prompts(
        tmp_path, _agent("solo", system="system.md", extra_default=False), "hi"
    )

    assert prompts == ["You are the user management agent."]


async def test_default_prompt_is_not_interpolated_but_custom_prompt_is(tmp_path: Path) -> None:
    # Resolver-free: an unknown placeholder in the custom prompt is left as-is,
    # and the default (engine text) is passed through byte-for-byte.
    _write(tmp_path, "system.md", "Hello {{name}}")

    (prompt,) = await _system_prompts(tmp_path, _agent("solo", system="system.md"), "hi")

    assert prompt.startswith(EXTRA_DEFAULT_SYSTEM_PROMPT)
    assert prompt.endswith("Hello {{name}}")


# ---------------------------------------------------------------------------
# Orchestrators and delegation
# ---------------------------------------------------------------------------


async def test_orchestrator_opt_out_keeps_routing_prompt_and_contract(tmp_path: Path) -> None:
    _write(tmp_path, "router.md", "Route carefully.")
    graph = _orchestrator("root", [_agent("child")], extra_default=False)

    prompts = await _system_prompts(tmp_path, graph, "hello")

    assert prompts[0] == f"root orchestrator\n\nRoute carefully.\n\n{_CONTRACT}"


async def test_delegated_run_sends_default_once_per_model_call(tmp_path: Path) -> None:
    _write(tmp_path, "router.md", "Route carefully.")
    _write(tmp_path, "child.md", "You handle child requests.")
    graph = _orchestrator("root", [_agent("child", system="child.md")])

    prompts = await _system_prompts(tmp_path, graph, "hello child")

    # Orchestrator decides, child answers, orchestrator synthesises.
    assert len(prompts) == 3
    for prompt in prompts:
        assert prompt.count(EXTRA_DEFAULT_SYSTEM_PROMPT) == 1
        assert prompt.startswith(EXTRA_DEFAULT_SYSTEM_PROMPT)
    assert f"{EXTRA_DEFAULT_SYSTEM_PROMPT}\n\nYou handle child requests." in prompts
    orchestrator_prompt = (
        f"{EXTRA_DEFAULT_SYSTEM_PROMPT}\n\nroot orchestrator\n\nRoute carefully.\n\n{_CONTRACT}"
    )
    assert prompts[0] == orchestrator_prompt


async def test_opt_out_applies_per_node_during_delegation(tmp_path: Path) -> None:
    _write(tmp_path, "router.md", "Route carefully.")
    _write(tmp_path, "child.md", "You handle child requests.")
    graph = _orchestrator("root", [_agent("child", system="child.md", extra_default=False)])

    prompts = await _system_prompts(tmp_path, graph, "hello child")

    assert "You handle child requests." in prompts  # child: custom only
    orchestrator_prompts = [p for p in prompts if p.endswith(_CONTRACT)]
    assert orchestrator_prompts
    assert all(p.startswith(EXTRA_DEFAULT_SYSTEM_PROMPT) for p in orchestrator_prompts)


# ---------------------------------------------------------------------------
# YAML: prompts.extra_default
# ---------------------------------------------------------------------------

_YAML = """
system:
  name: test
orchestrators:
  router:
    prompts:
      orchestrator: prompts/router.md
{router_extra}
agents:
  worker:
    description: "Worker"
{worker_prompts}
graph:
  router:
    worker:
"""


def _parse(tmp_path: Path, *, router_extra: str = "", worker_prompts: str = "") -> dict[str, Any]:
    cfg = tmp_path / "spec.yml"
    cfg.write_text(
        _YAML.format(router_extra=router_extra, worker_prompts=worker_prompts), encoding="utf-8"
    )
    spec = YAMLParser().parse(str(cfg))
    nodes: dict[str, NodeSpec] = {}
    pending = [spec.graph]
    while pending:
        current = pending.pop()
        nodes[current.node.id] = current.node
        pending.extend(current.children)
    return {node_id: node.get_prompts().extra_default for node_id, node in nodes.items()}


def test_yaml_default_is_enabled_without_configuration(tmp_path: Path) -> None:
    assert _parse(tmp_path) == {"router": True, "worker": True}


def test_yaml_can_disable_default_per_node(tmp_path: Path) -> None:
    flags = _parse(
        tmp_path,
        router_extra="      extra_default: false",
        worker_prompts="    prompts:\n      system: prompts/worker.md\n      extra_default: false",
    )

    assert flags == {"router": False, "worker": False}


@pytest.mark.parametrize(
    ("router_extra", "worker_prompts", "path"),
    [
        ('      extra_default: "no"', "", "orchestrators.router.prompts.extra_default"),
        ("", "    prompts:\n      extra_default: 0", "agents.worker.prompts.extra_default"),
    ],
)
def test_yaml_rejects_non_boolean_extra_default(
    tmp_path: Path, router_extra: str, worker_prompts: str, path: str
) -> None:
    with pytest.raises(ParseError) as exc_info:
        _parse(tmp_path, router_extra=router_extra, worker_prompts=worker_prompts)

    assert any(e.field == path and "boolean" in e.message for e in exc_info.value.errors)

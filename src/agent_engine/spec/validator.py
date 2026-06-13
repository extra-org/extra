"""Semantic validation for typed agent configuration specs."""

from __future__ import annotations

from pathlib import Path

from agent_engine.spec.errors import SpecValidationError, ValidationIssue
from agent_engine.spec.models import AgentEngineSpec, AgentSpec, OrchestratorSpec, PromptSpec
from agent_engine.utils import ProjectPaths

SECRET_MARKERS = ("api_key", "apikey", "secret", "token", "password", "private_key")


def validate_spec(spec: AgentEngineSpec, *, base_dir: Path) -> None:
    """Run semantic validation that cannot be expressed cleanly in JSON schema."""
    issues: list[ValidationIssue] = []

    _validate_graph(spec, issues)
    _validate_references(spec, issues)
    _validate_prompt_paths(spec, base_dir, issues)
    _validate_protected_access(spec, base_dir, issues)
    _validate_no_secrets(spec.model_dump(by_alias=True), issues)

    if issues:
        raise SpecValidationError(issues)


def _validate_graph(spec: AgentEngineSpec, issues: list[ValidationIssue]) -> None:
    if len(spec.graph) != 1:
        issues.append(
            ValidationIssue(
                path="$.graph",
                message=f"Graph must contain exactly one root; found {len(spec.graph)}",
            )
        )

    node_ids = spec.node_ids()
    seen_path: list[str] = []

    def walk(node: object, path: str) -> None:
        if node is None:
            return
        if not isinstance(node, dict):
            issues.append(
                ValidationIssue(
                    path=path,
                    message="Graph node value must be null or a nested mapping",
                )
            )
            return

        for node_id, children in node.items():
            child_path = f"{path}.{node_id}"
            if not isinstance(node_id, str) or not node_id:
                issues.append(
                    ValidationIssue(path=path, message="Graph node id must be a non-empty string")
                )
                continue
            if node_id not in node_ids:
                issues.append(
                    ValidationIssue(
                        path=child_path,
                        message=(
                            f"Unknown graph node '{node_id}'; declare it under "
                            "orchestrators or agents"
                        ),
                    )
                )
            if node_id in seen_path:
                cycle = " -> ".join([*seen_path, node_id])
                issues.append(
                    ValidationIssue(path=child_path, message=f"Graph cycle detected: {cycle}")
                )
                continue
            seen_path.append(node_id)
            walk(children, child_path)
            seen_path.pop()

    walk(spec.graph, "$.graph")


def _validate_references(spec: AgentEngineSpec, issues: list[ValidationIssue]) -> None:
    _validate_resolver_definitions(spec, issues)
    resolver_ids = set(spec.resolvers)
    tool_ids = set(spec.tools)
    mcp_ids = set(spec.mcps)

    for node_id, orchestrator in spec.orchestrators.items():
        _validate_resolver_refs(
            node_id=node_id,
            refs=orchestrator.resolvers,
            known=resolver_ids,
            path=f"$.orchestrators.{node_id}.resolvers",
            issues=issues,
        )
        if orchestrator.prompts.orchestrator is None:
            issues.append(
                ValidationIssue(
                    path=f"$.orchestrators.{node_id}.prompts.orchestrator",
                    message="Orchestrator prompt is required",
                )
            )

    for agent_id, agent in spec.agents.items():
        _validate_resolver_refs(
            node_id=agent_id,
            refs=agent.resolvers,
            known=resolver_ids,
            path=f"$.agents.{agent_id}.resolvers",
            issues=issues,
        )
        _validate_refs(
            refs=agent.tools,
            known=tool_ids,
            path=f"$.agents.{agent_id}.tools",
            noun="tool",
            owner=f"agent '{agent_id}'",
            issues=issues,
        )
        _validate_refs(
            refs=agent.mcps,
            known=mcp_ids,
            path=f"$.agents.{agent_id}.mcps",
            noun="MCP",
            owner=f"agent '{agent_id}'",
            issues=issues,
        )


def _validate_resolver_definitions(
    spec: AgentEngineSpec,
    issues: list[ValidationIssue],
) -> None:
    for resolver_id, resolver in spec.resolvers.items():
        if resolver.scope not in {"agent", "shared"}:
            issues.append(
                ValidationIssue(
                    path=f"$.resolvers.{resolver_id}.scope",
                    message=(
                        f"Resolver '{resolver_id}' has invalid scope '{resolver.scope}'; "
                        "expected 'agent' or 'shared'"
                    ),
                )
            )


def _validate_resolver_refs(
    *,
    node_id: str,
    refs: list[str],
    known: set[str],
    path: str,
    issues: list[ValidationIssue],
) -> None:
    _validate_refs(
        refs=refs,
        known=known,
        path=path,
        noun="resolver",
        owner=f"node '{node_id}'",
        issues=issues,
    )


def _validate_refs(
    *,
    refs: list[str],
    known: set[str],
    path: str,
    noun: str,
    owner: str,
    issues: list[ValidationIssue],
) -> None:
    for index, ref in enumerate(refs):
        if ref not in known:
            issues.append(
                ValidationIssue(
                    path=f"{path}[{index}]",
                    message=f"Unknown {noun} reference '{ref}' referenced by {owner}",
                )
            )


def _validate_prompt_paths(
    spec: AgentEngineSpec,
    base_dir: Path,
    issues: list[ValidationIssue],
) -> None:
    for node_id, orchestrator in spec.orchestrators.items():
        _validate_prompt_spec_paths(
            prompts=orchestrator.prompts,
            base_dir=base_dir,
            path=f"$.orchestrators.{node_id}.prompts",
            issues=issues,
        )

    for agent_id, agent in spec.agents.items():
        if agent.prompts is None:
            continue
        _validate_prompt_spec_paths(
            prompts=agent.prompts,
            base_dir=base_dir,
            path=f"$.agents.{agent_id}.prompts",
            issues=issues,
        )


def _validate_prompt_spec_paths(
    *,
    prompts: PromptSpec,
    base_dir: Path,
    path: str,
    issues: list[ValidationIssue],
) -> None:
    for field, prompt_path in prompts.paths().items():
        resolved = base_dir / prompt_path
        if not resolved.is_file():
            issues.append(
                ValidationIssue(
                    path=f"{path}.{field}",
                    message=f"Prompt file does not exist: {prompt_path}",
                )
            )


def _validate_protected_access(
    spec: AgentEngineSpec,
    base_dir: Path,
    issues: list[ValidationIssue],
) -> None:
    protected_nodes = [node_id for node_id, node in _iter_nodes(spec) if node.protected]
    if not protected_nodes:
        return

    if not ProjectPaths(base_dir).access_plugin.is_file():
        issues.append(
            ValidationIssue(
                path="$.agents",
                message=(
                    "Protected nodes require plugins/access.py with "
                    "AccessResolver.can_access(ctx, node_id); missing for "
                    f"{', '.join(sorted(protected_nodes))}"
                ),
            )
        )


def _iter_nodes(spec: AgentEngineSpec) -> list[tuple[str, OrchestratorSpec | AgentSpec]]:
    return [*spec.orchestrators.items(), *spec.agents.items()]


def _validate_no_secrets(data: object, issues: list[ValidationIssue], path: str = "$") -> None:
    if isinstance(data, dict):
        for key, value in data.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}"
            if _looks_secret(key_text):
                issues.append(
                    ValidationIssue(
                        path=child_path,
                        message="Hardcoded secret-like key is not allowed in YAML",
                    )
                )
            _validate_no_secrets(value, issues, child_path)
    elif isinstance(data, list):
        for index, value in enumerate(data):
            _validate_no_secrets(value, issues, f"{path}[{index}]")
    elif isinstance(data, str) and _looks_secret(data):
        issues.append(
            ValidationIssue(
                path=path,
                message="Hardcoded secret-like value is not allowed in YAML",
            )
        )


def _looks_secret(value: str) -> bool:
    normalized = value.lower().replace("-", "_")
    return any(marker in normalized for marker in SECRET_MARKERS)

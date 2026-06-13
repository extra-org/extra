from __future__ import annotations

from pathlib import Path

from agent_engine.core.errors import ValidationError
from agent_engine.core.spec import AgentSpec, GraphNode, OrchestratorSpec, SystemSpec


class SystemSpecValidator:
    """Engine-level validation: prompt files exist, access plugin present for protected nodes."""

    def validate(self, spec: SystemSpec, base_dir: Path) -> list[ValidationError]:
        errors: list[ValidationError] = []
        self._walk(spec.graph, base_dir, errors)
        return errors

    def _walk(self, node: GraphNode, base_dir: Path, errors: list[ValidationError]) -> None:
        self._validate_node(node, base_dir, errors)
        for child in node.children:
            self._walk(child, base_dir, errors)

    def _validate_node(
        self, node: GraphNode, base_dir: Path, errors: list[ValidationError]
    ) -> None:
        prompts = node.node.get_prompts()

        for field_name in ("system", "user"):
            path_str = getattr(prompts, field_name, None)
            if path_str and not (base_dir / path_str).is_file():
                errors.append(
                    ValidationError(
                        field=f"{node.node.id}.prompts.{field_name}",
                        message=f"Prompt file not found: {path_str}",
                    )
                )

        if isinstance(node.node, OrchestratorSpec):
            if node.node.prompts.orchestrator and not (
                base_dir / node.node.prompts.orchestrator
            ).is_file():
                errors.append(
                    ValidationError(
                        field=f"{node.node.id}.prompts.orchestrator",
                        message=f"Prompt file not found: {node.node.prompts.orchestrator}",
                    )
                )

        if node.node.protected and not (base_dir / "plugins" / "access.py").is_file():
            errors.append(
                ValidationError(
                    field=f"{node.node.id}.protected",
                    message="plugins/access.py is required for protected nodes",
                )
            )

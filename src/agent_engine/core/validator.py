from __future__ import annotations

from pathlib import Path

from agent_engine.core.errors import ValidationError
from agent_engine.core.plugin_stubs import scan_unimplemented_plugins
from agent_engine.core.spec import GraphNode, OrchestratorSpec, SystemSpec


class SystemSpecValidator:
    """Engine-level validation: prompt files exist, access plugin present for
    protected nodes, and no declared plugin is a generated-but-unimplemented stub."""

    def validate(self, spec: SystemSpec, base_dir: Path) -> list[ValidationError]:
        errors: list[ValidationError] = []
        self._walk(spec.graph, base_dir, errors)
        errors.extend(scan_unimplemented_plugins(spec, base_dir))
        return errors

    def _walk(self, node: GraphNode, base_dir: Path, errors: list[ValidationError]) -> None:
        self._validate_node(node, base_dir, errors)
        for child in node.children:
            self._walk(child, base_dir, errors)

    def _validate_node(
        self, node: GraphNode, base_dir: Path, errors: list[ValidationError]
    ) -> None:
        if node.node.protected and not (base_dir / "plugins" / "access.py").is_file():
            errors.append(
                ValidationError(
                    field=f"{node.node.id}.protected",
                    message="plugins/access.py is required for protected nodes",
                )
            )


"""Sprint 0 static source checks — verifies emergency fixes at the source level.

These are NOT end-to-end or integration tests. They parse source files directly
and check that specific patterns exist (or are absent). They run on Python 3.10+
without importing the project.

To run:  python -m pytest tests/test_sprint0_static_checks.py -v
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


# ── Helpers ──────────────────────────────────────────────────────────────────


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _read_src(rel: str) -> str:
    return (SRC / rel).read_text(encoding="utf-8")


# ── Task 1: Generic error responses ─────────────────────────────────────────


class TestGenericErrorResponses:
    """No str(exc) should leak into HTTP responses."""

    def test_no_str_exc_in_agent_engine_api(self) -> None:
        content = _read_src("agent_engine/api/app.py")
        # _map_approval_error uses str(exc) intentionally — approval errors carry only
        # safe identifiers (per approvals/errors.py:5). Strip that function before checking.
        lines = content.splitlines()
        skip = False
        filtered: list[str] = []
        for line in lines:
            if "def _map_approval_error" in line:
                skip = True
            elif skip:
                if line.strip() and not line[0].isspace():
                    skip = False
            if not skip:
                filtered.append(line)
        cleaned = "\n".join(filtered)
        assert "detail=str(exc)" not in cleaned, (
            "str(exc) leaked in agent_engine/api/app.py handler paths"
        )
        assert "'error': str(exc)" not in cleaned, "str(exc) leaked in SSE error event"

    def test_no_str_exc_in_agent_manager_routes(self) -> None:
        content = _read_src("agent_manager/api/routes.py")
        assert "detail=str(exc)" not in content, "str(exc) leaked in agent_manager/api/routes.py"
        assert "'error': str(exc)" not in content, "str(exc) leaked in SSE error event"

    def test_generic_error_string_in_agent_engine_api(self) -> None:
        content = _read_src("agent_engine/api/app.py")
        assert content.count("internal server error") >= 3, (
            "expected at least 3 generic error strings in agent_engine/api/app.py"
        )

    def test_generic_error_string_in_agent_manager_routes(self) -> None:
        content = _read_src("agent_manager/api/routes.py")
        assert content.count("internal server error") >= 2, (
            "expected at least 2 generic error strings in agent_manager/api/routes.py"
        )


# ── Task 2: Non-root Docker user ───────────────────────────────────────────


class TestDockerNonRoot:
    def test_user_agent_directive(self) -> None:
        content = _read("Dockerfile")
        assert "USER agent" in content, "Dockerfile missing 'USER agent' directive"

    def test_workspace_created_before_chown(self) -> None:
        content = _read("Dockerfile")
        # mkdir -p /workspace must appear before chown on the same line
        for line in content.splitlines():
            if "chown" in line and "mkdir" in line:
                assert line.index("mkdir") < line.index("chown"), (
                    "/workspace must be created before chown runs"
                )
                return
        pytest.fail("no line with both mkdir and chown found in Dockerfile")


# ── Task 3: Request size limits ────────────────────────────────────────────


class TestRequestSizeLimits:
    def test_invoke_request_message_max_length(self) -> None:
        tree = ast.parse(_read_src("agent_engine/api/app.py"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "InvokeRequest":
                for item in node.body:
                    if isinstance(item, ast.AnnAssign) and getattr(item.target, "id", None) == "message":
                        assert item.value is not None, "message field missing default value"
                        call = item.value
                        if isinstance(call, ast.Call):
                            for kw in call.keywords:
                                if kw.arg == "max_length":
                                    return
                        pytest.fail("InvokeRequest.message missing Field(max_length=...)")
        pytest.fail("InvokeRequest class not found")

    def test_send_message_request_max_length(self) -> None:
        tree = ast.parse(_read_src("agent_manager/api/schemas.py"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "SendMessageRequest":
                for item in node.body:
                    if isinstance(item, ast.AnnAssign) and getattr(item.target, "id", None) == "message":
                        assert item.value is not None
                        call = item.value
                        if isinstance(call, ast.Call):
                            for kw in call.keywords:
                                if kw.arg == "max_length":
                                    return
                        pytest.fail("SendMessageRequest.message missing Field(max_length=...)")
        pytest.fail("SendMessageRequest class not found")

    def test_id_fields_max_length(self) -> None:
        tree = ast.parse(_read_src("agent_manager/api/schemas.py"))
        id_fields_found = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name in ("CreateConversationRequest", "SendMessageRequest"):
                for item in node.body:
                    if isinstance(item, ast.AnnAssign) and getattr(item.target, "id", None) in ("user_id", "session_id"):
                        assert item.value is not None
                        call = item.value
                        if isinstance(call, ast.Call):
                            for kw in call.keywords:
                                if kw.arg == "max_length":
                                    id_fields_found += 1
                                    break
        assert id_fields_found >= 3, f"expected at least 3 id fields with max_length, found {id_fields_found}"


# ── Task 4: Default host 0.0.0.0 (Docker network model) ──────────────────


class TestDefaultHost:
    def test_agentctl_serve_default_host(self) -> None:
        content = _read_src("agentctl/main.py")
        assert 'default="0.0.0.0"' in content or "default='0.0.0.0'" in content, (
            "agentctl serve --host default should be 0.0.0.0"
        )

    def test_agent_manager_config_default_host(self) -> None:
        content = _read_src("agent_manager/config.py")
        assert 'host: str = "0.0.0.0"' in content or "host: str = '0.0.0.0'" in content, (
            "agent_manager Settings.host default should be 0.0.0.0"
        )


# ── Task 5: Docker Compose ────────────────────────────────────────────────


class TestDockerCompose:
    def test_file_exists(self) -> None:
        assert (ROOT / "docker-compose.yml").exists(), "docker-compose.yml not found"

    def test_has_engine_service(self) -> None:
        content = _read("docker-compose.yml")
        assert "engine:" in content

    def test_has_manager_service(self) -> None:
        content = _read("docker-compose.yml")
        assert "manager:" in content

    def test_engine_port(self) -> None:
        content = _read("docker-compose.yml")
        assert "8090:8090" in content

    def test_manager_port(self) -> None:
        content = _read("docker-compose.yml")
        assert "8100:8100" in content

    def test_healthchecks(self) -> None:
        content = _read("docker-compose.yml")
        assert content.count("healthcheck:") >= 2

    def test_host_bound_ports(self) -> None:
        content = _read("docker-compose.yml")
        assert "127.0.0.1:8090:8090" in content, "engine port should be bound to 127.0.0.1"
        assert "127.0.0.1:8100:8100" in content, "manager port should be bound to 127.0.0.1"

    def test_no_sqlite_volume(self) -> None:
        content = _read("docker-compose.yml")
        assert "manager-data:" not in content, "SQLite volume should not be in docker-compose (not part of deployment model)"

    def test_explicit_host_override(self) -> None:
        content = _read("docker-compose.yml")
        assert "--host" in content, "containers must override host to 0.0.0.0 explicitly"


# ── Syntax validation ─────────────────────────────────────────────────────


class TestSyntaxValidation:
    @pytest.mark.parametrize(
        "module",
        [
            "agent_engine/api/app.py",
            "agent_manager/api/routes.py",
            "agent_manager/api/schemas.py",
            "agentctl/main.py",
            "agent_manager/config.py",
        ],
    )
    def test_file_parses(self, module: str) -> None:
        source = _read_src(module)
        ast.parse(source, filename=module)

from __future__ import annotations

from pathlib import Path

import pytest

from agent_engine.core.provenance import compute_prompt_hash
from agent_engine.engine.langgraph.prompting import (
    load_file,
    load_file_with_provenance,
    render_prompt,
)


@pytest.fixture
def prompts_dir(tmp_path: Path) -> Path:
    prompt_dir = tmp_path / "prompts" / "billing"
    prompt_dir.mkdir(parents=True)
    system_file = prompt_dir / "system.md"
    system_file.write_text(
        "You are a billing assistant for {{company_name}}.\nContact: {{support_email}}",
        encoding="utf-8",
    )
    return tmp_path


class TestPromptFileLoadingWithProvenance:
    def test_load_existing_file_with_provenance(self, prompts_dir: Path) -> None:
        rel_path = "prompts/billing/system.md"
        resolved = load_file_with_provenance(prompts_dir, rel_path)

        assert resolved is not None
        assert "You are a billing assistant for {{company_name}}." in resolved.content
        assert resolved.provenance.source == "file"
        assert resolved.provenance.identifier == "prompts/billing/system.md"
        assert resolved.provenance.requested_ref is None
        assert resolved.provenance.resolved_revision is None
        assert resolved.provenance.content_hash == compute_prompt_hash(resolved.content)

    def test_load_file_with_provenance_normalizes_relative_path(self, prompts_dir: Path) -> None:
        rel_path = "prompts\\billing\\system.md"
        resolved = load_file_with_provenance(prompts_dir, rel_path)

        assert resolved is not None
        assert resolved.provenance.identifier == "prompts/billing/system.md"

    def test_load_file_with_either_separator_style_produces_equivalent_result(
        self, prompts_dir: Path
    ) -> None:
        from_forward = load_file_with_provenance(prompts_dir, "prompts/billing/system.md")
        from_backslash = load_file_with_provenance(prompts_dir, "prompts\\billing\\system.md")

        assert from_forward is not None
        assert from_backslash is not None
        assert from_forward.content == from_backslash.content
        assert from_forward.provenance == from_backslash.provenance
        assert from_backslash.provenance.identifier == "prompts/billing/system.md"

    def test_load_nonexistent_file_returns_none(self, prompts_dir: Path) -> None:
        resolved = load_file_with_provenance(prompts_dir, "prompts/missing.md")
        assert resolved is None

    def test_load_empty_or_none_path_returns_none(self, prompts_dir: Path) -> None:
        assert load_file_with_provenance(prompts_dir, None) is None
        assert load_file_with_provenance(prompts_dir, "") is None

    def test_load_directory_path_returns_none(self, prompts_dir: Path) -> None:
        assert load_file_with_provenance(prompts_dir, "prompts/billing") is None


class TestBackwardCompatibilityLoadFile:
    def test_load_file_returns_exact_content_string(self, prompts_dir: Path) -> None:
        content = load_file(prompts_dir, "prompts/billing/system.md")
        assert "You are a billing assistant for {{company_name}}." in content

    def test_load_file_missing_returns_empty_string(self, prompts_dir: Path) -> None:
        assert load_file(prompts_dir, "prompts/missing.md") == ""

    def test_load_file_none_or_empty_path_returns_empty_string(self, prompts_dir: Path) -> None:
        assert load_file(prompts_dir, None) == ""
        assert load_file(prompts_dir, "") == ""


class TestPromptProvenanceSeparationFromRenderedOutput:
    def test_rendering_interpolates_without_modifying_provenance(self, prompts_dir: Path) -> None:
        resolved = load_file_with_provenance(prompts_dir, "prompts/billing/system.md")
        assert resolved is not None

        context = {
            "company_name": "Acme Corp",
            "support_email": "billing@acme.example.com",
        }
        rendered = render_prompt(resolved.content, context)

        assert "You are a billing assistant for Acme Corp." in rendered
        assert "Contact: billing@acme.example.com" in rendered

        # The provenance source hash represents the raw template before variable substitution
        assert resolved.provenance.content_hash == compute_prompt_hash(resolved.content)
        assert resolved.provenance.content_hash != compute_prompt_hash(rendered)

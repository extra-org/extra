from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import PureWindowsPath

import pytest

from agent_engine.core.provenance import (
    PromptProvenance,
    ResolvedPrompt,
    compute_prompt_hash,
)


class TestDeterministicPromptHashing:
    def test_same_content_produces_identical_hash(self) -> None:
        content = "You are a helpful customer support agent for {{company}}."
        hash1 = compute_prompt_hash(content)
        hash2 = compute_prompt_hash(content)

        assert hash1 == hash2
        assert hash1.startswith("sha256:")
        assert len(hash1) == len("sha256:") + 64

    def test_content_changes_produce_different_hash(self) -> None:
        content_v1 = "You are a customer assistant."
        content_v2 = "You are a helpful customer assistant."

        assert compute_prompt_hash(content_v1) != compute_prompt_hash(content_v2)

    def test_whitespace_and_newline_differences_change_hash(self) -> None:
        base = "System prompt"
        with_trailing_space = "System prompt "
        with_newline = "System prompt\n"

        hash_base = compute_prompt_hash(base)
        assert compute_prompt_hash(with_trailing_space) != hash_base
        assert compute_prompt_hash(with_newline) != hash_base

    def test_utf8_multibyte_characters_handled_deterministically(self) -> None:
        content = "Support: こんにちは世界 🚀"
        hash1 = compute_prompt_hash(content)
        hash2 = compute_prompt_hash(content)

        assert hash1 == hash2
        assert hash1.startswith("sha256:")

    def test_empty_string_produces_known_sha256(self) -> None:
        # Standard SHA-256 of empty string is:
        # e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
        expected = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        assert compute_prompt_hash("") == expected


class TestPromptProvenanceModel:
    def test_local_file_provenance_creation(self) -> None:
        path = "prompts/billing/system.md"
        content = "You are a billing specialist."
        provenance = PromptProvenance.for_local_file(path, content)

        assert provenance.source == "file"
        assert provenance.identifier == "prompts/billing/system.md"
        assert provenance.content_hash == compute_prompt_hash(content)
        assert provenance.requested_ref is None
        assert provenance.resolved_revision is None

    def test_path_normalization_converts_backslashes_to_forward_slashes(self) -> None:
        windows_path = PureWindowsPath("prompts\\billing\\system.md")
        content = "You are a billing specialist."
        provenance = PromptProvenance.for_local_file(windows_path, content)

        assert provenance.identifier == "prompts/billing/system.md"

    def test_remote_compatible_representation_with_mutable_label(self) -> None:
        provenance = PromptProvenance(
            source="langfuse",
            identifier="billing-agent",
            requested_ref="production",
            resolved_revision="17",
            content_hash=compute_prompt_hash("Raw template v17"),
        )

        assert provenance.source == "langfuse"
        assert provenance.identifier == "billing-agent"
        assert provenance.requested_ref == "production"
        assert provenance.resolved_revision == "17"
        assert provenance.content_hash.startswith("sha256:")

    def test_remote_compatible_representation_with_pinned_version(self) -> None:
        provenance = PromptProvenance(
            source="langfuse",
            identifier="billing-agent",
            requested_ref="12",
            resolved_revision="12",
            content_hash=compute_prompt_hash("Raw template v12"),
        )

        assert provenance.requested_ref == "12"
        assert provenance.resolved_revision == "12"

    def test_provenance_is_immutable(self) -> None:
        provenance = PromptProvenance.for_local_file("prompts/system.md", "hello")

        with pytest.raises(FrozenInstanceError):
            provenance.identifier = "prompts/other.md"  # type: ignore[misc]

        with pytest.raises(FrozenInstanceError):
            provenance.resolved_revision = "99"  # type: ignore[misc]

    def test_to_dict_serialization(self) -> None:
        provenance = PromptProvenance(
            source="langfuse",
            identifier="billing-agent",
            requested_ref="production",
            resolved_revision="17",
            content_hash="sha256:123456",
        )
        data = provenance.to_dict()

        assert data == {
            "source": "langfuse",
            "identifier": "billing-agent",
            "requested_ref": "production",
            "resolved_revision": "17",
            "content_hash": "sha256:123456",
        }

    def test_sensitive_rendered_content_not_in_provenance(self) -> None:
        raw_template = "Hello {{customer_ssn}}"
        sensitive_rendered = "Hello 123-45-6789"

        provenance = PromptProvenance.for_local_file("prompts/agent.md", raw_template)

        # Provenance only records the source template hash, never the rendered content
        assert sensitive_rendered not in str(provenance)
        assert sensitive_rendered not in provenance.to_dict().values()
        assert provenance.content_hash == compute_prompt_hash(raw_template)
        assert provenance.content_hash != compute_prompt_hash(sensitive_rendered)


class TestResolvedPrompt:
    def test_resolved_prompt_structure_and_immutability(self) -> None:
        content = "Template content"
        provenance = PromptProvenance.for_local_file("prompts/sys.md", content)
        resolved = ResolvedPrompt(content=content, provenance=provenance)

        assert resolved.content == content
        assert resolved.provenance == provenance

        with pytest.raises(FrozenInstanceError):
            resolved.content = "Changed"  # type: ignore[misc]

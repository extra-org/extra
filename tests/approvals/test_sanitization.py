"""Sensitive-argument masking."""

from __future__ import annotations

from agent_engine.approvals.sanitization import REDACTED, mask_arguments, mask_sensitive


def test_masks_common_sensitive_keys() -> None:
    args = {
        "to": "a@b.com",
        "password": "hunter2",
        "api_key": "sk-1",
        "apiKey": "sk-2",
        "access_token": "at",
        "refresh_token": "rt",
        "authorization": "Bearer x",
        "cookie": "c",
        "amount": 10,
    }
    masked = mask_arguments(args)
    assert masked["to"] == "a@b.com"
    assert masked["amount"] == 10
    for key in (
        "password",
        "api_key",
        "apiKey",
        "access_token",
        "refresh_token",
        "authorization",
        "cookie",
    ):
        assert masked[key] == REDACTED


def test_recursive_masking_in_nested_structures() -> None:
    args = {"outer": {"token": "t", "keep": 1}, "list": [{"secret": "s"}, {"ok": 2}]}
    masked = mask_arguments(args)
    assert masked["outer"]["token"] == REDACTED
    assert masked["outer"]["keep"] == 1
    assert masked["list"][0]["secret"] == REDACTED
    assert masked["list"][1]["ok"] == 2


def test_original_arguments_unchanged() -> None:
    args = {"password": "hunter2", "nested": {"token": "t"}}
    mask_arguments(args)
    assert args["password"] == "hunter2"
    assert args["nested"]["token"] == "t"


def test_mask_sensitive_passes_scalars_through() -> None:
    assert mask_sensitive(5) == 5
    assert mask_sensitive("plain") == "plain"

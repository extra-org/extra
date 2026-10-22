"""Exact text roundtrips and grapheme boundaries, with no new test dependency."""

import random

import pytest

from agent_manager.infrastructure.channels.whatsapp.rendering import render_text


def test_rendering_prefers_paragraphs_and_preserves_every_character():
    assert render_text("aaaa\n\nbbbb", limit=6) == ("aaaa\n\n", "bbbb")


@pytest.mark.parametrize("cluster", ["שָׁ", "👩🏽‍💻", "🇮🇱", "é", "\r\n", "가"])
def test_rendering_never_splits_a_grapheme(cluster):
    size = len(cluster.encode("utf-16-le")) // 2
    text = "x" + cluster * 5 + "y"
    parts = render_text(text, limit=max(size, 2))
    assert "".join(parts) == text
    assert all(len(part.encode("utf-16-le")) // 2 <= max(size, 2) for part in parts)
    assert all(part.strip("xy").replace(cluster, "") == "" for part in parts)


def test_rendering_roundtrip_bounds_and_determinism_over_unicode_corpus():
    rng = random.Random(771)
    alphabet = ["א", "שָׁ", "a", "👨‍👩‍👧‍👦", "🇮🇱", "\n", " ", "é", "中文", "\u200f"]
    for _ in range(200):
        text = "x" + "".join(rng.choices(alphabet, k=rng.randrange(1, 100))) + "y"
        parts = render_text(text, limit=32)
        assert "".join(parts) == text
        assert all(0 < len(part.encode("utf-16-le")) // 2 <= 32 for part in parts)
        assert render_text(text, limit=32) == parts


@pytest.mark.parametrize("text", ["", "   ", "private\x00text", "x" + "́" * 4096])
def test_unrenderable_input_is_rejected_without_truncation(text):
    with pytest.raises(ValueError):
        render_text(text)


def test_rendering_caps_part_count():
    with pytest.raises(ValueError):
        render_text("x" * 129, limit=1)

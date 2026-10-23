"""Plain text, exact grapheme boundaries and conservative UTF-16 bounds."""

import regex

from agent_manager.domain.channels.models import bounded_text


def render_text(text: str, *, limit: int = 4096) -> tuple[str, ...]:
    bounded_text(text)
    if not text.strip() or not 1 <= limit <= 4096:
        raise ValueError("invalid WhatsApp text or part bound")
    clusters = regex.findall(r"\X", text)
    parts: list[str] = []
    start = 0
    while start < len(clusters):
        end, units, paragraph = start, 0, None
        while end < len(clusters):
            size = len(clusters[end].encode("utf-16-le")) // 2
            if units + size > limit:
                break
            units += size
            end += 1
            if end >= 2 and clusters[end - 2 : end] == ["\n", "\n"]:
                paragraph = end
        if end == start:
            raise ValueError("one grapheme exceeds the WhatsApp part bound")
        if end < len(clusters) and paragraph is not None:
            end = paragraph
        parts.append("".join(clusters[start:end]))
        start = end
        if len(parts) > 128:
            raise ValueError("WhatsApp reply exceeds 128 parts")
    return tuple(parts)

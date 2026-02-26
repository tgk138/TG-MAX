"""Text formatting helpers for Telegram -> MAX and web preview."""

from __future__ import annotations

import re

_BULLET_RE = re.compile(r"(?m)^\s*[•●◦]\s+")
_MARKDOWN_HINT_RE = re.compile(
    r"(\*\*[\s\S]+?\*\*|__[\s\S]+?__|~~[\s\S]+?~~|`[^`\n]+`|\[[^\]]+\]\(https?://[^)]+\))"
)


def normalize_tg_text(text: str | None) -> str | None:
    """Normalize Telegram text while preserving user formatting."""
    if not text:
        return None
    cleaned = text.replace("\r\n", "\n").strip()
    if not cleaned:
        return None
    # MAX markdown works better with regular '-' list markers.
    cleaned = _BULLET_RE.sub("- ", cleaned)
    return cleaned


def to_max_text_payload(text: str | None) -> tuple[str | None, str | None]:
    """Return normalized text and optional MAX format ('markdown' or None)."""
    normalized = normalize_tg_text(text)
    if not normalized:
        return None, None
    format_ = "markdown" if _MARKDOWN_HINT_RE.search(normalized) else None
    return normalized, format_


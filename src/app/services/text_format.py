"""Text formatting helpers for Telegram -> MAX and web preview."""

from __future__ import annotations

import re

_BULLET_RE = re.compile(r"(?m)^\s*[•●◦]\s+")
_MARKDOWN_HINT_RE = re.compile(
    r"(\*\*[^*\n]+\*\*|__[^_\n]+__|~~[^~\n]+~~|`[^`\n]+`|\[[^\]]+\]\(https?://[^)]+\))"
)


def normalize_tg_text(text: str | None, entities=None) -> str | None:
    """Normalize Telegram text, preserving formatting from entities as markdown.

    If entities are provided (from Telethon message), converts them to
    Telegram-style markdown (**bold**, __italic__, etc.).
    """
    if not text:
        return None

    if entities:
        try:
            from telethon.extensions import markdown as tg_md
            text = tg_md.unparse(text, entities)
        except Exception:
            pass

    cleaned = text.replace("\r\n", "\n").strip()
    if not cleaned:
        return None
    cleaned = _BULLET_RE.sub("- ", cleaned)
    return cleaned


def _tg_markdown_to_max_markdown(text: str) -> str:
    """Convert Telegram-style markdown to MAX-style markdown.

    MAX API uses different syntax (dev.max.ru/docs-api):
      Bold:          *text*   (Telegram uses **text**)
      Italic:        _text_   (same)
      Strikethrough: ~text~   (Telegram uses ~~text~~)
      Code:          `text`   (same)
    """
    content = text

    # Preserve code blocks and inline code from conversion
    blocks: list[str] = []
    def _save_block(m):
        token = f"\x00CB{len(blocks)}\x00"
        blocks.append(m.group(0))
        return token
    content = re.sub(r"```[\s\S]*?```", _save_block, content)

    codes: list[str] = []
    def _save_code(m):
        token = f"\x00IC{len(codes)}\x00"
        codes.append(m.group(0))
        return token
    content = re.sub(r"`[^`\n]+`", _save_code, content)

    # **bold** → *bold* (same line only, no * inside)
    content = re.sub(r"\*\*([^*\n]+)\*\*", r"*\1*", content)
    # __bold__ → *bold*
    content = re.sub(r"__([^_\n]+)__", r"*\1*", content)

    # ~~strike~~ → ~strike~ (same line only)
    content = re.sub(r"~~([^~\n]+)~~", r"~\1~", content)

    # _italic_ stays as _italic_ (same syntax)
    # `code` stays as `code` (same syntax)
    # [text](url) → just keep as text (url) since MAX markdown may not support links
    content = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        r"\1 (\2)",
        content,
    )

    # Restore code blocks and inline code
    for i, block in enumerate(blocks):
        content = content.replace(f"\x00CB{i}\x00", block)
    for i, code in enumerate(codes):
        content = content.replace(f"\x00IC{i}\x00", code)

    return content


def to_max_text_payload(text: str | None) -> tuple[str | None, str | None]:
    """Return text and format for MAX API.

    Converts Telegram markdown to MAX markdown syntax:
      **bold** → *bold*, ~~strike~~ → ~strike~
    Returns format='markdown' so MAX renders formatting.
    """
    normalized = normalize_tg_text(text)
    if not normalized:
        return None, None
    if _MARKDOWN_HINT_RE.search(normalized):
        return _tg_markdown_to_max_markdown(normalized), "markdown"
    return normalized, None


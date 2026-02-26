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
    cleaned = _BULLET_RE.sub("- ", cleaned)
    return cleaned


def _markdown_to_html(text: str) -> str:
    """Convert Telegram-style markdown to HTML for MAX API.

    MAX API renders HTML reliably; markdown support is inconsistent.
    """
    import html as html_mod

    content = text

    # Preserve code blocks
    blocks: list[str] = []
    def _save_block(m):
        token = f"\x00CB{len(blocks)}\x00"
        blocks.append(f"<pre><code>{html_mod.escape(m.group(1))}</code></pre>")
        return token
    content = re.sub(r"```([\s\S]*?)```", _save_block, content)

    # Inline code (before escaping)
    codes: list[str] = []
    def _save_code(m):
        token = f"\x00IC{len(codes)}\x00"
        codes.append(f"<code>{html_mod.escape(m.group(1))}</code>")
        return token
    content = re.sub(r"`([^`\n]+)`", _save_code, content)

    # Escape HTML
    content = html_mod.escape(content)

    # Links
    content = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        r'<a href="\2">\1</a>',
        content,
    )

    # Bold (multiline-safe)
    content = re.sub(r"\*\*([\s\S]+?)\*\*", r"<b>\1</b>", content)
    content = re.sub(r"__([\s\S]+?)__", r"<b>\1</b>", content)

    # Strikethrough
    content = re.sub(r"~~([\s\S]+?)~~", r"<s>\1</s>", content)

    # Italic (single * or _), avoid matching bold markers
    content = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", content)
    content = re.sub(r"(?<!_)_([^_\n]+)_(?!_)", r"<i>\1</i>", content)

    # Restore code blocks and inline codes
    for i, block in enumerate(blocks):
        content = content.replace(f"\x00CB{i}\x00", block)
    for i, code in enumerate(codes):
        content = content.replace(f"\x00IC{i}\x00", code)

    return content


def to_max_text_payload(text: str | None) -> tuple[str | None, str | None]:
    """Return text and format for MAX API.

    If text contains markdown formatting, converts to HTML and returns
    format='html'. Otherwise returns plain text with no format.
    """
    normalized = normalize_tg_text(text)
    if not normalized:
        return None, None
    if _MARKDOWN_HINT_RE.search(normalized):
        return _markdown_to_html(normalized), "html"
    return normalized, None


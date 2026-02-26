"""Text formatting helpers for Telegram -> MAX and web preview."""

from __future__ import annotations

import html as html_mod
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


def _tg_markdown_to_html(text: str) -> str:
    """Convert Telegram-style markdown to HTML for MAX API.

    Confirmed via live test: MAX API only renders format='html'.
    format='markdown' does NOT work despite documentation.
    """
    content = text

    # Preserve code blocks
    blocks: list[str] = []
    def _save_block(m):
        token = f"\x00CB{len(blocks)}\x00"
        blocks.append(f"<pre><code>{html_mod.escape(m.group(1))}</code></pre>")
        return token
    content = re.sub(r"```([\s\S]*?)```", _save_block, content)

    # Preserve inline code
    codes: list[str] = []
    def _save_code(m):
        token = f"\x00IC{len(codes)}\x00"
        codes.append(f"<code>{html_mod.escape(m.group(1))}</code>")
        return token
    content = re.sub(r"`([^`\n]+)`", _save_code, content)

    # Escape HTML entities
    content = html_mod.escape(content)

    # Links [text](url) → <a href="url">text</a>
    content = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        r'<a href="\2">\1</a>',
        content,
    )

    # Bold **text** → <b>text</b> (same line only)
    content = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", content)
    # Bold __text__ → <b>text</b>
    content = re.sub(r"__([^_\n]+)__", r"<b>\1</b>", content)
    # Strikethrough ~~text~~ → <s>text</s>
    content = re.sub(r"~~([^~\n]+)~~", r"<s>\1</s>", content)
    # Italic _text_ (avoid matching __ already converted)
    content = re.sub(r"(?<!_)_([^_\n]+)_(?!_)", r"<i>\1</i>", content)

    # Restore preserved tokens
    for i, block in enumerate(blocks):
        content = content.replace(f"\x00CB{i}\x00", block)
    for i, code in enumerate(codes):
        content = content.replace(f"\x00IC{i}\x00", code)

    return content


def to_max_text_payload(text: str | None) -> tuple[str | None, str | None]:
    """Return text and format for MAX API.

    Converts Telegram markdown to HTML. MAX API only renders format='html'
    (confirmed: format='markdown' does NOT work despite documentation).
    """
    normalized = normalize_tg_text(text)
    if not normalized:
        return None, None
    if _MARKDOWN_HINT_RE.search(normalized):
        return _tg_markdown_to_html(normalized), "html"
    return normalized, None

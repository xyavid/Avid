"""HTML to text without a third-party parser: block selection by link density.

The kernel keeps one runtime dependency (httpx), so content selection is a heuristic rather than
readability: prefer a ``<main>``/``<article>`` region, then drop blocks whose characters mostly
sit inside links. The bound is deliberate and known — a short stray label can survive, and a body
built by JavaScript cannot be recovered at all, so that case is reported by
``looks_like_shell`` instead of being handed back as content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

#: Elements whose text is never content.
SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "svg", "head", "iframe"})

#: Tags that end the current block, so text is assembled per paragraph-like unit.
BLOCK_TAGS = frozenset(
    {
        "p", "div", "br", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6",
        "section", "article", "main", "header", "footer", "nav", "aside", "pre", "blockquote",
        "ul", "ol", "dl", "dd", "dt", "table", "form", "figure", "figcaption", "caption",
    }
)

#: Regions that usually hold the article rather than the chrome around it.
MAIN_TAGS = frozenset({"main", "article"})

#: A block is navigation when at least this share of its characters sits inside links.
LINK_DENSITY_LIMIT = 0.5

#: A ``<main>`` region is only trusted when it carries at least this share of the kept text.
MAIN_SHARE = 0.15
MAIN_FLOOR = 200

#: Verdict thresholds for a page whose text is missing because the body is rendered elsewhere.
SHELL_HTML_FLOOR = 4000
SHELL_TEXT_FLOOR = 600
SHELL_RATIO = 0.02

_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Block:
    """One paragraph-like unit of text, with how much of it sat inside links."""

    tag: str
    text: str
    chars: int
    link_chars: int
    in_main: bool


class _BlockParser(HTMLParser):
    """Collects blocks in document order, tagging each one with whether it sits in a main region."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._blocks: list[Block] = []
        self._depth = 0
        self._skip = 0
        self._main_depth: int | None = None
        self._links = 0
        self._tag = "body"
        self._parts: list[str] = []
        self._chars = 0
        self._link_chars = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._depth += 1
        if tag in SKIP_TAGS:
            self._skip += 1
            return
        if tag in BLOCK_TAGS:
            self._flush()
            self._tag = tag
        if tag == "a":
            self._links += 1
        # After the flush: a block opened before <main> must not be credited to the region.
        if tag in MAIN_TAGS and self._main_depth is None:
            self._main_depth = self._depth

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        else:
            if tag == "a":
                self._links = max(0, self._links - 1)
            if tag in BLOCK_TAGS:
                self._flush()
            if tag in MAIN_TAGS and self._main_depth == self._depth:
                self._main_depth = None
        self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        self._parts.append(data)
        length = len(_WHITESPACE.sub(" ", data).strip())
        self._chars += length
        if self._links:
            self._link_chars += length

    def finish(self) -> list[Block]:
        """Flush the trailing block; an unclosed skip element drops its buffer instead."""
        if self._skip:
            self._parts = []
            self._chars = 0
            self._link_chars = 0
        else:
            self._flush()
        return self._blocks

    def _flush(self) -> None:
        raw = "".join(self._parts)
        chars, link_chars = self._chars, self._link_chars
        self._parts = []
        self._chars = 0
        self._link_chars = 0
        text = _WHITESPACE.sub(" ", raw).strip()
        if not text:
            return
        self._blocks.append(
            Block(
                tag=self._tag,
                text=text,
                chars=chars or len(text),
                link_chars=link_chars,
                in_main=self._main_depth is not None,
            )
        )


def blocks(html: str) -> list[Block]:
    """Every block of the document in order, unfiltered — the input to the selection rule."""
    parser = _BlockParser()
    parser.feed(html)
    parser.close()
    return parser.finish()


def looks_like_shell(text: str, html_chars: int) -> bool:
    """True when a page carried real markup but almost no text, which is what a client-rendered
    body looks like from the outside; below ``SHELL_HTML_FLOOR`` the ratio means nothing."""
    if html_chars < SHELL_HTML_FLOOR:
        return False
    return len(text) < max(SHELL_TEXT_FLOOR, html_chars * SHELL_RATIO)


def _keep(block: Block) -> bool:
    if len(block.text) < 2:
        return False
    return not (block.chars and block.link_chars / block.chars >= LINK_DENSITY_LIMIT)


def _length(kept: list[Block]) -> int:
    return sum(len(block.text) for block in kept)


def _join(kept: list[Block]) -> str:
    lines: list[str] = []
    for block in kept:
        if block.text != (lines[-1] if lines else None):
            lines.append(block.text)
    return "\n".join(lines)


def main_text(html: str) -> str:
    """The article-ish text of ``html``: chrome dropped, a trusted main region preferred."""
    kept = [block for block in blocks(html) if _keep(block)]
    region = [block for block in kept if block.in_main]
    outside = [block for block in kept if not block.in_main]
    if region and _length(region) >= max(MAIN_FLOOR, MAIN_SHARE * _length(outside)):
        return _join(region)
    return _join(kept)

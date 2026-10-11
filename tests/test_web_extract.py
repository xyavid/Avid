"""HTML -> text extraction: navigation is dropped by link density, prose survives, and a
page whose body is rendered by JavaScript is reported instead of returned as content.

The fixtures are structural recreations of three real shapes (chrome-heavy article, docs page,
client-rendered shell); the measurements against the real pages live in the phase-67 evidence log.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from avid.agent.tools.extract import blocks, looks_like_shell, main_text

FIXTURES = Path(__file__).parent / "fixtures" / "html"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---- chrome-heavy article (no <main>: the article sits in a content div) ----


def test_chrome_heavy_page_keeps_prose_and_drops_link_lists():
    html = fixture("chrome-heavy-article.html")
    text = main_text(html)

    assert "Web scraping, web harvesting, or web data extraction" in text
    assert "A related practice is web crawling" in text


@pytest.mark.parametrize(
    "navigation_text",
    [
        "Random article",  # header menu
        "Community portal",  # header menu
        "Create account",  # personal tools
        "Metasearch engine",  # "See also" link list
        "A standard reference on crawling",  # references
        "Internet terminology",  # categories
    ],
)
def test_chrome_heavy_page_drops_every_link_dense_block(navigation_text: str):
    assert navigation_text not in main_text(fixture("chrome-heavy-article.html"))


def test_inline_links_inside_prose_are_kept():
    """A paragraph with one or two inline links is prose, not navigation; the sentence around
    them must survive, otherwise the extractor would eat the article body."""
    text = main_text(fixture("chrome-heavy-article.html"))

    assert "screen scraping" in text
    assert "for later retrieval or analysis" in text


# ---- docs page (<main> wrapper: navigation and footer live outside it) ----


def test_main_region_is_preferred_and_chrome_outside_it_is_gone():
    text = main_text(fixture("main-article.html"))

    assert "Fetching returns the decoded body of one URL" in text
    assert "print(result.text)" in text


@pytest.mark.parametrize(
    "chrome_text",
    ["Client reference", "Changelog", "Quick start", "Privacy", "Terms"],
)
def test_chrome_outside_the_main_region_is_dropped(chrome_text: str):
    assert chrome_text not in main_text(fixture("main-article.html"))


def test_prose_after_an_inline_link_in_the_main_region_survives():
    text = main_text(fixture("main-article.html"))

    assert "byte and time budgets that apply to every request" in text


# ---- client-rendered shell ----


def test_js_shell_is_flagged_and_its_bundle_never_becomes_text():
    html = fixture("js-shell.html")
    text = main_text(html)

    assert looks_like_shell(text, len(html)) is True
    assert "hydrate(" not in text
    assert "Doc section 1" not in text


def test_a_substantial_article_is_not_flagged_as_a_shell():
    """Real pages carry much more markup than text; the ratio must not fire on a real article.
    The fixtures stay small on purpose, so this case builds the markup bulk itself."""
    prose = "<p>" + "The quick brown fox jumps over the lazy dog. " * 80 + "</p>"
    padding = "<!-- filler markup -->" * 500
    html = f"<html><body>{prose}{padding}</body></html>"

    text = main_text(html)

    assert len(text) > 3000
    assert looks_like_shell(text, len(html)) is False


def test_tiny_pages_are_never_flagged_as_shells():
    """Below the floor there is not enough HTML for the ratio to mean anything, so a short
    plain page must not be reported as client-rendered."""
    assert looks_like_shell("short page", 900) is False


# ---- parser invariants ----


def test_scripts_and_styles_never_reach_the_text():
    html = "<html><head><style>.a{color:red}</style></head><body><p>hello</p><script>var x=1</script></body></html>"
    text = main_text(html)

    assert "hello" in text
    assert "color:red" not in text
    assert "var x" not in text


def test_blocks_carry_link_density_for_the_selection_rule():
    html = '<p>plain prose here</p><ul><li><a href="/a">only a link</a></li></ul>'
    collected = blocks(html)

    density = {block.text: (block.link_chars / block.chars if block.chars else 0) for block in collected}
    assert density["plain prose here"] == 0
    assert density["only a link"] == 1


def test_whitespace_is_collapsed_and_entities_decoded():
    html = "<p>a &amp; b\n\n   c</p>"
    assert main_text(html) == "a & b c"

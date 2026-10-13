"""G1 - G4 documentation gates: the resident budget, reference resolution, section points
and index completeness -- held without running anything."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

#: The resident layer enters the bootstrap block of every run, so its budget is a prompt
#: cap, not a style preference. `avid/agent/prompt.py` truncates the block at
#: `AGENTS_MD_MAX_CHARS` (16_000): staying well under it keeps the resident instructions
#: whole instead of handing the model a truncated contract.
AGENTS_MAX_CHARS = 6_000

#: On-demand files are read once per relevant task; a bigger cap only bounds drift.
DOCS_MAX_CHARS = 9_000


def _agents() -> Path:
    return ROOT / "AGENTS.md"


def _docs_files() -> list[Path]:
    return sorted(p for p in DOCS.rglob("*.md") if p.is_file())


def _py_files() -> list[Path]:
    return sorted((ROOT / "avid").rglob("*.py")) + sorted((ROOT / "tests").rglob("*.py"))


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------- G1: size ----------------

def test_agents_md_stays_within_the_resident_budget():
    actual = len(_text(_agents()))
    assert actual <= AGENTS_MAX_CHARS, (
        f"AGENTS.md 有 {actual} 字符，超过常驻预算 {AGENTS_MAX_CHARS}。"
        "它是每个运行的 bootstrap 块：把描述性内容下沉到 docs/，别在这里展开。"
    )


def test_docs_files_stay_readable_in_one_sitting():
    for path in _docs_files():
        actual = len(_text(path))
        assert actual <= DOCS_MAX_CHARS, (
            f"{path.relative_to(ROOT)} 有 {actual} 字符，超过 {DOCS_MAX_CHARS}；"
            "一份文档装一个主题，超出说明它该拆。"
        )


# ---------------- G2: references resolve ----------------

#: Three documented notations, all anchored to code symbols rather than line numbers (line
#: counts rot as soon as the file grows):
#:   `path.py:symbol`        — inline
#:   `path.py` 的 `symbol`    — prose
#:   `path.py` 的 SYMBOL      — prose, no backticks on the symbol
_REF_INLINE = re.compile(
    r"(?<![\w/.-])([A-Za-z][\w.-]*/[\w./-]+\.(?:py|ts|tsx|css|json)):([A-Za-z_][\w.]*)(?:\([^)]*\))?"
)
_REF_PROSE = re.compile(
    r"([A-Za-z][\w.-]*/[\w./-]+\.(?:py|ts|tsx|css))`?\s*的\s*`?([A-Za-z_][\w.]*)"
)


def _resolve(rel: str) -> Path | None:
    for prefix in ("", "avid/", "web/src/", "avid/web/"):
        candidate = ROOT / prefix / rel
        if candidate.exists():
            return candidate
    return None


def _markdown_targets(text: str) -> list[str]:
    return re.findall(r"\]\(([^)#]+?)(?:#[^)]*)?\)", text)


def test_every_symbol_reference_in_markdown_resolves():
    checked = 0
    for path in [_agents(), *_docs_files()]:
        text = _text(path)
        pairs = _REF_INLINE.findall(text) + _REF_PROSE.findall(text)
        for rel, symbol in pairs:
            target = _resolve(rel)
            if target is None:
                pytest.fail(f"{path.relative_to(ROOT)} 引用了不存在的文件：{rel}")
            body = _text(target)
            #: `Class.method` appears in code as `def method`, so compare the tail and the
            #: qualifier separately instead of demanding the dotted literal.
            assert all(part in body for part in symbol.split(".")), (
                f"{path.relative_to(ROOT)} 引用了 {rel}:{symbol}，但那里没有这个符号；"
                "要么代码改名了，要么文档在指一个从未存在过的东西。"
            )
            checked += 1
    assert checked > 0, "一条符号引用都没匹配到：要么文档没有引用，要么正则失效"


def test_every_backticked_repo_path_exists():
    checked = 0
    for path in [_agents(), *_docs_files()]:
        for match in re.finditer(
            r"`([A-Za-z][\w.-]*/[\w./-]+\.(?:py|ts|tsx|css|svg|json))(?::[\w.()…-]+)?`", _text(path)
        ):
            rel = match.group(1)
            if _resolve(rel) is None:
                pytest.fail(f"{path.relative_to(ROOT)} 引用了不存在的路径：{rel}")
            checked += 1
    assert checked > 0, "一条路径引用都没匹配到：文档与代码脱锚了"


def test_every_markdown_link_target_exists():
    for path in [_agents(), *_docs_files()]:
        for href in _markdown_targets(_text(path)):
            if href.startswith(("http://", "https://", "mailto:")):
                continue
            anchor = href.split("#", 1)[0]
            if not anchor:
                continue
            assert (path.parent / anchor).exists(), (
                f"{path.relative_to(ROOT)} 链接到不存在的 {anchor}"
            )


# ---------------- G3: section pointers ----------------

_SECTION = re.compile(r"AGENTS\.md\s*§\s*(\d+)")


def _agents_heading_numbers() -> set[str]:
    return {m.group(1) for m in re.finditer(r"^## (\d+)\.", _text(_agents()), re.MULTILINE)}


def test_section_pointers_in_code_resolve_to_a_real_heading():
    headings = _agents_heading_numbers()
    checked = 0
    for path in _py_files():
        for quoted in _SECTION.findall(_text(path)):
            assert quoted in headings, (
                f"{path.relative_to(ROOT)} 引用了 AGENTS.md §{quoted}，"
                f"但现有标题是 §{sorted(headings)}"
            )
            checked += 1
    assert checked > 0, "一条 §引用都没匹配到：正则失效"


# ---------------- G4: the index has no orphans ----------------

def test_every_docs_file_is_reachable_from_the_index():
    index = _text(DOCS / "README.md")
    for path in _docs_files():
        if path.name == "README.md":
            continue
        assert f"]({path.name})" in index, (
            f"docs/{path.name} 没有出现在 docs/README.md 的索引里；"
            "没被索引的文档靠搜索才会被发现，等于不存在。"
        )


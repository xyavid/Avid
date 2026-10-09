"""Path resolution helpers: the workspace is the default place to work, not a security bound.

It resolves paths, identifies targets and tests workspace membership, but never decides
permissions.

"""


from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from pathlib import Path

# Fixed at import time; tests replace it with a temporary directory.
WORKSPACE_ROOT = Path.cwd()

# Command tokens that may carry a path, split on blanks and shell metacharacters.
_TOKEN_SPLIT = re.compile(r"[\s;|&()<>'\"]+")
#: Shell quote characters and metacharacters that end a token.
_QUOTE_CHARS = "'\""
_META_CHARS = ";|&()<>"
#: Prefixes that let a quoted span pass as one absolute path (`~/` equals `$HOME/`).
_QUOTED_WHOLE_PREFIXES = ("/", "~", "$HOME")
_PATHLIKE = re.compile(r"(?:^|/)(?:\.\.?)(?:/|$)")
#: Windows absolute/UNC shapes: a drive letter (C:\ or C:/) and \\server\share.
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]|^\\\\")


def _base(root: Path | None = None) -> Path:
    """Returns the resolved base directory, defaulting to the process workspace root."""
    return (root or WORKSPACE_ROOT).resolve()


def is_within(path: Path, base: Path) -> bool:
    """Reports whether a path is the base itself or lies inside it."""
    return path == base or base in path.parents


def resolve(
    raw: str,
    *,
    root: Path | None = None,
    outside_ok: Callable[[Path], bool] | bool | None = None,
) -> tuple[Path | None, str | None]:
    """Resolves a path to an absolute one inside the workspace, or returns an error line.

    ``outside_ok`` carries the permission layer's verdict and defaults to false.
    """
    text = raw.strip()
    if not text:
        return None, "路径为空"

    base = _base(root)
    path = Path(text)
    if not path.is_absolute():
        path = base / path
    path = path.resolve()

    if not is_within(path, base):
        allowed = outside_ok(path) if callable(outside_ok) else bool(outside_ok)
        if not allowed:
            return None, f"拒绝访问工作区外的路径：{raw}"
    return path, None


def relative(path: Path, *, root: Path | None = None) -> str:
    """Renders a path relative to the workspace, which is shorter and stable for the model."""
    base = _base(root)
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def target_path(raw: str, *, root: Path | None = None) -> Path:
    """Resolves a path literal to an absolute path without any boundary check."""
    base = _base(root)
    path = Path(raw.strip())
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def outside_target(raw: str, *, root: Path | None = None) -> str | None:
    """Returns the absolute path when a file tool's target lies outside the workspace."""
    text = raw.strip()
    if not text:
        return None
    base = _base(root)
    path = target_path(text, root=base)
    return None if is_within(path, base) else str(path)


def _candidate(token: str, base: Path) -> Path | None:
    """Reads one command token as a path, or None when it does not look like one; relative
    paths count too, since a rule may target them."""
    token = token.strip().rstrip(",;)")
    if not token:
        return None
    if token.startswith("-"):
        _, sep, value = token.partition("=")
        if not sep:
            return None
        token = value.strip().rstrip(",;)")
        if not token:
            return None
    if "://" in token:
        return None
    if _WINDOWS_ABSOLUTE.search(token):
        # Absolute/UNC paths: ntpath on Windows; on POSIX a literal name (conservative).
        return Path(token)
    if token.startswith("~"):
        return Path.home() / token[2:] if token.startswith("~/") else Path.home()
    if token.startswith("$HOME"):
        return Path.home() / token[6:] if token.startswith("$HOME/") else Path.home()
    if token.startswith("/"):
        return Path(token)
    if token.startswith("._"):
        # The attribute name inside type(e).__name__, not a dotfile in the workspace.
        return None
    if (
        token == ".."
        or token.startswith("../")
        or _PATHLIKE.search(token)
        or "/" in token
        or "\\" in token
        or token.startswith(".")
    ):
        return base / token
    return None


def _iter_marks(command: str) -> Iterator[tuple[str, bool]]:
    """Splits a command into tokens, recording whether each held a quotation, so blanks inside
    quotes do not separate."""
    buffer: list[str] = []
    quoted = False
    index = 0
    length = len(command)
    while index < length:
        char = command[index]
        if char in _QUOTE_CHARS:
            end = command.find(char, index + 1)
            if end < 0:  # Unclosed quote: the rest is content, over-scanning rather than under.
                quoted = True
                buffer.append(command[index + 1 :])
                break
            quoted = True
            buffer.append(command[index + 1 : end])
            index = end + 1
            continue
        if char.isspace() or char in _META_CHARS:
            if buffer:
                yield "".join(buffer), quoted
                buffer, quoted = [], False
            index += 1
            continue
        buffer.append(char)
        index += 1
    if buffer:
        yield "".join(buffer), quoted


def _mark_paths(mark: str, quoted: bool, base: Path) -> list[Path]:
    """Returns the paths one token may denote, trying the whole token first, since a quoted
    absolute path keeps its spaces."""
    if quoted and mark.startswith(_QUOTED_WHOLE_PREFIXES):
        whole = _candidate(mark, base)
        if whole is not None:
            return [whole]
    paths: list[Path] = []
    for token in _TOKEN_SPLIT.split(mark):
        if quoted and token.startswith(".") and "/" not in token:
            continue
        candidate = _candidate(token, base)
        if candidate is not None:
            paths.append(candidate)
    return paths


def outside_command_target(command: str, *, root: Path | None = None) -> str | None:
    """Returns the first path in a command that lies outside the workspace — a heuristic over
    literal tokens, so a guard rail rather than a barrier."""
    if not isinstance(command, str) or not command.strip():
        return None
    base = _base(root)
    for resolved in command_targets(command, root=base):
        if not is_within(resolved, base):
            return str(resolved)
    return None


def command_targets(command: str, *, root: Path | None = None) -> tuple[Path, ...]:
    """Scans every path-like token of a command, resolved, ordered and de-duplicated; the
    outside check and target identification share it so they cannot drift."""
    if not isinstance(command, str) or not command.strip():
        return ()
    base = _base(root)

    found: list[Path] = []
    for mark, quoted in _iter_marks(command):
        for candidate in _mark_paths(mark, quoted, base):
            try:
                resolved = candidate.resolve()
            except (OSError, RuntimeError):  # pragma: no cover - depends on the filesystem
                continue
            if resolved not in found:
                found.append(resolved)
    return tuple(found)

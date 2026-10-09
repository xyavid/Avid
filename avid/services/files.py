"""Read-only backend of the workspace-files panel: paths resolve through the tools' own
``workspace.resolve`` (blocking ``..`` and symlink escapes) and credential paths face the same
``brokerize`` gate as file-reading tools, so the repository has one answer to which files may not
be read. Listings cap at MAX_ENTRIES, previews at MAX_PREVIEW_BYTES, binaries are judged from the
leading sample only, and directory sizes are not offered because they would take a full walk.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from ..agent.tools import workspace as paths
from ..security.action import brokerize
from .errors import FileMissing, FileNotDirectory, FileOutside, FileSensitive

MAX_ENTRIES = 400
MAX_PREVIEW_BYTES = 256 * 1024
#: Binaries are judged from this leading sample only, or reading the whole file would void the cap.
BINARY_SAMPLE_BYTES = 8192


def _relative(root: Path, target: Path) -> str:
    """POSIX path relative to the workspace root; the root itself is the empty string."""
    try:
        text = target.relative_to(root.resolve()).as_posix()
    except ValueError:  # unreachable: ownership was checked before entering
        return ""
    return "" if text == "." else text


def _resolve(root: Path, relative: str) -> Path:
    target, problem = paths.resolve(relative or ".", root=root)
    if target is None:
        raise FileOutside(problem or "只能在当前工作区内浏览")
    # Credential paths ask the security layer the same question the file-reading tools ask.
    action = brokerize("read_file", {"path": str(target)}, root=str(root))
    if action.credentials or "secret_access" in action.capabilities:
        reason = action.credentials[0] if action.credentials else "密钥文件"
        raise FileSensitive(f"这类文件不开放浏览（{reason}）")
    return target


def list_dir(root: Path, relative: str = "") -> dict[str, Any]:
    """List one directory level (directories first, name order), truncated at MAX_ENTRIES and
    reported as such; the returned dict already is the wire shape, as in the other services.
    """
    target = _resolve(root, relative)
    if not target.is_dir():
        raise FileNotDirectory(f"不是目录：{relative or '/'}")
    try:
        children = list(target.iterdir())
    except OSError as exc:
        raise FileMissing(f"读不了这个目录：{exc.strerror or exc}") from exc
    children.sort(key=lambda item: (not item.is_dir(), item.name.lower()))
    here = _relative(root, target)
    return {
        "path": here,
        # Parent as a relative path; None at the root.
        "parent": None if here == "" else _parent_of(here),
        "entries": [_entry(root, child) for child in children[:MAX_ENTRIES]],
        "truncated": len(children) > MAX_ENTRIES,
    }


def read_text(root: Path, relative: str) -> dict[str, Any]:
    """Preview one file: text returns content (possibly truncated), a binary only the fact."""
    target = _resolve(root, relative)
    if not target.is_file():
        raise FileMissing(f"没有这个文件：{relative or '/'}")
    try:
        size = target.stat().st_size
        with target.open("rb") as handle:
            head = handle.read(MAX_PREVIEW_BYTES + 1)
    except OSError as exc:
        raise FileMissing(f"读不了这个文件：{exc.strerror or exc}") from exc
    truncated = len(head) > MAX_PREVIEW_BYTES
    head = head[:MAX_PREVIEW_BYTES]
    if b"\x00" in head[:BINARY_SAMPLE_BYTES]:
        return {"path": relative, "size": size, "text": None, "binary": True, "truncated": truncated}
    return {
        "path": relative,
        "size": size,
        "text": head.decode("utf-8", errors="replace"),
        "binary": False,
        "truncated": truncated,
    }


def _entry(root: Path, child: Path) -> dict[str, Any]:
    is_dir = child.is_dir()
    try:
        size = None if is_dir else child.stat().st_size  # no size for dirs: it needs a full walk
    except OSError:
        size = None
    return {
        "name": child.name,
        "path": _relative(root, child),
        "kind": "dir" if is_dir else "file",
        "size": size,
    }


def _parent_of(relative: str) -> str:
    parent = PurePosixPath(relative).parent
    return "" if str(parent) == "." else parent.as_posix()


__all__ = [
    "BINARY_SAMPLE_BYTES",
    "MAX_ENTRIES",
    "MAX_PREVIEW_BYTES",
    "list_dir",
    "read_text",
]

"""工作区文件浏览：Web 端「工作区文件」面板的只读后端。

三条边界都在这一层（路由只翻协议）：
- **只在工作区内**：路径交给工具层同一个 `workspace.resolve` —— 解析后判归属，
  `..` 与 symlink 穿透都拦得住；
- **凭据类不开放**：判据复用安全层的 `brokerize`（`sensitive_reason` + `.env` 规则），
  与工具读文件走的是同一道闸——同一个仓库里「哪些文件不许读」只能有一个答案；
- **有上限**：目录最多列 `MAX_ENTRIES` 条、预览最多 `MAX_PREVIEW_BYTES` 字节。
  面板是给人看的，不是给整棵仓库做索引的；二进制只报「是二进制」，不猜编码。

目录大小不给：要递归才知道，为一个面板不值当。判二进制只看开头那一段。
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from ..agent.tools import workspace as paths
from ..security.action import brokerize
from .errors import FileMissing, FileNotDirectory, FileOutside, FileSensitive

MAX_ENTRIES = 400
MAX_PREVIEW_BYTES = 256 * 1024
#: 二进制判定只看开头这段（整份读进来再判，等于把上限当摆设）。
BINARY_SAMPLE_BYTES = 8192


def _relative(root: Path, target: Path) -> str:
    """相对工作区根的 POSIX 路径；根本身是空串。"""
    try:
        text = target.relative_to(root.resolve()).as_posix()
    except ValueError:  # 不该发生：进来之前已经判过归属
        return ""
    return "" if text == "." else text


def _resolve(root: Path, relative: str) -> Path:
    target, problem = paths.resolve(relative or ".", root=root)
    if target is None:
        raise FileOutside(problem or "只能在当前工作区内浏览")
    # 凭据类问安全层同一个问题：工具读文件走的就是这道闸。
    action = brokerize("read_file", {"path": str(target)}, root=str(root))
    if action.credentials or "secret_access" in action.capabilities:
        reason = action.credentials[0] if action.credentials else "密钥文件"
        raise FileSensitive(f"这类文件不开放浏览（{reason}）")
    return target


def list_dir(root: Path, relative: str = "") -> dict[str, Any]:
    """列一层目录：目录在前、名字序；超出上限截断并报出来。

    返回形状就是线格式（与其余服务一致：services 只吐 dict，路由不搬字段）。
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
        # 上一级相对路径；根目录为 None。
        "parent": None if here == "" else _parent_of(here),
        "entries": [_entry(root, child) for child in children[:MAX_ENTRIES]],
        "truncated": len(children) > MAX_ENTRIES,
    }


def read_text(root: Path, relative: str) -> dict[str, Any]:
    """读一个文件的预览：文本给内容（可能截断），二进制只报事实（`text` 为 None）。"""
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
        size = None if is_dir else child.stat().st_size  # 目录不给大小：要递归才知道
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

"""工作区边界：文件类工具共用的路径解析。

这不是沙箱。它只保证文件类工具不越过工作区；bash 以本进程权限运行，不受这里约束。
"""

from __future__ import annotations

from pathlib import Path

# 在 import 时固定；测试通过 monkeypatch 替换成临时目录。
WORKSPACE_ROOT = Path.cwd()


def resolve(raw: str, *, root: Path | None = None) -> tuple[Path | None, str | None]:
    """把路径解析成工作区内的绝对路径。

    返回 (path, error)：解析失败时 path 为 None，error 是可直接回传给模型的文本。
    """
    text = raw.strip()
    if not text:
        return None, "路径为空"

    base = (root or WORKSPACE_ROOT).resolve()
    path = Path(text)
    if not path.is_absolute():
        path = base / path
    path = path.resolve()

    if path != base and base not in path.parents:
        return None, f"拒绝访问工作区外的路径：{raw}"
    return path, None


def relative(path: Path, *, root: Path | None = None) -> str:
    """回传给模型时一律用工作区相对路径，输出更短也更稳定。"""
    base = (root or WORKSPACE_ROOT).resolve()
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)

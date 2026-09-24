"""fixture → 临时工作区。

每个 case 的 fixture 是**只读**的：真跑时复制进 `mkdtemp` 出来的目录，跑完删掉。
不这么做的话，第一次运行就会把 `.avid/`（会话）写进
`benchmarks/fixtures/`，第二次运行的初始状态就不再干净——评测集必须可重放。
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .case import FIXTURES_ROOT, Case

#: runtime 的簿记目录：写在**工作区里**，但不是 case 的受判内容。
#: 判定与 manifest 都排除它们，否则"只读"这条约束无法表达。
BOOKKEEPING = (".avid",)


@contextmanager
def materialized(case: Case) -> Iterator[Path]:
    """复制 fixture 到临时目录并交出根路径；退出时删除。"""
    source = FIXTURES_ROOT / case.fixture
    if not source.is_dir():
        raise FileNotFoundError(f"fixture 不存在：{source}")
    root = Path(tempfile.mkdtemp(prefix=f"avidbench-{case.id}-"))
    try:
        shutil.copytree(source, root, dirs_exist_ok=True)
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def manifest(root: Path) -> dict[str, int]:
    """受判文件的清单（相对路径 → 字节数），排除簿记目录。"""
    result: dict[str, int] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] in BOOKKEEPING:
            continue
        if path.is_file():
            result[relative.as_posix()] = path.stat().st_size
    return result

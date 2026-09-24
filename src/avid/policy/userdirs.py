"""用户级 Avid 目录：注册表、审计、沙箱用的空掩蔽文件都落在它下面。

单独成模块是因为它被三处共用（``workspaces.py`` 的注册表、``policy/audit.py`` 的审计、
``policy/sandbox.py`` 的掩蔽源文件），而它们**互相不能 import**：``workspaces`` →
``policy.permission`` → ``policy.audit``，任何一条反向 import 都会成环。这个模块是叶子
（只依赖标准库），谁都能安全地用。

``AVID_HOME`` 覆盖它（测试靠这条隔离：不让任何用例写进真实的 ``~/.avid``）。
"""

from __future__ import annotations

import os
from pathlib import Path

AVID_HOME_ENV = "AVID_HOME"


def avid_home() -> Path:
    """用户级 Avid 目录：``AVID_HOME`` 优先，否则 ``~/.avid``。"""
    override = os.environ.get(AVID_HOME_ENV)
    return Path(override).expanduser() if override else Path.home() / ".avid"


def home_dir() -> Path:
    """``avid_home`` 的别名（既有调用方用的名字）。"""
    return avid_home()


__all__ = ["AVID_HOME_ENV", "avid_home", "home_dir"]

"""测试夹具：把工作区根目录换到临时目录，并给模型调用一份可用的环境变量。"""

from __future__ import annotations

import pytest

from avid.tools import workspace


@pytest.fixture(autouse=True)
def model_env(monkeypatch):
    """每个测试都有"看起来可用"的模型配置：svc 会在运行线程里 load_config()。"""
    monkeypatch.setenv("AVID_API_KEY", "test-key")
    monkeypatch.setenv("AVID_MODEL", "test-model")
    monkeypatch.delenv("AVID_BASE_URL", raising=False)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """工作区根目录 → tmp_path：会话、任务、压缩落盘都跟着走。"""
    monkeypatch.setattr(workspace, "WORKSPACE_ROOT", tmp_path)
    return tmp_path

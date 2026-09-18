"""测试夹具：把工作区根目录换到临时目录，并给模型调用一份可用的环境变量。"""

from __future__ import annotations

import pytest

from avid.tools import workspace
from avid.workspaces import AVID_HOME_ENV


@pytest.fixture(autouse=True)
def model_env(monkeypatch):
    """每个测试都有"看起来可用"的模型配置：svc 会在运行线程里 load_config()。"""
    monkeypatch.setenv("AVID_API_KEY", "test-key")
    monkeypatch.setenv("AVID_MODEL", "test-model")
    monkeypatch.delenv("AVID_BASE_URL", raising=False)


@pytest.fixture(autouse=True)
def avid_home(tmp_path, monkeypatch):
    """用户级目录 → tmp_path：工作区注册表绝不写进真实的 ~/.avid。"""
    monkeypatch.setenv(AVID_HOME_ENV, str(tmp_path / "avid-home"))


@pytest.fixture
def hook_registry(monkeypatch):
    """本用例专用的 hook 注册表（P2-19）。

    `agent_loop` 默认取 `hooks.DEFAULT_HOOKS`；这里换上一份空的并把它返回，于是
    "注册回调 → 跑循环 → 断言"全发生在这份局部对象上：不再改模块级字典，也不会
    漏到别的用例（以前靠 monkeypatch `HOOKS` 来隔离）。
    """
    from avid.runtime import hooks as hooks_module
    from avid.runtime.hooks import HookRegistry

    registry = HookRegistry()
    monkeypatch.setattr(hooks_module, "DEFAULT_HOOKS", registry)
    return registry


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    """工作区根目录 → tmp_path：会话、任务、压缩落盘都跟着走。

    **autouse**：隔离必须是失败关闭的。以前它是可选夹具，于是漏掉它的用例会
    直接写进真实工作目录——实测把 `.avid/context/transcript-0001.json` 覆盖成了
    测试数据，而落盘目录又是按进程内序号命名的，正好和真实使用的文件同名。
    """
    monkeypatch.setattr(workspace, "WORKSPACE_ROOT", tmp_path)
    return tmp_path

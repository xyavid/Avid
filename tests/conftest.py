"""测试夹具：把工作区根目录换到临时目录，并给每次运行一份可用的 BYOK 模型配置。"""

from __future__ import annotations

import json

import pytest

from avid.agent.tools import workspace
from avid.services.workspace_registry import AVID_HOME_ENV


@pytest.fixture(autouse=True)
def model_env(request, tmp_path, monkeypatch):
    """每个测试都有一份"看起来可用"的 BYOK 配置：svc 会在运行线程里 resolve_chat()。

    **例外**：`eval` / `eval_smoke` 标记的真模型评测要用真实环境（`~/.avid` 下的
    BYOK 配置）。被这份夹具顶成 `test-key` 的话运行会全部 401，而 `llm_error` 不在
    「仪器错误」的断言里——测出来就是「全红但绿」（实测踩到过一次，见
    `support.INFRA_STATUSES`）。
    """
    if request.node.get_closest_marker("eval") or request.node.get_closest_marker("eval_smoke"):
        return
    byok_dir = tmp_path / "byok"
    byok_dir.mkdir()
    # 与旧 env 基线同名同值：既有用例对 test-key / test-model 的断言继续成立
    # （support.real_config_or_skip 也靠这两个名字识别「假配置」并拒绝真模型评测）。
    (byok_dir / "models.json").write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "id": "test",
                        "label": "Test",
                        "protocol": "openai-compatible",
                        "base_url": "https://test.example/v1",
                        "models": [{"id": "test-model"}],
                    }
                ],
                "bindings": {"chat": "test/test-model"},
            }
        ),
        encoding="utf-8",
    )
    (byok_dir / "secrets.json").write_text(json.dumps({"test": "test-key"}), encoding="utf-8")
    monkeypatch.setenv("AVID_BYOK_CONFIG", str(byok_dir / "models.json"))
    monkeypatch.setenv("AVID_BYOK_SECRETS", str(byok_dir / "secrets.json"))
    # 拔掉旧的模型身份环境变量：它们已不被读取，留着只会骗人；谁再读谁翻车。
    for name in ("AVID_API_KEY", "AVID_MODEL", "AVID_BASE_URL", "AVID_PROVIDER", "AVID_CONTEXT_WINDOW"):
        monkeypatch.delenv(name, raising=False)
    # 关掉"问 provider 要窗口"的探测：单测不打真实端点（要验它自己注入 MockTransport
    # 并把这一项打开，见 tests/test_usage.py）。
    monkeypatch.setenv("AVID_MODEL_INFO", "off")


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
    from avid.agent import hooks as hooks_module
    from avid.agent.hooks import HookRegistry

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

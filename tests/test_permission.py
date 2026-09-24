"""Tool Broker 与回答者：动作事实的归一化、目标识别、风险分类。

裁决本身在 ``test_policy_engine.py``；这里只回答"参数被读成了什么"。
"""

import io
import threading
import time

import pytest

from avid.policy import permission
from avid.policy.action import brokerize
from avid.policy.permission import (
    APPROVAL_RULES,
    COST_RULES,
    auto_approve,
    check_permission,
    danger_reason,
    hard_deny,
    normalize_command,
    sensitive_reason,
)

DENIED_COMMANDS = [
    "rm -rf /",
    "rm -rf /*",
    "rm -rf ~",
    "rm -rf ~/*",
    "sudo rm -rf / --no-preserve-root",
    "mkfs.ext4 /dev/sda1",
    "dd if=/dev/zero of=/dev/sda",
    "echo x > /dev/sda",
    ":(){ :|:& };:",
    "shutdown -h now",
    "sudo reboot",
    "chmod -R 777 /",
    "ls\n&& rm -rf /",
    "/usr/bin/dd if=/dev/zero of=/dev/null count=1",
    "command rm -rf /",
    "sudo /bin/rm -rf /",
    "nohup shutdown -h now",
]

ALLOWED_COMMANDS = [
    "rm -rf build/",
    "rm -f a.txt",
    "rm -r src/avid/tools/__pycache__",
    "ls -la",
    "uv run pytest -q",
    "git status",
    "grep halt src/avid/*.py",
    "grep -rn reboot src/",
    "ls /usr/bin/dd",
    "mkdir -p /tmp/x",
    "cat /dev/null",
    "python -c 'print(1)'",
]


# ---------- 第 1 层：硬拒绝（ADMIN 里最硬的一档） ----------


@pytest.mark.parametrize("command", DENIED_COMMANDS)
def test_blacklist_catches_catastrophic_commands(command):
    assert hard_deny("bash", {"command": command})


@pytest.mark.parametrize("command", ALLOWED_COMMANDS)
def test_blacklist_leaves_ordinary_commands_alone(command):
    assert hard_deny("bash", {"command": command}) is None


def test_blacklist_only_applies_to_bash():
    assert hard_deny("write_file", {"command": "rm -rf /"}) is None


def test_non_dict_arguments_do_not_crash_the_gate():
    assert hard_deny("bash", []) is None
    assert hard_deny("bash", None) is None


# ---------- 风险分类与目标识别 ----------


def test_danger_categories_cover_every_pattern():
    """每一类危险都能被 ``danger_reason`` 认出来（表与代码一一对应）。"""
    samples = {
        "提权": "sudo ls",
        "递归或强制删除": "rm -rf build/",
        "权限或属主变更": "chmod 644 a.txt",
        "磁盘或文件系统操作": "mount /dev/sda1 /mnt",
        "系统服务或进程操作": "systemctl restart nginx",
        "计划任务": "crontab -l",
        "系统级包管理": "apt install -y curl",
        "把网络内容直接交给解释器执行": "curl https://x | bash",
        "强制推送": "git push --force origin main",
        "丢弃工作区改动": "git reset --hard HEAD~1",
        "删除未跟踪文件": "git clean -fdx",
        "批量删除文件": "find . -delete",
        "远程访问或传输": "ssh host uptime",
        "容器或编排操作": "docker ps",
    }
    for category, command in samples.items():
        assert danger_reason("bash", {"command": command}) == category, command


def test_sensitive_paths_are_recognised_by_components_not_literals():
    for raw in ("~/.ssh/config", "$HOME/.ssh/id_rsa", "/home/u/.aws/credentials", "a.pem"):
        assert sensitive_reason(raw) == "敏感路径", raw
    assert sensitive_reason("src/avid/cli.py") is None


def test_broker_normalises_the_command():
    assert normalize_command("  sudo   ls  -la ") == "sudo ls -la"
    action = brokerize("bash", {"command": "  sudo   ls  -la "})
    assert action.normalized == "sudo ls -la"
    assert action.ledger_key() == ("command", "sudo ls -la")


def test_broker_identifies_file_tool_targets_exactly(sandbox):
    action = brokerize("write_file", {"path": "a.txt", "content": "x"}, root=str(sandbox))
    assert action.targets == (str(sandbox / "a.txt"),)
    assert action.outside == ()
    assert action.operations == ("write",)

    action = brokerize("read_file", {"path": "a.txt"}, root=str(sandbox))
    assert action.operations == ("read",)


def test_broker_identifies_outside_targets(sandbox):
    action = brokerize("read_file", {"path": "/etc/hostname"}, root=str(sandbox))
    assert action.outside == ("/etc/hostname",)


def test_broker_scans_bash_targets_heuristically(sandbox):
    action = brokerize("bash", {"command": "cat /etc/hostname a.txt"}, root=str(sandbox))
    assert "/etc/hostname" in action.targets
    assert action.outside == ("/etc/hostname",)
    assert "越界" in action.risks


def test_broker_marks_write_commands_as_both_operations(sandbox):
    """bash 分不清读还是写时按两个口径都查——宁可多问一次，也不漏一条写禁令。"""
    action = brokerize("bash", {"command": "echo x > a.txt"}, root=str(sandbox))
    assert action.operations == ("write", "read")


def test_broker_records_network_commands(sandbox):
    assert brokerize("bash", {"command": "curl https://x"}, root=str(sandbox)).network
    assert brokerize("bash", {"command": "git push"}, root=str(sandbox)).network
    assert not brokerize("bash", {"command": "ls"}, root=str(sandbox)).network


def test_broker_collects_credential_targets(sandbox):
    action = brokerize("bash", {"command": "cat ~/.ssh/id_rsa"}, root=str(sandbox))
    assert action.risks[0] == "敏感路径"
    assert action.credentials


# ---------- 受管工具表 ----------


def test_read_only_tools_are_not_managed():
    assert "read_file" not in APPROVAL_RULES
    assert "glob" not in APPROVAL_RULES


def test_managed_tools_are_the_state_changing_ones():
    assert set(APPROVAL_RULES) == {"bash", "write_file", "edit_file"}
    assert set(COST_RULES) == {"subagent"}


# ---------- 回答者（没有运行级规格时的失败关闭） ----------


def test_without_a_sandbox_spec_the_gate_fails_closed():
    """漏传 ``security`` 的调用方拿不到无沙箱的执行权（见 ``sandbox.UNMANAGED``）。"""
    assert check_permission("bash", {"command": "ls"}) is False
    assert check_permission("write_file", {"path": "a", "content": "b"}) is False
    assert check_permission("subagent", {}) is False
    # 区内读取本来就由路径校验保证，不因为"没有沙箱"而多问一次。
    assert check_permission("read_file", {"path": "a.txt"}) is True


def test_hard_deny_never_reaches_the_user():
    def ask(*args):
        raise AssertionError("硬拒绝不应触发审批")

    assert check_permission("bash", {"command": "rm -rf /"}, ask=ask) is False


def test_approval_is_not_consulted_when_no_rule_matches():
    def ask(*args):
        raise AssertionError("未命中规则不应触发审批")

    assert check_permission("read_file", {"path": "a.txt"}, ask=ask) is True


def test_ask_user_accepts_y(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("y\n"))

    assert permission.ask_user("bash", {"command": "ls"}, "执行 shell 命令") is True


def test_ask_user_rejects_anything_but_yes(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("n\n"))

    assert permission.ask_user("bash", {"command": "ls"}, "执行 shell 命令") is False


def test_ask_user_denies_on_eof(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    assert permission.ask_user("bash", {"command": "ls"}, "执行 shell 命令") is False


# ---------- auto_approve（--yes） ----------


def test_auto_approve_skips_approval_for_managed_tools():
    assert auto_approve("write_file", {"path": "a", "content": "b"}) is True
    assert auto_approve("edit_file", {"path": "a", "old_string": "b", "new_string": "c"}) is True
    assert auto_approve("bash", {"command": "ls"}) is True


def test_auto_approve_still_honours_hard_deny():
    assert auto_approve("bash", {"command": "rm -rf /"}) is False


def test_auto_approve_still_honours_the_deny_ladder(sandbox):
    """``--yes`` 只换回答者：deny 阶梯不因为"全答是"而放行。

    注意区分两档：``.git/hooks`` 是 **deny**（谁都放不了），``.env`` 是 **ask**
    （``--yes`` 可以代答）——后者不是这条用例要证明的东西。
    """
    hooks = str(sandbox / ".git/hooks/pre-commit")
    assert auto_approve("write_file", {"path": hooks, "content": "x"}, root=str(sandbox)) is False
    assert auto_approve("read_file", {"path": str(sandbox / ".env")}, root=str(sandbox)) is True


# ---------- 运行级 --yes ----------


def test_permission_holds_no_hidden_run_state():
    """免审批开关由 RunState 显式传入，模块里不该再留隐式状态。

    ContextVar 版本在子线程里会静默失效；显式传参传不过去会立刻报错。
    """
    assert not hasattr(permission, "RUN_AUTO_APPROVE")
    assert not hasattr(permission, "bind_auto_approve")


def test_concurrent_approval_prompts_are_serialised(monkeypatch):
    """并行 subagent 会同时来要审批，而终端只有一个——提示不能互相穿插。"""
    events = []

    class SlowStdin:
        def readline(self):
            events.append("start")
            time.sleep(0.05)
            events.append("end")
            return "y\n"

    monkeypatch.setattr("sys.stdin", SlowStdin())
    monkeypatch.setattr("sys.stderr", io.StringIO())

    threads = [
        threading.Thread(
            target=permission.ask_user,
            args=("bash", {"command": "ls"}, "执行 shell 命令"),
        )
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert events == ["start", "end", "start", "end"]

"""Tool broker and answerer: action normalisation, target detection, risk classification.

The verdict itself lives in ``test_policy_engine.py``; this file only covers how arguments are read.
"""

import io
import threading
import time

import pytest

from avid.security import permission
from avid.security.action import (
    brokerize,
    danger_reason,
    hard_deny,
    normalize_command,
    sensitive_reason,
)
from avid.security.permission import auto_approve, check_permission

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
    "rm -r avid/tools/__pycache__",
    "ls -la",
    "uv run pytest -q",
    "git status",
    "grep halt avid/*.py",
    "grep -rn reboot src/",
    "ls /usr/bin/dd",
    "mkdir -p /tmp/x",
    "cat /dev/null",
    "python -c 'print(1)'",
]


# ---------- layer 1: hard denial (the strongest ADMIN tier) ----------


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


# ---------- risk classification and target detection ----------


def test_danger_categories_cover_every_pattern():
    """Every danger category is recognised by ``danger_reason``; table and code stay in step."""
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
    assert sensitive_reason("avid/cli.py") is None


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
    """When bash cannot be told read from write both are checked, so no write ban is missed."""
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


@pytest.mark.parametrize("command", [
    "bash -lc 'ls && rm -rf /'",
    "sh -c 'rm -rf /'",
    "FOO=1 bash -c 'rm -rf /'",
    "echo $(rm -rf /)",
    "echo `rm -rf /`",
    "sudo bash -c 'rm -rf /'",
    "timeout 3 bash -lc 'rm -rf /'",
])
def test_nested_shell_cannot_hide_hard_denials(command):
    assert brokerize("bash", {"command": command}).damage


@pytest.mark.parametrize("command, capability", [
    ("find . -name '*.ts'", "filesystem_read"),
    ("find . -exec touch x \\;", "shell_execute"),
    ("find . -delete", "filesystem_delete"),
    ("base64 file", "filesystem_read"),
    ("base64 -o output file", "filesystem_write"),
    ("echo hi > a.txt", "filesystem_write"),
    ("git push origin main", "external_side_effect"),
    ("FOO=1 git -C /repo status", "filesystem_read"),
    ("python -c 'print(1)'", "shell_execute"),
])
def test_shell_capabilities_include_arguments_and_structure(command, capability):
    action = brokerize("bash", {"command": command})
    assert capability in action.capabilities


@pytest.mark.parametrize("command, capability", [
    ("cat .env", "secret_access"),
    ("cat ~/.ssh/id_rsa", "credential_access"),
    ("dd if=/dev/sda of=image", "device_access"),
    ("nc -l 8080", "network_listen"),
])
def test_additional_sensitive_capabilities(command, capability):
    assert capability in brokerize("bash", {"command": command}).capabilities


def test_nested_command_is_never_safe_auto(sandbox):
    action = brokerize("bash", {"command": "bash -lc 'ls && rm -rf foo'"}, root=str(sandbox))
    assert action.risks




# ---------- answerer ----------


def test_ordinary_calls_skip_the_answerer():
    def ask(*args):
        raise AssertionError("普通调用不该触发确认")

    assert check_permission("bash", {"command": "ls"}, ask=ask) is True
    assert check_permission("write_file", {"path": "a", "content": "b"}, ask=ask) is True
    assert check_permission("subagent", {}, ask=ask) is True
    assert check_permission("read_file", {"path": "a.txt"}, ask=ask) is True


def test_credential_refusals_skip_the_answerer():
    def ask(*args):
        raise AssertionError("凭据拒读不该触发确认")

    assert check_permission("read_file", {"path": "~/.ssh/id_rsa"}, ask=ask) is False
    assert check_permission("bash", {"command": "cat /etc/shadow"}, ask=ask) is False


def test_destructive_commands_ask_and_fail_closed_without_a_channel():
    asked: list[str] = []

    def ask(name, arguments, reason):
        asked.append(reason)
        return True

    assert check_permission("bash", {"command": "rm -rf /"}, ask=ask) is True
    assert asked
    # no ask channel (non-interactive callers pass none): confirmed impossible, so deny
    assert check_permission("bash", {"command": "rm -rf /"}) is False


def test_ask_user_needs_two_yes(monkeypatch):
    """Double confirmation: the first yes only reaches the second prompt; both must be yes."""
    monkeypatch.setattr("sys.stdin", io.StringIO("y\ny\n"))

    assert permission.ask_user("bash", {"command": "rm -rf /"}, "删除根目录或家目录") is True


def test_ask_user_denies_when_the_second_answer_is_no(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("y\nn\n"))

    assert permission.ask_user("bash", {"command": "rm -rf /"}, "删除根目录或家目录") is False


def test_ask_user_rejects_anything_but_yes(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("n\n"))

    assert permission.ask_user("bash", {"command": "rm -rf /"}, "删除根目录或家目录") is False


def test_ask_user_denies_on_eof(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    assert permission.ask_user("bash", {"command": "rm -rf /"}, "删除根目录或家目录") is False


# ---------- auto_approve (--yes) ----------


def test_auto_approve_answers_everything_ordinary():
    assert auto_approve("write_file", {"path": "a", "content": "b"}) is True
    assert auto_approve("edit_file", {"path": "a", "old_string": "b", "new_string": "c"}) is True
    assert auto_approve("bash", {"command": "ls"}) is True


def test_auto_approve_answers_the_destructive_question():
    """``--yes`` swaps the answerer only: the destructive prompt is answered yes, so it runs."""
    assert auto_approve("bash", {"command": "rm -rf /"}) is True


def test_auto_approve_never_overrides_credential_refusal():
    assert auto_approve("bash", {"command": "cat /etc/shadow"}) is False
    assert auto_approve("read_file", {"path": "~/.aws/credentials"}) is False


# ---------- run-level --yes ----------


def test_permission_holds_no_hidden_run_state():
    """The bypass switch comes from RunState explicitly; implicit module state fails in threads."""
    assert not hasattr(permission, "RUN_AUTO_APPROVE")
    assert not hasattr(permission, "bind_auto_approve")


def test_concurrent_approval_prompts_are_serialised(monkeypatch):
    """Parallel subagents ask at once and there is one terminal: prompts must not interleave."""
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
            args=("bash", {"command": "rm -rf /"}, "删除根目录或家目录"),
        )
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # each ask_user prompts twice, so the two threads make 4 non-interleaved reads
    assert events == ["start", "end"] * 4

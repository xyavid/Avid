import io

import pytest

from avid import permission
from avid.permission import (
    APPROVAL_RULES,
    auto_approve,
    check_permission,
    hard_deny,
    match_rule,
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


# ---------- 闸门 2：规则匹配 ----------


def test_read_only_tools_need_no_approval():
    assert match_rule("read_file", {}) is None
    assert match_rule("glob", {}) is None


def test_mutating_tools_match_a_rule():
    assert set(APPROVAL_RULES) == {"bash", "write_file", "edit_file"}


# ---------- 闸门 3：用户审批 ----------


def test_allowed_when_user_approves():
    asked = []

    def ask(name, arguments, reason):
        asked.append((name, reason))
        return True

    assert check_permission("bash", {"command": "ls"}, ask=ask) is True
    assert asked == [("bash", APPROVAL_RULES["bash"])]


def test_denied_when_user_refuses():
    assert check_permission("bash", {"command": "ls"}, ask=lambda *a: False) is False


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


# ---------- auto_approve ----------


def test_auto_approve_skips_approval_for_mutating_tools():
    assert auto_approve("write_file", {"path": "a", "content": "b"}) is True
    assert auto_approve("edit_file", {"path": "a", "old_string": "b", "new_string": "c"}) is True
    assert auto_approve("bash", {"command": "ls"}) is True


def test_auto_approve_still_honours_hard_deny():
    assert auto_approve("bash", {"command": "rm -rf /"}) is False

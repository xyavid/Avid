"""bash 目标扫描（tools/workspace.py）：引号感知 + 排除「不像路径」的记号。

这份扫描器只决定「要不要问 / 要不要拒」（物理边界是 policy.sandbox），但它的假阳性
会直接变成一次拒绝：工作区路径带空格时，旧实现按空白切开 cd "/a b c"，把 /a 判成
区外目标——现场会话 01a0d277 连续 15 条 deny 就是它。

钉住的行为：

1. 引号里的整段是一个记号（带空格的路径不再被切断）；
2. 引号内嵌的解释器文本仍要能扫出里面的路径（bash -c "rm -rf /etc"）；
3. 明显不是路径的记号（URL、Python 属性、命令选项）不算目标；
   但 .env / .git/hooks/... 这类点开头的真文件必须仍然是目标。
"""

from __future__ import annotations

import pytest

from avid.tools import workspace


@pytest.fixture
def spaced_root(tmp_path, monkeypatch):
    """根目录自身带空格——Linux 上完全合法，也是这条 bug 的触发条件。"""
    root = tmp_path / "Avid workspace"
    root.mkdir()
    monkeypatch.setattr(workspace, "WORKSPACE_ROOT", root)
    return root


def targets(command, root):
    return [str(path) for path in workspace.command_targets(command, root=root)]


def test_double_quoted_path_with_spaces_stays_one_target(spaced_root):
    command = f'cd "{spaced_root}" && ls -la'

    assert targets(command, spaced_root) == [str(spaced_root)]
    # 旧行为：按空白切开，把父目录 /…/Avid 当成区外目标
    assert str(spaced_root.parent / "Avid") not in targets(command, spaced_root)
    assert workspace.outside_command_target(command, root=spaced_root) is None


def test_single_quoted_path_with_spaces_stays_one_target(spaced_root):
    command = f"cd '{spaced_root}' && python3 _probe.py"

    assert workspace.outside_command_target(command, root=spaced_root) is None
    assert targets(command, spaced_root) == [str(spaced_root)]


def test_redirect_into_a_spaced_workspace_is_not_outside(spaced_root):
    command = f'echo hi > "{spaced_root}/out.txt"'

    assert targets(command, spaced_root) == [str(spaced_root / "out.txt")]
    assert workspace.outside_command_target(command, root=spaced_root) is None


def test_paths_inside_interpreter_text_are_still_found(spaced_root):
    """整段优先、不像路径再拆开：引号里的解释器程序文本仍要扫出绝对路径。"""
    assert (
        workspace.outside_command_target(
            'bash -c "rm -rf /etc/passwd"', root=spaced_root
        )
        == "/etc/passwd"
    )


def test_url_is_not_a_path(spaced_root):
    assert targets("curl -sI https://registry.npmjs.org", spaced_root) == []


def test_python_attribute_is_not_a_path(spaced_root):
    """type(e).__name__ 里的 .__name__ 是属性，不是工作区下的一个文件。"""
    assert targets('python3 -c "print(type(e).__name__)"', spaced_root) == []


def test_attribute_access_inside_program_text_is_not_a_path(spaced_root):
    """os.environ.get 里的 .environ.get 是属性，不是工作区下一个隐藏文件。

    真数据（2026-09-24 审计）里 15 条被拒命令中有 4 条带这种产物：目标列表被
    属性名污染，越界判定与审计的可读性一起变差。
    """
    assert targets('python3 -c "import os; print(os .environ.get(chr(80)))"', spaced_root) == []


def test_option_value_is_the_target_not_the_whole_option(spaced_root):
    assert targets("pip install --target=./_libs pygame", spaced_root) == [
        str(spaced_root / "_libs")
    ]


def test_dotfile_is_still_a_path(spaced_root):
    assert targets("cat .env", spaced_root) == [str(spaced_root / ".env")]


def test_hidden_directory_is_still_a_path(spaced_root):
    assert targets("echo x > .git/hooks/pre-commit", spaced_root) == [
        str(spaced_root / ".git/hooks/pre-commit")
    ]

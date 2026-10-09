"""bash target scanning (tools/workspace.py): quote-aware, and plainly non-path tokens stay out.

This scanner decides only whether to ask or deny (the physical boundary is policy.sandbox), but a
false positive turns into a denial, so its failure list is:

1. a quoted whole stays one token, so a spaced path is not split apart;
2. interpreter text inside quotes must still yield the absolute paths it contains, as in
   ``bash -c "rm -rf /etc"``;
3. plainly non-path tokens (URLs, Python attributes, command options) are not targets, while real
   dotfiles such as ``.env`` or ``.git/hooks/...`` still are.
"""

from __future__ import annotations

import pytest

from avid.agent.tools import workspace


@pytest.fixture
def spaced_root(tmp_path, monkeypatch):
    root = tmp_path / "Avid workspace"
    root.mkdir()
    monkeypatch.setattr(workspace, "WORKSPACE_ROOT", root)
    return root


def targets(command, root):
    return [str(path) for path in workspace.command_targets(command, root=root)]


def test_double_quoted_path_with_spaces_stays_one_target(spaced_root):
    command = f'cd "{spaced_root}" && ls -la'

    assert targets(command, spaced_root) == [str(spaced_root)]
    # the parent directory must not leak in as an outside target
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
    assert (
        workspace.outside_command_target(
            'bash -c "rm -rf /etc/passwd"', root=spaced_root
        )
        == "/etc/passwd"
    )


def test_url_is_not_a_path(spaced_root):
    assert targets("curl -sI https://registry.npmjs.org", spaced_root) == []


def test_python_attribute_is_not_a_path(spaced_root):
    assert targets('python3 -c "print(type(e).__name__)"', spaced_root) == []


def test_attribute_access_inside_program_text_is_not_a_path(spaced_root):
    """Attribute tokens must not pollute the target list: they degrade outside detection."""
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

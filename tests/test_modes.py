"""三个用户模式 = 三轴预设：取值、正交性、默认值、full 三重锁、旧名迁移。"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from avid.policy import permission
from avid.policy.permission import (
    ADMIN_WRITABLE_SOURCES,
    APPROVAL_CLASSIFIER,
    APPROVAL_NONE,
    APPROVAL_USER,
    DEFAULT_MODE,
    FULL_MODE,
    LEGACY_MODES,
    MODE_AUTO,
    MODE_FULL,
    MODE_MANUAL,
    MODES,
    NETWORK_OPEN,
    NETWORK_RESTRICTED,
    SANDBOX_DISABLED,
    SANDBOX_WORKSPACE,
    FullAccessError,
    PermissionModeError,
    build_run_security,
    full_grant_error,
    migrate_mode,
    mode_spec,
    resolve_axes,
    validate_mode,
)

ROOT = Path(__file__).resolve().parents[1]

#: 用户规格里逐字固定的三个组合（这张表就是"三模式"的定义）。
EXPECTED = {
    MODE_MANUAL: (APPROVAL_USER, SANDBOX_WORKSPACE, NETWORK_RESTRICTED),
    MODE_AUTO: (APPROVAL_CLASSIFIER, SANDBOX_WORKSPACE, NETWORK_RESTRICTED),
    MODE_FULL: (APPROVAL_NONE, SANDBOX_DISABLED, NETWORK_OPEN),
}


@pytest.mark.parametrize("mode", MODES)
def test_each_preset_is_exactly_the_agreed_triple(mode):
    assert mode_spec(mode).axes() == EXPECTED[mode]
    assert resolve_axes(mode) == EXPECTED[mode]


def test_mode_names_are_only_these_three():
    assert MODES == (MODE_MANUAL, MODE_AUTO, MODE_FULL)
    assert validate_mode("manual") == "manual"
    with pytest.raises(PermissionModeError):
        validate_mode("strict")


def test_manual_is_the_default_and_full_is_not():
    """full ≠ default：最严一档是缺省，full 永远要显式点名。"""
    assert DEFAULT_MODE == MODE_MANUAL
    assert DEFAULT_MODE != MODE_FULL
    assert mode_spec(DEFAULT_MODE).approval == APPROVAL_USER


def test_axes_are_orthogonal():
    """原则①：没有任何一处从 approval 推导 sandbox。

    两条断言合起来才说明"正交"：固定 sandbox/network 时 approval 可以不同（manual vs
    auto），固定 approval 时 sandbox 才变（那只能靠 full）。也就是说 manual 与 auto
    的沙箱**逐字相同**——"auto = 关沙箱"在这张表上不可能出现。
    """
    manual, auto, full = (
        EXPECTED[MODE_MANUAL],
        EXPECTED[MODE_AUTO],
        EXPECTED[MODE_FULL],
    )
    assert manual[0] != auto[0], "manual 与 auto 的差别必须在 approval 上"
    assert manual[1] == auto[1] == SANDBOX_WORKSPACE
    assert manual[2] == auto[2] == NETWORK_RESTRICTED
    # 沙箱不是"approval 的函数"：同一个 approval 值不能推出两个不同的 sandbox 取值
    # 之外的东西；这里显式列出三轴的取值域，避免有人往 approval 里塞别名。
    assert {mode_spec(m).approval for m in MODES} == {
        APPROVAL_USER,
        APPROVAL_CLASSIFIER,
        APPROVAL_NONE,
    }
    assert {mode_spec(m).sandbox for m in MODES} == {SANDBOX_WORKSPACE, SANDBOX_DISABLED}
    assert full[0] == APPROVAL_NONE and full[1] == SANDBOX_DISABLED


def test_no_code_path_derives_sandbox_from_approval():
    """会失败的静态断言：源码里不许出现"approval 是 X 所以关沙箱"的写法。

    用法：任何 `sandbox=` 的赋值右值都不许直接引用 approval 变量。
    """
    offenders: list[str] = []
    for path in sorted((ROOT / "src" / "avid").rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "sandbox=" not in line or line.lstrip().startswith("#"):
                continue
            right = line.split("sandbox=", 1)[1]
            if re.search(r"approval", right):
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert offenders == [], f"sandbox 由 approval 推导出来了：{offenders}"


# ---------------------------------------------------------------- full 三重锁


def test_full_needs_an_explicit_switch_on_the_cli():
    assert full_grant_error(MODE_FULL, acknowledged=False, source="cli") is not None
    assert full_grant_error(MODE_FULL, acknowledged=True, source="cli") is None


def test_full_needs_an_explicit_ack_from_the_web():
    assert full_grant_error(MODE_FULL, acknowledged=False, source="web") is not None
    assert full_grant_error(MODE_FULL, acknowledged=True, source="web") is None


def test_full_can_never_be_a_workspace_default():
    """产品规则：full 必须是**某一次运行的**显式授权，不能变成持久默认值。"""
    problem = full_grant_error(MODE_FULL, acknowledged=True, source="workspace_default")
    assert problem is not None and "不能作为工作区默认权限" in problem


def test_non_full_modes_need_no_grant():
    for mode in (MODE_MANUAL, MODE_AUTO):
        assert full_grant_error(mode, acknowledged=False, source="workspace_default") is None
    assert ADMIN_WRITABLE_SOURCES == ("cli", "web", "api")


def test_build_run_security_refuses_full_without_ack(sandbox):
    with pytest.raises(FullAccessError):
        build_run_security(mode=MODE_FULL, root=str(sandbox), audit_enabled=False)
    spec = build_run_security(
        mode=MODE_FULL, root=str(sandbox), full_ack=True, audit_enabled=False
    )
    assert spec.full_granted is True
    assert spec.sandbox_policy == SANDBOX_DISABLED


def test_build_run_security_marks_axes_in_its_summary(sandbox):
    spec = build_run_security(mode=MODE_AUTO, root=str(sandbox), audit_enabled=False)
    summary = spec.summary()
    assert (summary["mode"], summary["approval"]) == (MODE_AUTO, APPROVAL_CLASSIFIER)
    assert summary["sandbox"] == SANDBOX_WORKSPACE and summary["network"] == NETWORK_RESTRICTED
    assert summary["rules"] > 0


# ---------------------------------------------------------------- 旧名迁移


@pytest.mark.parametrize(
    ("legacy", "expected"),
    [("strict", MODE_MANUAL), ("workspace", MODE_MANUAL), ("system", MODE_AUTO)],
)
def test_legacy_names_migrate_toward_less_autonomy(legacy, expected):
    """迁移只许"收紧或持平"：旧 workspace 问人 → 新 manual 仍然问人（不是 auto）。"""
    mode, note = migrate_mode(legacy)
    assert mode == expected
    assert note is not None and legacy in note


def test_migration_never_lands_on_full():
    assert FULL_MODE not in set(LEGACY_MODES.values())


def test_migration_reports_unknown_names():
    with pytest.raises(PermissionModeError):
        migrate_mode("yolo")


def test_legacy_names_are_rejected_by_validate_mode():
    """读路径走 migrate，写路径不接受旧名——否则"新写的配置可能是旧语义"。"""
    for legacy in LEGACY_MODES:
        with pytest.raises(PermissionModeError):
            validate_mode(legacy)


# ---------------------------------------------------------------- 词汇一致性


def test_mode_vocabulary_is_the_same_in_four_places():
    """模式名是**契约**：内核、CLI、REST DTO、前端联合类型必须逐字一致。

    前端漏改会变成"界面选了 full、服务端按 422 拒"，所以 TS 联合类型也拉进来对账；
    web/src/api/types.ts 是前端侧模式名的单点。
    """
    import re
    from typing import get_args

    from avid import cli
    from avid.web.schemas import CreateWorkspaceIn, StartRunIn

    def literal_values(annotation) -> set[str]:
        """``Literal["a","b"] | None`` → {"a","b"}。"""
        found: set[str] = set()
        for arg in get_args(annotation):
            values = get_args(arg)
            found.update(item for item in (values or (arg,)) if isinstance(item, str))
        return found

    cli_choices = set(
        next(a for a in cli.build_parser()._actions if a.dest == "permission").choices
    )
    ws_choices = set(cli._default_mode_choices())
    run_choices = literal_values(StartRunIn.model_fields["permission"].annotation)
    ws_field = literal_values(CreateWorkspaceIn.model_fields["permission"].annotation)

    assert cli_choices == set(MODES) == run_choices
    # 默认值那一档不接受 full（见 full 三重锁）
    assert ws_choices == {MODE_MANUAL, MODE_AUTO} == ws_field

    types = (ROOT / "web" / "src" / "api" / "types.ts").read_text(encoding="utf-8")
    match = re.search(r"export type PermissionMode =([^\n]+)", types)
    assert match, "types.ts 里找不到 PermissionMode"
    assert set(re.findall(r"'([a-z]+)'", match.group(1))) == set(MODES)


def test_labels_and_help_text_exist_for_every_mode():
    for mode in MODES:
        assert permission.MODE_LABELS[mode]
        assert mode_spec(mode).summary

"""文件类工具的实现：读、写、改、按模式查找。

失败一律返回以"错误："开头的文本，而不是抛异常——工具回传文本，
循环才能继续，模型才有机会自纠。

**边界是沙箱能力，不是工作区**：``bash`` 跑在沙箱里，沙箱以 ``--ro-bind / /`` 让读
整个文件系统成为已有能力、只把工作区（与授予的路径）留成可写；文件工具不在沙箱里跑，
所以这里按同一份口径执行——**读**区外不需要授权，**写**区外仍然需要账本里的 rw 授权。

``state`` 是**可选**的运行级上下文：有它就用运行级工作区根、读区外放行、写区外查账本；
没有它（直接调用工具、单元测试）回落到进程默认根且区外一律回绝——没有 run 级安全规格
就连 deny/ask 阶梯都查不了，失败方向只能是关闭。授权由 ``policy.permission.gate`` 决定
并写进账本，这里只读结果——工具不做权限决定。

**受保护目标还有一道工具级兜底**：命中 deny 阶梯（凭据、``.git/hooks``…）的路径在这里
再拒一次。它不是重复劳动——它是"权限层从未批准"时的失败关闭（直接调用工具、答复超时
收敛为拒绝），与 ``拒绝访问工作区外的路径`` 同一性质。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..policy.permission import APPROVAL_NONE
from .workspace import relative, resolve

if TYPE_CHECKING:  # 只用于标注：tools 不在运行时依赖 runtime 的实例类型
    from ..runtime.state import RunState

MAX_READ_CHARS = 20000
MAX_READ_LINES = 2000
MAX_GLOB_RESULTS = 200


def _root(state: "RunState | None") -> Path | None:
    """运行级工作区根；None 表示让 workspace 模块读进程默认根（调用时读取）。"""
    raw = getattr(state, "workspace_root", None)
    return Path(raw) if raw else None


def _grant(state: "RunState | None", operation: str = "read"):
    """返回限定 ro/rw 口径的授权查询器；没有状态时越界一律回绝。"""
    if state is None:
        return None
    access = "rw" if operation == "write" else "ro"
    return lambda path: state.outside_allowed(path, access)


def _read_outside_ok(state: "RunState | None") -> bool:
    """区外读要不要授权：不要，但必须有 run 级安全规格。

    沙箱把"读整个文件系统"当作已有能力（同 Codex ``workspace-write``：permits reading
    files），所以读区外不查能力账本。仍然要求 ``security`` 存在，是因为受保护目标
    （``~/.ssh``、``/etc/shadow``…）的 deny/ask 判定来自那份阶梯；没有它就只能关闭。
    """
    return getattr(state, "security", None) is not None



def _protected(state: "RunState | None", path: Path, operation: str) -> str | None:
    """命中 deny/ask 阶梯且本次运行没有授权时，返回回绝文本。

    与 ``_grant`` 同一个道理：决定由 ``gate`` 做，这里只是**兜底**——没有授权就不放行，
    所以"gate 没跑过"或"答复超时"都不会变成一次静默放行。
    """
    security = getattr(state, "security", None)
    if security is None:
        return None
    rule = security.ladder.verdict_for(str(path), (operation,))
    if rule is None:
        return None
    if rule.verdict == "deny":
        # deny 档：任何模式、任何批准都不放行（包括 full）。
        return f"错误：{rule.reason}（{rule.tier} 策略禁止访问，任何批准都不能放行）"
    # ask 档：full 整圈预授权，或账本里已经记着这一次批准。
    if security.approval == APPROVAL_NONE:
        return None
    if state is not None and state.ledger.outside_allowed(str(path), "rw" if operation == "write" else "ro"):
        return None
    return f"错误：{rule.reason}（{rule.tier} 策略，需逐次批准）"


def _int(value: Any, *, default: int, minimum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(number, minimum)


def _count_lines(path: Path) -> int:
    """流式数行数：分块读、不驻留（`splitlines()` 会把整个文件变成一份行列表）。"""
    total = 0
    tail_ends_with_newline = True
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1 << 20):
                total += chunk.count(b"\n")
                tail_ends_with_newline = chunk.endswith(b"\n")
    except OSError:
        return total
    if total and not tail_ends_with_newline:
        total += 1  # 最后一行没有换行也算一行
    return total


def _read_window(handle: Any, offset: int, limit: int) -> tuple[list[str], bool]:
    """从第 ``offset`` 行起取最多 ``limit`` 行，返回 (窗口, 是否被行数截断)。

    只把窗口读进内存：以前 `read_text().splitlines()` 会把整个文件（几 GB 的日志
    也一样）变成字符串加一份行列表，再切出两千行——读大文件等于把进程撑爆。
    """
    window: list[str] = []
    hit_limit = False
    for number, line in enumerate(handle, start=1):
        if number < offset:
            continue
        if len(window) == limit:
            hit_limit = True  # 当前这一行没进窗口：后面确实还有
            break
        window.append(line[:-1] if line.endswith("\n") else line)
    return window, hit_limit


def read_file(args: dict[str, Any], *, state: "RunState | None" = None) -> str:
    raw = str(args.get("path", ""))
    path, error = resolve(raw, root=_root(state), outside_ok=_read_outside_ok(state))
    if error:
        return f"错误：{error}"
    assert path is not None  # resolve 成功时必有路径（error 与 path 二选一）
    blocked = _protected(state, path, "read")
    if blocked:
        return blocked
    if not path.exists():
        return f"错误：文件不存在：{raw}"
    if path.is_dir():
        return f"错误：{raw} 是目录；列目录用 glob，或用 bash 的 ls"

    offset = _int(args.get("offset"), default=1, minimum=1)
    limit = _int(args.get("limit"), default=MAX_READ_LINES, minimum=1)

    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            window, hit_limit = _read_window(handle, offset, limit)
    except OSError as exc:
        return f"错误：读取失败：{exc}"

    if not window:
        if offset == 1:
            return ""  # 空文件
        return f"错误：offset {offset} 超出文件范围（共 {_count_lines(path)} 行）"

    text = "\n".join(window)
    reasons = []
    if len(text) > MAX_READ_CHARS:
        text = text[:MAX_READ_CHARS]
        reasons.append("字符数")
    if hit_limit:
        reasons.append(f"行数（共 {_count_lines(path)} 行）")
    if reasons:
        text += f"\n…（已按{'、'.join(reasons)}截断，可调 offset / limit 继续读）"
    return text


def write_file(args: dict[str, Any], *, state: "RunState | None" = None) -> str:
    raw = str(args.get("path", ""))
    path, error = resolve(raw, root=_root(state), outside_ok=_grant(state, "write"))
    if error:
        return f"错误：{error}"
    assert path is not None  # resolve 成功时必有路径
    blocked = _protected(state, path, "write")
    if blocked:
        return blocked

    content = args.get("content")
    if not isinstance(content, str):
        return "错误：缺少参数 content，或它不是字符串"
    if path.is_dir():
        return f"错误：{raw} 是目录"

    existed = path.exists()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        return f"错误：写入失败：{exc}"

    lines = content.count("\n") + 1
    return f"{'已覆盖' if existed else '已新建'} {raw}（{lines} 行，{len(content)} 字符）"


def edit_file(args: dict[str, Any], *, state: "RunState | None" = None) -> str:
    raw = str(args.get("path", ""))
    path, error = resolve(raw, root=_root(state), outside_ok=_grant(state, "write"))
    if error:
        return f"错误：{error}"
    assert path is not None  # resolve 成功时必有路径
    blocked = _protected(state, path, "write")
    if blocked:
        return blocked

    old = args.get("old_string")
    new = args.get("new_string")
    if not isinstance(old, str) or not old:
        return "错误：缺少参数 old_string，或它为空字符串"
    if not isinstance(new, str):
        return "错误：缺少参数 new_string（删除内容时传空字符串）"
    if not path.exists():
        return f"错误：文件不存在：{raw}"
    if path.is_dir():
        return f"错误：{raw} 是目录"

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return f"错误：读取失败：{exc}"

    occurrences = text.count(old)
    if occurrences == 0:
        return "错误：old_string 在文件中不存在，未做任何修改"
    if occurrences > 1:
        return (
            f"错误：old_string 在文件中出现 {occurrences} 次，不唯一；"
            "请带上更多上下文让它唯一"
        )

    try:
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
    except OSError as exc:
        return f"错误：写入失败：{exc}"
    return f"已替换 {raw} 中的 1 处文本"


def glob_files(args: dict[str, Any], *, state: "RunState | None" = None) -> str:
    pattern = str(args.get("pattern", "")).strip()
    if not pattern:
        return "错误：缺少参数 pattern"

    raw = str(args.get("path", ".") or ".")
    root, error = resolve(raw, root=_root(state), outside_ok=_read_outside_ok(state))
    if error:
        return f"错误：{error}"
    assert root is not None  # resolve 成功时必有路径
    blocked = _protected(state, root, "read")
    if blocked:
        return blocked
    if not root.is_dir():
        return f"错误：{raw} 不是目录"

    try:
        matches = sorted(p for p in root.glob(pattern) if p.is_file())
    except (ValueError, OSError) as exc:
        return f"错误：模式非法或查找失败：{exc}"

    if not matches:
        return f"未找到匹配 {pattern} 的文件"

    shown = matches[:MAX_GLOB_RESULTS]
    text = "\n".join(relative(p, root=_root(state)) for p in shown)
    if len(matches) > len(shown):
        text += f"\n…（共 {len(matches)} 个匹配，只显示前 {len(shown)} 个）"
    return text

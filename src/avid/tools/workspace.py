"""路径解析辅助：工作区是默认工作地点，不是 agent 的总安全边界。

**这不是沙箱。** bash 的物理边界由 policy.sandbox 负责；文件工具的读写能力由
policy/permission 的运行级安全规格与能力账本决定。本模块只负责路径归一化、目标识别和
工作区归属判断，不自行作权限裁决。

根目录的解析基准按调用时读取 ``WORKSPACE_ROOT``（不是 import 时拷贝进签名默认值），
测试才能用 monkeypatch 把它换成临时目录；运行级的工作区根由调用方显式传入 ``root=``。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from pathlib import Path

# 在 import 时固定；测试通过 monkeypatch 替换成临时目录。
WORKSPACE_ROOT = Path.cwd()

# bash 里可能带路径的记号：按空白与 shell 元字符切开。**只用于引号内部**——
# 带空格的路径靠引号保护，见 `_iter_marks` 与 `_mark_paths`。
_TOKEN_SPLIT = re.compile(r"[\s;|&()<>'\"]+")
#: shell 的引号与元字符：`_iter_marks` 逐字符扫描时要知道这两个集合。
_QUOTE_CHARS = "'\""
_META_CHARS = ";|&()<>"
#: 引号包住的一整段先按"它本身是不是绝对路径"试一次；`~/`、`$HOME/` 同义。
_QUOTED_WHOLE_PREFIXES = ("/", "~", "$HOME")
_PATHLIKE = re.compile(r"(?:^|/)(?:\.\.?)(?:/|$)")


def _base(root: Path | None = None) -> Path:
    return (root or WORKSPACE_ROOT).resolve()


def is_within(path: Path, base: Path) -> bool:
    """路径是否在 base 之内（含 base 本身）。"""
    return path == base or base in path.parents


def resolve(
    raw: str,
    *,
    root: Path | None = None,
    outside_ok: Callable[[Path], bool] | bool | None = None,
) -> tuple[Path | None, str | None]:
    """把路径解析成工作区内的绝对路径。

    返回 (path, error)：解析失败时 path 为 None，error 是可直接回传给模型的文本。

    ``outside_ok`` 是**权限层已经做出的决定**（账本里记着这个目标、或 ``system`` 模式）：
    真值则放行区外路径。缺省为假——工具自己不判断、只读结果，因此"没有授权"必然失败关闭。
    """
    text = raw.strip()
    if not text:
        return None, "路径为空"

    base = _base(root)
    path = Path(text)
    if not path.is_absolute():
        path = base / path
    path = path.resolve()

    if not is_within(path, base):
        allowed = outside_ok(path) if callable(outside_ok) else bool(outside_ok)
        if not allowed:
            return None, f"拒绝访问工作区外的路径：{raw}"
    return path, None


def relative(path: Path, *, root: Path | None = None) -> str:
    """回传给模型时一律用工作区相对路径，输出更短也更稳定。"""
    base = _base(root)
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def target_path(raw: str, *, root: Path | None = None) -> Path:
    """把一个路径字面量解析成绝对路径（**不做**任何越界判断）。

    与 :func:`resolve` 共用同一份路径数学——识别目标与执行不可能给出不同答案。
    """
    base = _base(root)
    path = Path(raw.strip())
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def outside_target(raw: str, *, root: Path | None = None) -> str | None:
    """文件类工具的目标是否在工作区之外：是则返回绝对路径字符串。

    与 :func:`resolve` 共用同一份路径数学——判定与执行不可能给出不同答案。
    """
    text = raw.strip()
    if not text:
        return None
    base = _base(root)
    path = target_path(text, root=base)
    return None if is_within(path, base) else str(path)


def _candidate(token: str, base: Path) -> Path | None:
    """把一个命令记号解读成路径；不象路径的返回 None。

    **相对路径也算路径**（`or "/" in token`）：`.git/hooks/pre-commit`、`src/avid/cli.py`
    这类写法在策略层必须能与 deny/ask 规则对上——否则"写 `.git/hooks`"会因为没有前导
    `/` 而整条规则失效（E2E 实测过：`echo x > .git/hooks/pre-commit` 在三种模式下都被
    放行）。它们解析到工作区内，因此不改变"是否越界"的判定；改变的只是"这条规则管不管
    得到它"。

    三种"看着像路径、其实不是"的记号要排除（都是现场审计里的假阳性）：

    * URL（``https://pypi.org/simple``）：``://`` 之后是主机名，不是工作区下的相对
      路径；
    * 命令选项（``--target=./libs``）：选项本身不是路径，但 ``=`` 后面的取值是，所以
      取出来继续判；没有 ``=`` 的选项（``-la``）直接不算；
    * Python 属性（``type(e).__name__`` 里的 ``.__name__``）：``._`` 开头的是属性名，
      不是工作区里一个点开头的文件。``.env``、``.git/hooks/pre-commit`` 不受影响。
    """
    token = token.strip().rstrip(",;)")
    if not token:
        return None
    if token.startswith("-"):
        _, sep, value = token.partition("=")
        if not sep:
            return None
        token = value.strip().rstrip(",;)")
        if not token:
            return None
    if "://" in token:
        return None
    if token.startswith("~"):
        return Path.home() / token[2:] if token.startswith("~/") else Path.home()
    if token.startswith("$HOME"):
        return Path.home() / token[6:] if token.startswith("$HOME/") else Path.home()
    if token.startswith("/"):
        return Path(token)
    if token.startswith("._"):
        # `type(e).__name__` 的属性名，不是工作区下的文件。
        return None
    if (
        token == ".."
        or token.startswith("../")
        or _PATHLIKE.search(token)
        or "/" in token  # `src/avid/cli.py`、`.git/hooks/pre-commit`
        or token.startswith(".")  # `.env`、`.bashrc`：点开头的裸文件名也是路径
    ):
        return base / token
    return None


def _iter_marks(command: str) -> Iterator[tuple[str, bool]]:
    """把命令切成记号，并标出这个记号里出现过引号。

    与 _TOKEN_SPLIT 的差别只有一处：**引号内的空白不再是分隔符**。
    cd "/a b c" 于是得到 ("/a b c", True)，而不是拆成两个记号——现场会话
    01a0d277 的工作区根是 /…/Avid workspace，被切开之后 /…/Avid 成了「区外目标」，
    同一份探针被连续 deny 了 15 次。

    引号里的内容有两种身份：带空格的**路径**，或者写给解释器的**程序文本**
    （python3 -c "…"）。这里不猜，两种都交给 _mark_paths 两段式处理。
    """
    buffer: list[str] = []
    quoted = False
    index = 0
    length = len(command)
    while index < length:
        char = command[index]
        if char in _QUOTE_CHARS:
            end = command.find(char, index + 1)
            if end < 0:  # 引号没闭合：剩下的都算内容。宁可多扫，不少扫。
                quoted = True
                buffer.append(command[index + 1 :])
                break
            quoted = True
            buffer.append(command[index + 1 : end])
            index = end + 1
            continue
        if char.isspace() or char in _META_CHARS:
            if buffer:
                yield "".join(buffer), quoted
                buffer, quoted = [], False
            index += 1
            continue
        buffer.append(char)
        index += 1
    if buffer:
        yield "".join(buffer), quoted


def _mark_paths(mark: str, quoted: bool, base: Path) -> list[Path]:
    """一个记号可能对应多个路径：**先整段、后拆开**。

    整段成立的条件很窄：引号包住的一整段**本身就是绝对路径**（cd "/…/Avid workspace"）
    ——带空格的绝对路径只能靠这条活下来。其余记号一律按空白与元字符拆开，于是
    bash -c "rm -rf /etc/passwd"、git commit -m "修 /etc/hosts" 里的绝对路径照样扫得到，
    python3 -c "…" 里的程序文本也不会因为多了一个空格而改变结论。两段都在旧行为上
    只做减法：多出来的是「整段是绝对路径」这一种，少掉的是程序文本里的属性名。
    """
    if quoted and mark.startswith(_QUOTED_WHOLE_PREFIXES):
        whole = _candidate(mark, base)
        if whole is not None:
            return [whole]
    paths: list[Path] = []
    for token in _TOKEN_SPLIT.split(mark):
        if quoted and token.startswith(".") and "/" not in token:
            # 引号里的**程序文本**：点开头又没有斜杠的记号是属性访问
            # （`os .environ.get` 被空白切开后的 `.environ.get`），不是工作区下的
            # 隐藏文件——真要点开头的相对路径会带斜杠（`./.env`）。
            continue
        candidate = _candidate(token, base)
        if candidate is not None:
            paths.append(candidate)
    return paths


def outside_command_target(command: str, *, root: Path | None = None) -> str | None:
    """bash 命令里是否出现工作区之外的路径：是则返回该绝对路径。

    启发式：只看字面量记号（绝对路径、``~``、``$HOME``、含 ``..`` 的相对路径）。
    变量拼接、写入脚本再执行、解释器内构造的路径都绕得过去——这是护栏，不是拦截。
    """
    if not isinstance(command, str) or not command.strip():
        return None
    base = _base(root)
    for resolved in command_targets(command, root=base):
        if not is_within(resolved, base):
            return str(resolved)
    return None


def command_targets(command: str, *, root: Path | None = None) -> tuple[Path, ...]:
    """扫出命令里**所有**像路径的记号（解析成绝对路径，保序去重）。

    这是越界判定与"目标识别"共用的唯一一份扫描器：``outside_command_target``
    只要第一个区外目标，Tool Broker（``policy/action.py``）要全部目标 + 敏感命中。
    两份实现会漂移，所以只有一份。

    两段式（见 _mark_paths）：**先整段、后拆开**。带空格的路径只能靠前一段活下来；
    先拆后合的顺序永远救不回它。
    """
    if not isinstance(command, str) or not command.strip():
        return ()
    base = _base(root)

    found: list[Path] = []
    for mark, quoted in _iter_marks(command):
        for candidate in _mark_paths(mark, quoted, base):
            try:
                resolved = candidate.resolve()
            except (OSError, RuntimeError):  # pragma: no cover - 取决于文件系统
                continue
            if resolved not in found:
                found.append(resolved)
    return tuple(found)

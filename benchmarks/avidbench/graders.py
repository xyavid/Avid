"""确定性判定器：只读最终状态与最终回答，不猜过程、不请模型当裁判。

五种原语：

* `command`            —— 在临时工作区里跑一条命令，判退出码与 stdout 子串；
* `file_contains`      —— 判文件包含文本；
* `file_equals`        —— 判文件内容逐字相等（忽略末尾换行）；
* `json_path_equals`   —— 判 JSON 里某个点路径的值；
* `answer_contains`    —— 判最终 assistant 文本里的子串（归一化后）。

归一化只做三件事：小写、去掉所有空白、去掉逗号。于是 `Sum = 4,176` 与
`sum=4176` 等价。不做同义词、不做模糊匹配——判定必须可复现，这是它能当
「仪器」而不是「感觉」的前提。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: 单条命令 grader 的超时。它判的是"最终状态对不对"，不该等一个卡住的命令。
COMMAND_TIMEOUT_SECONDS = 120.0

#: 工具失败在结果文本里的两类前缀（`runtime/execution.py` 与各工具的错误文案）。
#: 工具失败返回文本、不抛异常，所以"失败"只能从内容前缀认——这里集中一处，
#: 改文案时同步这里。
FAILURE_PREFIXES = ("错误：", "工具执行失败：")

KINDS = ("command", "file_contains", "file_equals", "json_path_equals", "answer_contains")

_REQUIRED: dict[str, tuple[str, ...]] = {
    "command": ("run",),
    "file_contains": ("path", "text"),
    "file_equals": ("path", "text"),
    "json_path_equals": ("path", "pointer", "value"),
    "answer_contains": ("text",),
}

#: 每种 kind 允许出现的字段（不含通用的 kind / name）。多写一个字段就报错——
#: 拼错的字段名（`stdout_contain`）静默被忽略的话，判定就悄悄变弱了。
_ALLOWED: dict[str, tuple[str, ...]] = {
    "command": ("run", "expect_exit", "stdout_contains"),
    "file_contains": ("path", "text"),
    "file_equals": ("path", "text"),
    "json_path_equals": ("path", "pointer", "value"),
    "answer_contains": ("text",),
}


class GraderError(Exception):
    """grader 声明不合法。"""


@dataclass(frozen=True)
class GraderResult:
    kind: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "passed": self.passed, "detail": self.detail}


def normalize(text: str) -> str:
    """归一化：小写、去掉所有空白与逗号。"""
    return "".join(ch for ch in text.lower() if not ch.isspace() and ch != ",")


def is_tool_failure(content: str) -> bool:
    """工具结果是不是一次失败。`TOOL_CALL_FINISHED` 没有 error 字段，只能看文案。"""
    return content.lstrip().startswith(FAILURE_PREFIXES)


def validate_spec(spec: Any) -> None:
    """校验一条 grader 声明。合法就静默返回，不合法抛 `GraderError`。"""
    if not isinstance(spec, dict):
        raise GraderError(f"grader 必须是表，实际是 {type(spec).__name__}")
    kind = spec.get("kind")
    if kind not in KINDS:
        raise GraderError(f"未知 kind {kind!r}；可用：{'、'.join(KINDS)}")
    for key in _REQUIRED[kind]:
        if key not in spec:
            raise GraderError(f"{kind} 缺少字段 {key!r}")
    unknown = set(spec) - set(_ALLOWED[kind]) - {"kind", "name"}
    if unknown:
        raise GraderError(f"{kind} 有未知字段 {sorted(unknown)}")

    if kind == "command":
        if not isinstance(spec["run"], str) or not spec["run"].strip():
            raise GraderError("command.run 必须是非空字符串")
        expect = spec.get("expect_exit", 0)
        if not isinstance(expect, int) or isinstance(expect, bool):
            raise GraderError("command.expect_exit 必须是整数")
        _texts(spec.get("stdout_contains"))
        return

    if kind in ("file_contains", "file_equals"):
        path = spec["path"]
        if not isinstance(path, str) or not path.strip():
            raise GraderError(f"{kind}.path 必须是非空字符串")
        if not _texts(spec["text"]):
            raise GraderError(f"{kind}.text 不能为空")
        return

    if kind == "json_path_equals":
        if not isinstance(spec["pointer"], str):
            raise GraderError("json_path_equals.pointer 必须是字符串（点路径，可为空）")
        path = spec["path"]
        if not isinstance(path, str) or not path.strip():
            raise GraderError("json_path_equals.path 必须是非空字符串")
        return

    if not _texts(spec["text"]):
        raise GraderError("answer_contains.text 不能为空")


def _texts(value: Any) -> list[str]:
    """把 `text` 字段统一成列表；`None` 表示"这一项不判"。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    raise GraderError(f"text 必须是字符串或字符串列表，实际是 {value!r}")


def _safe_path(workspace: Path, rel: Any) -> Path:
    if not isinstance(rel, str) or not rel.strip():
        raise GraderError("path 必须是非空字符串")
    root = workspace.resolve()
    candidate = (root / rel).resolve()
    if candidate != root and root not in candidate.parents:
        raise GraderError(f"path 越出工作区：{rel}")
    return candidate


def _read_text(workspace: Path, rel: Any, kind: str) -> str:
    path = _safe_path(workspace, rel)
    if not path.is_file():
        raise GraderError(f"{kind}：文件不存在 {rel}")
    return path.read_text(encoding="utf-8", errors="replace")


def _json_at(document: Any, pointer: str) -> Any:
    """点路径取值：`a.b.0.c`；空串取整个文档。取不到抛 `GraderError`。"""
    current = document
    if not pointer:
        return current
    for part in pointer.split("."):
        if isinstance(current, list):
            if not part.lstrip("-").isdigit():
                raise GraderError(f"pointer {pointer!r} 的第 {part!r} 段需要数组下标")
            index = int(part)
            if not -len(current) <= index < len(current):
                raise GraderError(f"pointer {pointer!r} 下标越界：{part}")
            current = current[index]
        elif isinstance(current, dict):
            if part not in current:
                raise GraderError(f"pointer {pointer!r} 在 {part!r} 处不存在")
            current = current[part]
        else:
            raise GraderError(f"pointer {pointer!r} 走到非容器值上：{part}")
    return current


def run_grader(
    spec: dict[str, Any],
    *,
    workspace: Path,
    answer: str,
    timeout_seconds: float = COMMAND_TIMEOUT_SECONDS,
) -> GraderResult:
    """跑一条 grader。任何声明或环境问题都变成 `passed=False`，绝不抛给 runner。"""
    validate_spec(spec)
    kind = str(spec["kind"])
    label = str(spec.get("name") or kind)
    try:
        outcome = _evaluate(spec, kind, workspace=workspace, answer=answer,
                            timeout_seconds=timeout_seconds)
    except (GraderError, OSError) as exc:
        return GraderResult(kind=label, passed=False, detail=f"判定器出错：{exc}")
    return GraderResult(kind=label, passed=outcome[0], detail=outcome[1])


def _evaluate(
    spec: dict[str, Any], kind: str, *, workspace: Path, answer: str, timeout_seconds: float
) -> tuple[bool, str]:
    if kind == "command":
        run = str(spec["run"])
        expect = int(spec.get("expect_exit", 0))
        wanted = _texts(spec.get("stdout_contains"))
        try:
            proc = subprocess.run(
                ["bash", "-lc", run],
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return False, f"命令超时（>{timeout_seconds:g}s）：{run}"
        missing = [item for item in wanted if item not in proc.stdout]
        passed = proc.returncode == expect and not missing
        detail = f"exit={proc.returncode}（期望 {expect}）"
        if missing:
            detail += f"；stdout 缺少 {missing}"
            detail += f"；实际 stdout 前 200 字：{proc.stdout[:200]!r}"
        return passed, detail

    if kind == "file_contains":
        text = _read_text(workspace, spec["path"], kind)
        wanted = _texts(spec["text"])
        missing = [item for item in wanted if item not in text]
        return not missing, (
            f"{spec['path']} 命中全部 {len(wanted)} 项" if not missing
            else f"{spec['path']} 缺少 {missing}"
        )

    if kind == "file_equals":
        text = _read_text(workspace, spec["path"], kind).rstrip("\n")
        wanted = [item.rstrip("\n") for item in _texts(spec["text"])]
        passed = text in wanted
        return passed, (
            f"{spec['path']} 与期望逐字相等" if passed
            else f"{spec['path']} 与期望不相等；实际前 200 字：{text[:200]!r}"
        )

    if kind == "json_path_equals":
        raw = _read_text(workspace, spec["path"], kind)
        try:
            document = json.loads(raw)
        except json.JSONDecodeError as exc:
            return False, f"{spec['path']} 不是合法 JSON：{exc}"
        pointer = str(spec["pointer"])
        actual = _json_at(document, pointer)
        expected = spec["value"]
        # `True == 1` 在 Python 里成立，所以布尔与整数要分开认——否则 grader 会
        # 把 `"enabled": 1` 判成 `true`。
        passed = actual == expected and isinstance(actual, bool) == isinstance(expected, bool)
        return passed, f"{spec['path']}#{pointer or '<root>'} = {actual!r}（期望 {expected!r}）"

    if kind == "answer_contains":
        wanted = _texts(spec["text"])
        haystack = normalize(answer)
        missing = [item for item in wanted if normalize(item) not in haystack]
        return not missing, (
            "回答命中全部 " + str(len(wanted)) + " 项" if not missing
            else f"回答缺少 {missing}；实际回答前 200 字：{answer[:200]!r}"
        )

    raise GraderError(f"未知 kind {kind!r}")


def run_graders(
    specs: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    *,
    workspace: Path,
    answer: str,
    timeout_seconds: float = COMMAND_TIMEOUT_SECONDS,
) -> list[GraderResult]:
    """按声明顺序跑全部 grader。`resolved` 由调用方按"全部通过"聚合。"""
    return [
        run_grader(spec, workspace=workspace, answer=answer, timeout_seconds=timeout_seconds)
        for spec in specs
    ]

"""任务图（Task DAG）：给 agent 一张跨会话存活的任务依赖图。

``todo_write`` 的清单项只有 ``content`` 与 ``status``，于是 Harness 回答不了三件事：
一条任务**现在能不能开工**（缺依赖字段）、**谁在做**（缺 owner）、**跨会话怎么引用
同一条任务**（缺稳定 id）。这里用一个任务一个 JSON 文件补上：``blockedBy`` 表达依赖，
``owner`` 表达分工，``task_xxxxxxxx`` + ``.tasks/{id}.json`` 提供稳定标识与耐久。

分层（语义与验收标准见 ``docs/design/runtime-architecture.md`` §17）：

* **库函数层**：``create_task`` / ``update_task`` / ``can_start`` / ``claim_task`` /
  ``complete_task`` / ``get_task`` —— 签名与消息模板逐字对应设计文档，可被单测直接调用；
* **工具外壳层**：``create_task_tool`` 等 —— 把"任务不存在 / 文件损坏 / 校验失败"
  转成以 ``错误：`` 开头的文本（项目约定：工具失败返回文本、不抛异常），成功时把
  ``Task`` 渲染成模型可读的结果。

状态只沿两条边前进：``pending`` →(claim)→ ``in_progress`` →(complete)→ ``completed``。
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import workspace

if TYPE_CHECKING:  # 只用于标注：tools 不在运行时依赖 runtime 的实例类型
    from ..runtime.state import RunState

logger = logging.getLogger("avid.tools.tasks")

# 设计文档 §17.2 的原文常量：目录名保持 ".tasks"。相对路径在**每次操作时**
# 相对 workspace.WORKSPACE_ROOT 解析（工具侧唯一的边界权威），因此测试只要
# monkeypatch 工作区根目录，任务目录就跟着走。
TASKS_DIR = Path(".tasks")

ID_PATTERN = re.compile(r"^task_[0-9a-f]{8}$")
VALID_STATUSES = ("pending", "in_progress", "completed")
MAX_ID_ATTEMPTS = 16

MISSING = "<任务文件缺失>"


class TaskError(Exception):
    """任务操作被拒绝：校验失败、文件损坏、找不到目标。工具外壳把它转成文本。"""


@dataclass
class Task:
    id: str
    subject: str
    description: str
    status: str          # pending | in_progress | completed
    owner: str | None    # 负责当前任务的 Agent
    blockedBy: list[str] # 依赖的任务 ID 列表


def new_task_id() -> str:
    """``task_`` + 8 位随机十六进制。"""
    return f"task_{secrets.token_hex(4)}"


def _serialize(task: Task) -> str:
    return json.dumps(asdict(task), ensure_ascii=False, indent=2)


def _task_from_record(raw: Any, task_id: str) -> Task:
    """把磁盘上的一条记录还原成 Task；任何不一致都报"损坏"，不静默降级。"""
    if not isinstance(raw, dict):
        raise TaskError(f"任务文件损坏：{task_id}（不是 JSON 对象）")

    missing = [key for key in ("id", "subject", "description", "status", "owner", "blockedBy") if key not in raw]
    if missing:
        raise TaskError(f"任务文件损坏：{task_id}（缺字段 {'、'.join(missing)}）")

    task = Task(
        id=raw["id"],
        subject=raw["subject"],
        description=raw["description"],
        status=raw["status"],
        owner=raw["owner"],
        blockedBy=list(raw["blockedBy"]),
    )
    if task.id != task_id:
        raise TaskError(f"任务文件损坏：{task_id}（文件里的 id 是 {task.id!r}）")
    if not isinstance(task.subject, str) or not task.subject.strip():
        raise TaskError(f"任务文件损坏：{task_id}（subject 为空）")
    if not isinstance(task.description, str):
        raise TaskError(f"任务文件损坏：{task_id}（description 不是字符串）")
    if task.status not in VALID_STATUSES:
        raise TaskError(f"任务文件损坏：{task_id}（状态不认识：{task.status!r}）")
    if task.owner is not None and not isinstance(task.owner, str):
        raise TaskError(f"任务文件损坏：{task_id}（owner 既不是字符串也不是 null）")
    if not all(isinstance(item, str) for item in task.blockedBy):
        raise TaskError(f"任务文件损坏：{task_id}（blockedBy 里有非字符串元素）")
    return task


# 按目录共享的读写锁：任务库可以是"每个运行一个实例"（任务跟着工作区走），
# 但同一个工作区里的并发运行必须互斥认领同一条任务，所以锁按**目录**而不是按实例走。
_STORE_LOCKS: dict[str, threading.RLock] = {}
_STORE_LOCKS_GUARD = threading.Lock()


def _lock_for(directory: Path) -> threading.RLock:
    key = str(directory)
    with _STORE_LOCKS_GUARD:
        lock = _STORE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _STORE_LOCKS[key] = lock
        return lock


class TaskStore:
    """校验任务 ID 与读写 ``.tasks/{id}.json``。任务图的唯一写入口。"""

    def __init__(self, directory: Path = TASKS_DIR) -> None:
        self._directory = Path(directory)

    # ---------------- 路径与锁 ----------------

    @property
    def directory(self) -> Path:
        """每次都重新解析：相对路径相对工作区根目录，而不是进程 CWD。"""
        if self._directory.is_absolute():
            return self._directory
        return Path(workspace.WORKSPACE_ROOT) / self._directory

    @property
    def lock(self) -> threading.RLock:
        """读-改-写锁（可重入，按目录共享）。

        ``claim_task`` / ``complete_task`` 是"读 → 判断 → 写"三步，单看每一次读写
        都不足以避免两个线程同时认领同一条任务；工具外壳用这把锁把三步括起来。
        真实的并发源是 ``subagent`` 的线程池（同进程、同工作区）。跨进程不互斥。
        """
        return _lock_for(self.directory)

    def path_for(self, task_id: str) -> Path:
        """把任务 ID 映射成文件路径；ID 必须匹配 ``^task_[0-9a-f]{8}$``。"""
        if not isinstance(task_id, str) or not ID_PATTERN.match(task_id):
            raise TaskError(
                f"任务 ID 非法：{task_id!r}（应为 task_ 加 8 位十六进制字符）"
            )
        return self.directory / f"{task_id}.json"

    # ---------------- 读 ----------------

    def load(self, task_id: str) -> Task | None:
        """读取单个任务：ID 非法或文件不存在返回 None；内容损坏抛 TaskError。"""
        try:
            path = self.path_for(task_id)
        except TaskError:
            return None
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise TaskError(f"任务文件损坏：{task_id}（{exc}）") from exc
        return _task_from_record(raw, task_id)

    def _require(self, task_id: str) -> Task:
        task = self.load(task_id)
        if task is None:
            raise TaskError(f"找不到任务 {task_id}")
        return task

    def list_all(self) -> list[Task]:
        """列出全部任务（按 id 排序）。损坏的文件跳过并记 warning。

        列表是最不该因为一个坏项整体失败的读操作（与阶段 12 的会话列举同一条原则）；
        但**修改路径**不同——``update_dependencies`` 遇到损坏的依赖文件会直接报错，
        不把它当成"缺失"。
        """
        directory = self.directory
        if not directory.is_dir():
            return []
        found: list[Task] = []
        for path in sorted(directory.glob("*.json")):
            if not ID_PATTERN.match(path.stem):
                continue
            try:
                task = self.load(path.stem)
            except TaskError as exc:
                logger.warning("跳过损坏的任务文件 %s：%s", path, exc)
                continue
            if task is not None:
                found.append(task)
        return found

    def _load_all_strict(self) -> dict[str, Task]:
        directory = self.directory
        known: dict[str, Task] = {}
        if not directory.is_dir():
            return known
        for path in sorted(directory.glob("*.json")):
            if not ID_PATTERN.match(path.stem):
                continue
            task = self.load(path.stem)  # 损坏 → TaskError 上抛
            if task is not None:
                known[path.stem] = task
        return known

    # ---------------- 写 ----------------

    def create(self, subject: str, description: str = "") -> Task:
        """校验 subject，分配随机 ID，排他写入。ID 已存在就重新生成，不覆盖。"""
        text = subject.strip() if isinstance(subject, str) else ""
        if not text:
            raise TaskError("subject 不能为空")
        body = description if isinstance(description, str) else ""

        with self.lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            for _ in range(MAX_ID_ATTEMPTS):
                task = Task(
                    id=new_task_id(),
                    subject=text,
                    description=body,
                    status="pending",
                    owner=None,
                    blockedBy=[],
                )
                if self._write_new(task):
                    logger.debug("创建任务 %s（%s）", task.id, task.subject)
                    return task
            raise TaskError(f"连续 {MAX_ID_ATTEMPTS} 次生成的 ID 都已存在，无法创建任务")

    def save(self, task: Task) -> None:
        """覆盖写单个任务文件（只用于已存在任务的字段更新）。"""
        with self.lock:
            self._write_atomic(task)

    def update_dependencies(self, task_id: str, add_blocked_by: list[str]) -> Task:
        """给 pending 且无人认领的任务加依赖。先校验整次修改，再统一保存。"""
        if not isinstance(add_blocked_by, list):
            raise TaskError("addBlockedBy 必须是数组")

        with self.lock:
            task = self._require(task_id)
            if task.status != "pending" or task.owner is not None:
                raise TaskError(
                    f"{task_id} 当前是 {task.status}（owner={task.owner}），"
                    "只能给 pending 且无人认领的任务加依赖"
                )

            known = self._load_all_strict()
            additions: list[str] = []
            for dependency in add_blocked_by:
                if not isinstance(dependency, str):
                    raise TaskError("依赖必须是任务 ID 字符串")
                if dependency == task_id:
                    raise TaskError(f"不能依赖自己：{task_id}")
                if dependency not in known:
                    raise TaskError(f"找不到依赖任务 {dependency}")
                if dependency in task.blockedBy or dependency in additions:
                    continue  # 重复依赖幂等，不产生重复边
                if self._reaches(dependency, task_id, known):
                    raise TaskError(f"加这些依赖会形成环：{task_id} → {dependency}")
                additions.append(dependency)

            # 到这里所有校验都过了，才动内存与磁盘。
            task.blockedBy = [*task.blockedBy, *additions]
            self._write_atomic(task)
            return task

    def _write_new(self, task: Task) -> bool:
        path = self.path_for(task.id)
        try:
            with path.open("x", encoding="utf-8") as handle:  # "x" = O_EXCL
                handle.write(_serialize(task))
        except FileExistsError:
            return False
        except OSError as exc:
            raise TaskError(f"任务写入失败：{path}（{exc}）") from exc
        return True

    def _write_atomic(self, task: Task) -> None:
        path = self.path_for(task.id)
        temp = path.with_name(path.name + ".tmp")
        try:
            temp.write_text(_serialize(task), encoding="utf-8")
            os.replace(temp, path)
        except OSError as exc:
            temp.unlink(missing_ok=True)
            raise TaskError(f"任务写入失败：{path}（{exc}）") from exc

    @staticmethod
    def _reaches(start: str, target: str, known: dict[str, Task]) -> bool:
        """沿 blockedBy 从 start 走，能否走到 target（用于环检测）。"""
        stack = [start]
        seen: set[str] = set()
        while stack:
            current = stack.pop()
            if current == target:
                return True
            if current in seen:
                continue
            seen.add(current)
            node = known.get(current)
            if node is not None:
                stack.extend(node.blockedBy)
        return False


TASKS = TaskStore(TASKS_DIR)


def store_for(state: "RunState | None") -> TaskStore:
    """运行级任务库：任务跟着工作区走，不再固定于进程 CWD。

    每次都新建一个 ``TaskStore``，但**锁按目录共享**（见 ``TaskStore.lock``），
    所以同一个工作区里的并发运行仍然互斥认领同一条任务。
    """
    raw_root = getattr(state, "workspace_root", None)
    return TaskStore(Path(raw_root) / TASKS_DIR) if raw_root else TASKS


# ---------------- 库函数层（签名与消息模板 = 设计文档原文） ----------------
#
# 每个函数都能接一个 ``store``：工具外壳传运行级的，直接调用（测试、脚本）走默认的
# 模块级实例——它的目录在调用时按工作区根解析，因此 monkeypatch 依旧有效。


def load_task(task_id: str, store: TaskStore | None = None) -> Task | None:
    return (store or TASKS).load(task_id)


def list_tasks(store: TaskStore | None = None) -> list[Task]:
    """列出全部任务（按 id 排序）。"""
    return (store or TASKS).list_all()


def incomplete_dependencies(task: Task | None, store: TaskStore | None = None) -> list[str]:
    """返回还没 completed 的前置任务 ID。

    只要有一个不是 completed，**或者对应文件已经不存在**，就算未完成；
    task 本身为 None（任务文件缺失）同样视为未完成。
    """
    if task is None:
        return [MISSING]
    active = store or TASKS
    incomplete: list[str] = []
    for dependency in task.blockedBy:
        upstream = active.load(dependency)
        if upstream is None or upstream.status != "completed":
            incomplete.append(dependency)
    return incomplete


def create_task(subject: str, description: str = "", store: TaskStore | None = None) -> Task:
    return (store or TASKS).create(subject, description)


def update_task(
    task_id: str, addBlockedBy: list[str], store: TaskStore | None = None
) -> Task:
    return (store or TASKS).update_dependencies(task_id, addBlockedBy)


def can_start(task_id: str, store: TaskStore | None = None) -> bool:
    return not incomplete_dependencies(load_task(task_id, store), store)


def claim_task(task_id: str, owner: str = "agent", store: TaskStore | None = None) -> str:
    active = store or TASKS
    task = load_task(task_id, active)
    if task.status != "pending":
        return f"Task {task_id} is {task.status}, cannot claim"
    dependencies = incomplete_dependencies(task, active)
    if dependencies:
        return f"Blocked by: {dependencies}"
    task.owner = owner
    task.status = "in_progress"
    active.save(task)
    return f"Claimed {task_id} ({task.subject})"


def complete_task(
    task_id: str, owner: str = "agent", store: TaskStore | None = None
) -> str:
    active = store or TASKS
    task = load_task(task_id, active)
    if task.status != "in_progress":
        return f"Task {task_id} is {task.status}, cannot complete"
    if task.owner != owner:
        return f"Task {task_id} is owned by {task.owner}, not {owner}"
    ready_before = {t.id for t in list_tasks(active)
                    if t.status == "pending" and t.blockedBy
                    and can_start(t.id, active)}
    task.status = "completed"
    active.save(task)
    unblocked = [t.subject for t in list_tasks(active)
                 if t.status == "pending" and t.blockedBy
                 and t.id not in ready_before
                 and can_start(t.id, active)]
    msg = f"Completed {task_id} ({task.subject})"
    if unblocked:
        msg += f"\nUnblocked: {', '.join(unblocked)}"
    return msg


def get_task(task_id: str, store: TaskStore | None = None) -> str:
    task = load_task(task_id, store)
    return json.dumps(asdict(task), indent=2)


# ---------------- 工具外壳层：失败一律变成以「错误：」开头的文本 ----------------


def _string(args: dict[str, Any], name: str, default: str | None = None) -> str | None:
    value = args.get(name, default)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TaskError(f"{name} 必须是字符串")
    return value


def _string_list(args: dict[str, Any], name: str) -> list[str]:
    value = args.get(name)
    if value is None:
        return []
    if not isinstance(value, list):
        raise TaskError(f"{name} 必须是数组")
    return value


def create_task_tool(args: dict[str, Any], *, state: "RunState | None" = None) -> str:
    try:
        subject = _string(args, "subject", "")
        description = _string(args, "description", "")
        task = create_task(subject or "", description or "", store_for(state))
    except TaskError as exc:
        return f"错误：{exc}"
    return task.id


def update_task_tool(args: dict[str, Any], *, state: "RunState | None" = None) -> str:
    try:
        task_id = _string(args, "task_id", "")
        task = update_task(
            task_id or "", _string_list(args, "addBlockedBy"), store_for(state)
        )
    except TaskError as exc:
        return f"错误：{exc}"
    return json.dumps(asdict(task), ensure_ascii=False, indent=2)


def can_start_tool(args: dict[str, Any], *, state: "RunState | None" = None) -> str:
    try:
        task_id = _string(args, "task_id", "")
        return str(can_start(task_id or "", store_for(state)))
    except TaskError as exc:
        return f"错误：{exc}"


def _transition(args: dict[str, Any], action, state: "RunState | None") -> str:
    """claim / complete 共用：整段读-改-写拿锁，缺任务先变文本。"""
    try:
        task_id = _string(args, "task_id", "")
        owner = _string(args, "owner", "agent")
    except TaskError as exc:
        return f"错误：{exc}"
    store = store_for(state)
    with store.lock:
        try:
            if store.load(task_id or "") is None:
                return f"错误：找不到任务 {task_id}"
        except TaskError as exc:
            return f"错误：{exc}"
        return action(task_id or "", owner or "agent", store)


def claim_task_tool(args: dict[str, Any], *, state: "RunState | None" = None) -> str:
    return _transition(args, claim_task, state)


def complete_task_tool(args: dict[str, Any], *, state: "RunState | None" = None) -> str:
    return _transition(args, complete_task, state)


def get_task_tool(args: dict[str, Any], *, state: "RunState | None" = None) -> str:
    try:
        task_id = _string(args, "task_id", "")
        store = store_for(state)
        if store.load(task_id or "") is None:
            return f"错误：找不到任务 {task_id}"
        return get_task(task_id or "", store)
    except TaskError as exc:
        return f"错误：{exc}"

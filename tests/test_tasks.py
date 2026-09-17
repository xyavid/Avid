"""任务图（Task DAG）：数据结构、落盘、六个工具的语义、状态机与解锁行为。

逐条对应 ``docs/design/runtime-architecture.md`` §17.9 的 12 条验收标准：
签名与消息模板按 §17.4 的原文、依赖方向按 §17.6 的图、状态术语统一为
pending / in_progress / completed。
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

import pytest

from avid.ai.client import Turn, Usage
from avid.ai.config import Config
from avid.runtime import hooks
from avid.runtime.execution import STATEFUL_TOOLS
from avid.runtime.loop import agent_loop
from avid.tools import TOOLS, TOOL_IMPLS
from avid.tools import tasks as task_tools
from avid.tools.tasks import TaskError
from avid.tools import workspace

CONFIG = Config(api_key="k", base_url="http://localhost", model="m")
ID_RE = re.compile(r"^task_[0-9a-f]{8}$")


# ---------------- 夹具与助手 ----------------


@pytest.fixture
def sandbox(monkeypatch, tmp_path: Path) -> Path:
    """任务目录跟着工作区根目录走，测试不碰真实的 .tasks/。"""
    monkeypatch.setattr(workspace, "WORKSPACE_ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def no_hooks(monkeypatch):
    monkeypatch.setattr(hooks, "HOOKS", {event: [] for event in hooks.EVENTS})


def task_dir(root: Path) -> Path:
    return root / ".tasks"


def record(task_id: str, root: Path) -> dict:
    return json.loads((task_dir(root) / f"{task_id}.json").read_text(encoding="utf-8"))


def raw(task_id: str, root: Path) -> str:
    return (task_dir(root) / f"{task_id}.json").read_text(encoding="utf-8")


def make(subject: str, description: str = "") -> str:
    return task_tools.create_task(subject, description).id


def make_turn(text="", tool_calls=(), finish_reason="stop"):
    return Turn(
        message={
            "role": "assistant",
            "content": text,
            **({"tool_calls": list(tool_calls)} if tool_calls else {}),
        },
        text=text,
        tool_calls=list(tool_calls),
        usage=Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
        model="m",
        finish_reason=finish_reason,
    )


def tool_call(name, arguments, call_id="call_1"):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
    }


class FakeChat:
    def __init__(self, *turns):
        self.turns = list(turns)
        self.requests = []

    def __call__(self, config, messages, **kwargs):
        self.requests.append({"messages": [dict(m) for m in messages], **kwargs})
        return self.turns[len(self.requests) - 1]


# ---------------- 1/2/3/4：数据结构、落盘、ID 与排他写入 ----------------


def test_create_task_writes_one_file_with_all_six_fields(sandbox):
    task_id = make("schema", "设计表结构")

    assert ID_RE.match(task_id)
    path = task_dir(sandbox) / f"{task_id}.json"
    assert path.is_file()
    assert record(task_id, sandbox) == {
        "id": task_id,
        "subject": "schema",
        "description": "设计表结构",
        "status": "pending",
        "owner": None,
        "blockedBy": [],
    }
    assert sorted(item.name for item in task_dir(sandbox).glob("*.json")) == [f"{task_id}.json"]


def test_each_task_is_a_separate_file(sandbox):
    ids = [make(f"步骤{index}") for index in range(3)]
    assert len(set(ids)) == 3
    assert sorted(item.name for item in task_dir(sandbox).glob("*.json")) == sorted(
        f"{task_id}.json" for task_id in ids
    )


def test_empty_subject_is_rejected_and_nothing_is_written(sandbox):
    assert task_tools.create_task_tool({"subject": "   "}) == "错误：subject 不能为空"
    assert not task_dir(sandbox).exists() or list(task_dir(sandbox).glob("*.json")) == []


def test_subject_is_stripped_and_description_defaults_to_empty(sandbox):
    task_id = make("  schema  ")
    assert record(task_id, sandbox)["subject"] == "schema"
    assert record(task_id, sandbox)["description"] == ""


def test_id_collision_regenerates_and_keeps_the_existing_file(sandbox, monkeypatch):
    task_dir(sandbox).mkdir(parents=True, exist_ok=True)
    taken = task_dir(sandbox) / "task_00000000.json"
    taken.write_text(
        json.dumps(
            {
                "id": "task_00000000",
                "subject": "占位",
                "description": "",
                "status": "completed",
                "owner": None,
                "blockedBy": [],
            }
        ),
        encoding="utf-8",
    )
    ids = iter(["task_00000000", "task_00000000", "task_11111111"])
    monkeypatch.setattr(task_tools, "new_task_id", lambda: next(ids))

    assert task_tools.create_task("新的").id == "task_11111111"
    assert json.loads(taken.read_text(encoding="utf-8"))["subject"] == "占位"  # 没被覆盖


def test_task_id_must_match_the_pattern_and_stay_inside_the_directory(sandbox):
    for bad in ("", "task_1234", "task_ZZZZZZZZ", "../task_00000000", "/etc/passwd", "task_0000000g"):
        with pytest.raises(TaskError):
            task_tools.TASKS.path_for(bad)
    assert task_tools.get_task_tool({"task_id": "../../etc/passwd"}) == "错误：找不到任务 ../../etc/passwd"


# ---------------- 5/6/7：两阶段构图与 update_task 的校验 ----------------


def test_update_task_adds_edges_and_is_idempotent(sandbox):
    schema, endpoints = make("schema"), make("endpoints")

    updated = task_tools.update_task(endpoints, [schema])
    assert updated.blockedBy == [schema]
    assert record(endpoints, sandbox)["blockedBy"] == [schema]

    again = task_tools.update_task(endpoints, [schema, schema])
    assert again.blockedBy == [schema]  # 重复依赖不产生重复边
    assert task_tools.update_task_tool(
        {"task_id": endpoints, "addBlockedBy": []}
    ) != "错误：addBlockedBy 必须是数组"


def test_update_task_tool_returns_the_full_task_json(sandbox):
    schema, docs = make("schema"), make("docs")
    text = task_tools.update_task_tool({"task_id": docs, "addBlockedBy": [schema]})
    payload = json.loads(text)
    assert payload["id"] == docs
    assert payload["blockedBy"] == [schema]
    assert payload["status"] == "pending"


@pytest.mark.parametrize(
    "scenario",
    ["unknown_target", "unknown_dependency", "claimed_target", "completed_target", "self", "cycle"],
)
def test_update_task_rejects_and_leaves_disk_untouched(sandbox, scenario):
    a, b, c = make("a"), make("b"), make("c")
    target, additions = c, [a]
    if scenario == "cycle":
        task_tools.update_task(a, [b])  # a 依赖 b
        target, additions = b, [a]  # 再让 b 依赖 a → 成环
    elif scenario == "unknown_target":
        target, additions = "task_deadbeef", [a]
    elif scenario == "unknown_dependency":
        additions = ["task_deadbeef"]
    elif scenario == "claimed_target":
        task_tools.claim_task(c)
    elif scenario == "completed_target":
        task_tools.claim_task(c)
        task_tools.complete_task(c)
    elif scenario == "self":
        target, additions = a, [a]

    before = {path.name: path.read_text(encoding="utf-8") for path in task_dir(sandbox).glob("*.json")}
    with pytest.raises(TaskError):
        task_tools.update_task(target, additions)
    after = {path.name: path.read_text(encoding="utf-8") for path in task_dir(sandbox).glob("*.json")}

    assert before == after  # 整次修改先校验再统一保存：被拒时磁盘一字不动


def test_update_task_rejects_a_non_array_addBlockedBy_through_the_tool(sandbox):
    a = make("a")
    assert task_tools.update_task_tool({"task_id": a, "addBlockedBy": "nope"}).startswith("错误：")


def test_two_phase_construction_through_the_agent_loop(sandbox, no_hooks):
    """第 1 轮建全部节点，第 2 轮用上一轮的 ID 连边——同轮内的 ID 互相不可见。"""

    class GraphChat:
        def __init__(self):
            self.round = 0

        def __call__(self, config, messages, **kwargs):
            self.round += 1
            if self.round == 1:
                return make_turn(
                    "",
                    [
                        tool_call("create_task", {"subject": "schema"}, "c1"),
                        tool_call("create_task", {"subject": "endpoints"}, "c2"),
                    ],
                )
            if self.round == 2:
                ids = [m["content"] for m in messages if m["role"] == "tool"]
                assert all(ID_RE.match(item) for item in ids)
                return make_turn(
                    "",
                    [tool_call("update_task", {"task_id": ids[1], "addBlockedBy": [ids[0]]}, "c3")],
                )
            return make_turn("图建好了")

    messages = [{"role": "user", "content": "建图"}]
    assert agent_loop(messages, config=CONFIG, chat=GraphChat()) == "图建好了"

    tasks = task_tools.list_tasks()
    assert {item.subject for item in tasks} == {"schema", "endpoints"}  # 顺序按 id 序，与建图顺序无关
    endpoints = next(item for item in tasks if item.subject == "endpoints")
    schema = next(item for item in tasks if item.subject == "schema")
    assert endpoints.blockedBy == [schema.id]


def test_guessed_id_cannot_be_used_in_the_same_reply(sandbox, no_hooks):
    chat = FakeChat(
        make_turn(
            "",
            [
                tool_call("create_task", {"subject": "schema"}, "c1"),
                tool_call(
                    "update_task",
                    {"task_id": "task_deadbeef", "addBlockedBy": ["task_cafebabe"]},
                    "c2",
                ),
            ],
        ),
        make_turn("好"),
    )
    messages = [{"role": "user", "content": "建图"}]
    agent_loop(messages, config=CONFIG, chat=chat)

    results = [message["content"] for message in messages if message["role"] == "tool"]
    assert ID_RE.match(results[0])
    assert results[1] == "错误：找不到任务 task_deadbeef"
    assert task_tools.list_tasks()[0].blockedBy == []


# ---------------- 8：can_start ----------------


def test_can_start_follows_completed_dependencies(sandbox):
    schema, endpoints = make("schema"), make("endpoints")
    task_tools.update_task(endpoints, [schema])

    assert task_tools.can_start(endpoints) is False
    assert task_tools.can_start_tool({"task_id": endpoints}) == "False"

    task_tools.claim_task(schema)
    assert task_tools.can_start(endpoints) is False  # in_progress 不算完成
    task_tools.complete_task(schema)

    assert task_tools.can_start(endpoints) is True
    assert task_tools.can_start_tool({"task_id": endpoints}) == "True"


def test_can_start_is_false_when_a_dependency_file_disappears(sandbox):
    schema, endpoints = make("schema"), make("endpoints")
    task_tools.update_task(endpoints, [schema])
    task_tools.claim_task(schema)
    task_tools.complete_task(schema)
    assert task_tools.can_start(endpoints) is True

    os.remove(task_dir(sandbox) / f"{schema}.json")
    assert task_tools.can_start(endpoints) is False


def test_can_start_is_false_for_a_missing_task(sandbox):
    assert task_tools.can_start("task_00000000") is False
    assert task_tools.can_start_tool({"task_id": "task_00000000"}) == "False"


# ---------------- 9：claim_task ----------------


def test_claim_sets_owner_and_status(sandbox):
    task_id = make("schema")
    assert task_tools.claim_task(task_id, "agent-1") == f"Claimed {task_id} (schema)"
    stored = record(task_id, sandbox)
    assert stored["status"] == "in_progress"
    assert stored["owner"] == "agent-1"


def test_claim_rejects_non_pending_with_the_original_message(sandbox):
    task_id = make("schema")
    task_tools.claim_task(task_id)
    assert (
        task_tools.claim_task(task_id)
        == f"Task {task_id} is in_progress, cannot claim"
    )


def test_claim_rejects_incomplete_dependencies_with_the_original_message(sandbox):
    schema, endpoints = make("schema"), make("endpoints")
    task_tools.update_task(endpoints, [schema])
    assert task_tools.claim_task_tool({"task_id": endpoints}) == f"Blocked by: ['{schema}']"
    assert record(endpoints, sandbox)["status"] == "pending"


def test_claim_missing_task_returns_error_text(sandbox):
    assert task_tools.claim_task_tool({"task_id": "task_00000000"}) == "错误：找不到任务 task_00000000"


def test_concurrent_claims_leave_exactly_one_winner(sandbox):
    task_id = make("抢一条任务")
    results: list[str] = []
    barrier = threading.Barrier(2)

    def worker(name: str) -> None:
        barrier.wait(5)
        results.append(task_tools.claim_task_tool({"task_id": task_id, "owner": name}))

    threads = [threading.Thread(target=worker, args=(f"agent-{index}",)) for index in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)

    assert sum(result.startswith("Claimed") for result in results) == 1
    assert sum("cannot claim" in result for result in results) == 1
    stored = record(task_id, sandbox)
    assert stored["status"] == "in_progress"
    assert stored["owner"] in {"agent-1", "agent-2"}


# ---------------- 10：complete_task 与解锁 ----------------


def test_complete_reports_newly_unblocked_downstream(sandbox):
    schema, endpoints, docs = make("schema"), make("endpoints"), make("docs")
    task_tools.update_task(endpoints, [schema])
    task_tools.update_task(docs, [schema])
    task_tools.claim_task(schema)

    message = task_tools.complete_task(schema)
    head, _, tail = message.partition("\nUnblocked: ")
    assert head == f"Completed {schema} (schema)"
    # 顺序按 list_tasks() 的 id 序（不保证与建图顺序一致），因此断言集合。
    assert set(tail.split(", ")) == {"endpoints", "docs"}
    assert task_tools.can_start(endpoints) is True
    assert task_tools.can_start(docs) is True


def test_complete_without_downstream_has_no_unblocked_line(sandbox):
    task_id = make("孤岛")
    task_tools.claim_task(task_id)
    message = task_tools.complete_task(task_id)
    assert message == f"Completed {task_id} (孤岛)"
    assert "Unblocked" not in message


def test_complete_only_reports_tasks_unblocked_by_this_completion(sandbox):
    a, b, d = make("a"), make("b"), make("d")
    task_tools.update_task(d, [a])
    task_tools.claim_task(a)
    assert "Unblocked: d" in task_tools.complete_task(a)  # d 这次被解锁

    task_tools.claim_task(b)
    assert "Unblocked" not in task_tools.complete_task(b)  # d 早已就绪，不重复报


def test_complete_rejects_wrong_status_and_wrong_owner(sandbox):
    task_id = make("schema")
    assert (
        task_tools.complete_task(task_id)
        == f"Task {task_id} is pending, cannot complete"
    )

    task_tools.claim_task(task_id, "agent-1")
    assert (
        task_tools.complete_task(task_id, "agent")
        == f"Task {task_id} is owned by agent-1, not agent"
    )
    assert record(task_id, sandbox)["status"] == "in_progress"  # 拒绝时状态不变


def test_complete_missing_task_returns_error_text(sandbox):
    assert task_tools.complete_task_tool({"task_id": "task_00000000"}) == (
        "错误：找不到任务 task_00000000"
    )


def test_full_dag_walk_from_the_design_chapter(sandbox):
    """§17.6 的图跑一遍：schema → endpoints → tests，schema → docs → deploy。"""
    schema, endpoints, docs, tests, deploy = (make(name) for name in ("schema", "endpoints", "docs", "tests", "deploy"))
    task_tools.update_task(endpoints, [schema])
    task_tools.update_task(docs, [schema])
    task_tools.update_task(tests, [endpoints])
    task_tools.update_task(deploy, [tests, docs])

    assert task_tools.complete_task_tool({"task_id": schema, "owner": "agent"}) == (
        f"Task {schema} is pending, cannot complete"
    )
    task_tools.claim_task(schema)
    message = task_tools.complete_task(schema)
    assert set(message.partition("\nUnblocked: ")[2].split(", ")) == {"endpoints", "docs"}

    task_tools.claim_task(endpoints, "agent-1")
    assert task_tools.can_start(tests) is False
    task_tools.complete_task(endpoints, "agent-1")
    task_tools.claim_task(docs)
    task_tools.complete_task(docs)
    assert task_tools.can_start(deploy) is False  # tests 还没完成
    task_tools.claim_task(tests)
    task_tools.complete_task(tests)
    assert task_tools.can_start(deploy) is True


# ---------------- 11/12：get_task、术语与注册 ----------------


def test_get_task_returns_the_full_json_including_description(sandbox):
    task_id = make("deploy", "把服务发布到预发环境并跑一遍冒烟")
    payload = json.loads(task_tools.get_task(task_id))
    assert payload == {
        "id": task_id,
        "subject": "deploy",
        "description": "把服务发布到预发环境并跑一遍冒烟",
        "status": "pending",
        "owner": None,
        "blockedBy": [],
    }


def test_get_task_missing_returns_error_text(sandbox):
    assert task_tools.get_task_tool({"task_id": "task_00000000"}) == "错误：找不到任务 task_00000000"


def test_status_terminology_is_exactly_the_three_names(sandbox):
    assert set(task_tools.VALID_STATUSES) == {"pending", "in_progress", "completed"}
    task_id = make("schema")
    assert record(task_id, sandbox)["status"] == "pending"
    task_tools.claim_task(task_id)
    assert record(task_id, sandbox)["status"] == "in_progress"
    task_tools.complete_task(task_id)
    assert record(task_id, sandbox)["status"] == "completed"

    enum = next(
        item for item in TOOLS if item["function"]["name"] == "create_task"
    )
    assert "status" not in enum["function"]["parameters"]["properties"]


def test_task_tools_are_registered_and_stateless(sandbox):
    names = {"create_task", "update_task", "can_start", "claim_task", "complete_task", "get_task"}
    assert names <= set(TOOL_IMPLS)
    assert names <= {item["function"]["name"] for item in TOOLS}
    assert names & set(STATEFUL_TOOLS) == set()  # 状态在文件里，不需要 RunState


# ---------------- 容错：损坏文件与列表 ----------------


def test_list_tasks_skips_corrupt_files_but_load_and_tools_report_them(sandbox, caplog):
    good = make("好的")
    task_dir(sandbox).joinpath("task_deadbeef.json").write_text("{ 坏掉的 JSON", encoding="utf-8")

    assert [item.id for item in task_tools.list_tasks()] == [good]
    with pytest.raises(TaskError):
        task_tools.load_task("task_deadbeef")
    assert task_tools.get_task_tool({"task_id": "task_deadbeef"}).startswith("错误：任务文件损坏")
    assert "跳过损坏的任务文件" in caplog.text


def test_update_task_refuses_to_treat_a_corrupt_dependency_as_missing(sandbox):
    target = make("target")
    task_dir(sandbox).joinpath("task_deadbeef.json").write_text("{ 坏", encoding="utf-8")
    with pytest.raises(TaskError) as info:
        task_tools.update_task(target, ["task_deadbeef"])
    assert "损坏" in str(info.value)
    assert record(target, sandbox)["blockedBy"] == []


def test_complete_task_reports_a_corrupt_target_as_an_error(sandbox):
    task_id = make("坏的")
    task_tools.claim_task(task_id)
    (task_dir(sandbox) / f"{task_id}.json").write_text("{ 坏", encoding="utf-8")
    assert task_tools.complete_task_tool({"task_id": task_id}).startswith("错误：任务文件损坏")

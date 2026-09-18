"""stress：长会话与性能门禁（默认不跑，`pytest -m stress` 或 CI 的 stress job 跑）。

为什么需要它：这些路径的正确性由普通用例守着，但**成本**只能靠量级守住。历史上
两处退化都不是功能错误、而是复杂度错误——只有把规模拉起来才会红：

* 会话列表每个会话都重放整个文件：20 会话 5.9 MB 要 54 ms，会话一多首屏就是秒级；
* 每个 delta 都全量扫事件缓冲：8000 分片 1.05 s，而且跑在读模型 SSE 的线程里。

因此这里的阈值都留了 10–30 倍余量：它们该抓的是**复杂度**（平方级、每会话全量
重放），不是几个百分点的抖动。写用例时优先断言机制（"一次都没 open"），时间只是
第二道保险。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from avid.runtime import events
from avid.session import (
    JsonlSessionRepo,
    JsonlSessionMetadata,
    STORAGE_VERSION,
)
from avid.session.jsonl import (
    JsonlHeader,
    JsonlStorage,
    encode_header,
    encode_transaction,
)
from avid.session.types import CommittedEntry, CommittedValueSet, NewEntry
from avid.session.values import BRANCH_TIP_NS, SESSION_NAME_NS
from avid.svc import Services
from avid.svc.runs import RunRecord, RunRegistry

pytestmark = pytest.mark.stress

USER = {"role": "user", "content": "一"}
ASSISTANT = {"role": "assistant", "content": "二"}


def write_session_file(
    directory: Path, session_id: str, messages: int, *, payload: int = 2000
) -> Path:
    """直接写 JSONL 行，不走 recorder 的每条 fsync。

    造 200 个会话要写一万多条消息——真走记录器的话光是 fsync 就要几分钟，
    stress 用例本身不能成为瓶颈。
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"2026-01-01T00-00-00-000_{session_id}.jsonl"
    lines = [encode_header(JsonlHeader(session_id, STORAGE_VERSION, 1))]
    parent: str | None = None
    for index in range(1, messages + 1):
        entry_id = f"{session_id}-e{index}"
        message = USER if index % 2 else {**ASSISTANT, "content": "二" * payload}
        lines.append(
            encode_transaction(
                [CommittedEntry(index, 1, NewEntry(entry_id, parent, message=message))]
            )
        )
        parent = entry_id
    lines.append(
        encode_transaction(
            [CommittedValueSet(messages + 1, BRANCH_TIP_NS, "main", parent or "")]
        )
    )
    lines.append(
        encode_transaction(
            [CommittedValueSet(messages + 2, SESSION_NAME_NS, "", f"会话 {session_id}")]
        )
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_stress_session_list_does_not_replay_each_session(tmp_path, monkeypatch):
    """列表页的成本必须与**会话数**成正比，而不是"会话数 × 文件大小"。

    以前每个会话都 open() 一次（逐行 JSON 解析 + 建 SessionState）：200 个
    120 KB 的会话实测要数秒。现在只读一次文件 + 解析尾部窗口，并且一次都不 open。
    """
    sessions_root = tmp_path / "sessions"
    count = 200
    for index in range(count):
        write_session_file(sessions_root, f"stress-{index:03d}", messages=60)

    opened: list[str] = []
    real_open = JsonlStorage.open

    def counting_open(path, **kwargs):
        opened.append(str(path))
        return real_open(path, **kwargs)

    monkeypatch.setattr(JsonlStorage, "open", staticmethod(counting_open))
    services = Services(root=sessions_root)
    try:
        started = time.perf_counter()
        listed = services.sessions.list_sessions()
        elapsed = time.perf_counter() - started
    finally:
        services.close()

    assert len(listed) == count
    assert opened == [], "列表页不该重放会话"
    sample = next(item for item in listed if item["id"] == "stress-007")
    assert sample["message_count"] == 60
    assert sample["name"] == "会话 stress-007"
    assert elapsed < 1.5, f"{count} 个会话的列表花了 {elapsed:.2f}s（复杂度退化了？）"


def test_stress_delta_emit_stays_linear(tmp_path):
    """每个 delta 的记账必须是 O(1)：全量扫缓冲会让长回复退化成平方级。

    修之前 8000 分片要 1.05 s（131 µs/条，而且随着缓冲变大越来越慢）；
    修之后约 14 ms（1.7 µs/条）。阈值 0.5 s 只抓复杂度，不抓抖动。
    """
    registry = RunRegistry(None, buffer_size=512)
    record = RunRecord(run_id="stress", session_id="s", started_at=0)
    registry.emit(record, events.RUN_STARTED)

    total = 8000
    started = time.perf_counter()
    for _ in range(total):
        registry.emit_delta(record, registry, "tok")
    elapsed = time.perf_counter() - started

    assert elapsed < 0.5, f"{total} 个 delta 花了 {elapsed:.2f}s（记账复杂度退化了？）"
    assert record.absolute_index() == record.dropped + len(record.events)


def test_stress_long_session_summarize_and_replay_stay_usable(tmp_path):
    """长会话的两条读路径都要能用：摘要（列表页）与重放（打开会话）。

    5000 条消息的会话：摘要是"读一次 + 尾部窗口"，重放是"逐行解析 + 建状态"。
    两者都不该出现分钟级退化——真到那个量级说明有人在读路径上加了全量扫描。
    """
    sessions_root = tmp_path / "sessions"
    path = write_session_file(sessions_root, "long", messages=5000)

    repo = JsonlSessionRepo(sessions_root)
    try:
        started = time.perf_counter()
        summary = repo.summarize(repo.list()[0])
        summarize_elapsed = time.perf_counter() - started

        started = time.perf_counter()
        session = repo.open(
            JsonlSessionMetadata(
                id="long",
                created_at=1,
                storage_version=STORAGE_VERSION,
                path=path,
            )
        )
        open_elapsed = time.perf_counter() - started
        session.close()
    finally:
        repo.close()

    assert summary.message_count == 5000
    assert summarize_elapsed < 1.0, f"摘要花了 {summarize_elapsed:.2f}s"
    assert open_elapsed < 3.0, f"重放 5000 条花了 {open_elapsed:.2f}s"

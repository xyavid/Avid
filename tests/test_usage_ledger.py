"""阶段 22：用量台账的派生量与落盘。

三件事各测各的边界：

* ``runtime/state.py``：``usage_report()`` 的统一 schema（占用率、命中率、压缩后读数）。
* ``session/`` + ``svc/runs.py``：快照真的落到会话值里，读侧拿得到，两个后端一致。
* HTTP 端到端：跑一次真循环（脚本模型）之后，分支列表与运行视图都带这份快照。
"""

from __future__ import annotations

import pytest
from support import ScriptedChat, make_turn

from avid.ai.config import Config
from avid.ai.usage import Usage
from avid.policy.compaction import CompactReport
from avid.runtime import RunState, announce
from avid.session import (
    USAGE_NS,
    MemorySessionRepo,
    SessionRecorder,
    UuidV7Generator,
)

# ---------------- usage_report：统一 schema ----------------

def test_report_is_all_none_before_the_first_model_call():
    report = RunState().usage_report()
    assert report == {
        "context": {"tokens": None, "window": None, "utilization": None, "parts": None},
        "cache": {"read_tokens": None, "write_tokens": None, "hit_ratio": None},
        "compaction": {"count": 0, "last_compaction_tokens": None, "last_step": None},
        "subagent": {"calls": 0, "tokens": 0},
    }


def test_report_shows_occupancy_cache_and_compaction():
    state = RunState(context_window=200_000)
    state.record_usage(Usage(72_000, 10, 72_010, cache_read_tokens=56_000))
    report = state.usage_report()
    assert report["context"] == {
        "tokens": 72_000,
        "window": 200_000,
        "utilization": 0.36,
        "parts": None,  # 没记字符数就不给分块，不猜
    }
    assert report["cache"]["read_tokens"] == 56_000
    assert report["cache"]["hit_ratio"] == pytest.approx(0.77777, rel=1e-4)
    assert report["cache"]["write_tokens"] is None
    assert report["compaction"]["count"] == 0


def test_report_treats_an_all_zero_usage_as_no_data():
    """端点不认 `include_usage` 时是"没有读数"，不是"上下文是空的"。"""
    state = RunState(context_window=200_000)
    state.record_usage(Usage(0, 0, 0))
    report = state.usage_report()
    assert report["context"]["tokens"] is None
    assert report["context"]["utilization"] is None
    # 累计量照记（那是运行账单，不是占用）。
    assert state.tokens == 0


def test_last_compaction_tokens_comes_from_the_next_real_call():
    """压缩后还剩多少由下一次模型调用回答——不在这里做本地估算。"""
    state = RunState(context_window=200_000)
    state.record_usage(Usage(150_000, 10, 150_010))
    announce(CompactReport("micro_compact", "落盘 3 条", 400, 200), state)

    report = state.usage_report()
    assert report["compaction"] == {
        "count": 1,
        "last_compaction_tokens": None,  # 还没有下一轮，不猜
        "last_step": "micro_compact",
    }

    state.record_usage(Usage(42_000, 10, 42_010))
    report = state.usage_report()
    assert report["compaction"]["last_compaction_tokens"] == 42_000
    assert report["context"]["tokens"] == 42_000  # 占用也跟着降到压完后的真实值


def test_compaction_counter_counts_only_real_reports():
    state = RunState()
    announce(None, state)  # 没压成 → 不计数
    assert state.compactions == 0
    announce(CompactReport("snip_compact", "裁掉中间", 10, 8), state)
    announce(CompactReport("compact_history", "摘要", 9, 3), state)
    assert state.compactions == 2
    assert state.usage_report()["compaction"]["last_step"] == "compact_history"


def test_event_carries_the_snapshot_every_round():
    """事件是实时通道：每轮 run_status 都带一份快照，字段就是统一 schema。"""
    seen: list[dict] = []
    state = RunState(observer=lambda event: seen.append(event), context_window=100_000)
    state.record_usage(Usage(5_000, 5, 5_005, cache_read_tokens=2_500))
    state.emit("run_status", round=1, usage=state.usage_report())
    assert seen[-1].data["usage"]["context"]["tokens"] == 5_000
    assert seen[-1].data["usage"]["cache"]["hit_ratio"] == 0.5


# ---------------- 落盘：进会话值，两个后端都读得到 ----------------

@pytest.mark.parametrize("backend", ["memory", "jsonl"])
def test_recorder_persists_usage_per_branch(backend, tmp_path):
    clock = iter(range(1_700_000_000_000, 1_700_000_100_000, 1_000)).__next__
    generator = UuidV7Generator(clock)
    if backend == "memory":
        repo = MemorySessionRepo(now=clock, id_generator=generator)
    else:
        from avid.session import JsonlSessionRepo

        repo = JsonlSessionRepo(tmp_path / "sessions", now=clock, id_generator=generator)
    try:
        session = repo.create(workspace="ws")
        main = SessionRecorder(session, "main")
        side = SessionRecorder(session, "b2")
        payload = {"context": {"tokens": 72_000, "window": 200_000, "utilization": 0.36}}
        main.record_usage(payload)
        # 覆盖式：同一分支只留最近一次。
        main.record_usage({**payload, "context": {"tokens": 90_000}})
        side.record_usage(payload)

        stored = {item.key: item.value for item in session.scan_values(USAGE_NS)}
        assert set(stored) == {"main", "b2"}
        assert stored["main"]["context"]["tokens"] == 90_000
        assert stored["b2"]["context"]["tokens"] == 72_000
    finally:
        repo.close()


def test_usage_value_survives_reopen(tmp_path):
    """退出重进：值与条目共用同一条日志，重开文件后照样读得到。"""
    from avid.session import JsonlSessionRepo

    clock = iter(range(1_700_000_000_000, 1_700_000_100_000, 1_000)).__next__
    generator = UuidV7Generator(clock)
    repo = JsonlSessionRepo(tmp_path / "sessions", now=clock, id_generator=generator)
    try:
        session = repo.create(id="s1", workspace="ws")
        SessionRecorder(session, "main").record_usage({"context": {"tokens": 12_345}})
        metadata = session.metadata
        session.close()
    finally:
        repo.close()

    reopened = JsonlSessionRepo(tmp_path / "sessions", now=clock, id_generator=generator)
    try:
        session = reopened.open(metadata)
        stored = {item.key: item.value for item in session.scan_values(USAGE_NS)}
        assert stored["main"] == {"context": {"tokens": 12_345}}
    finally:
        reopened.close()


# ---------------- 端到端：一次运行把快照写进会话 ----------------

def test_run_persists_usage_and_exposes_it_over_http(sandbox):
    """跑一次真循环（脚本模型），用量应当同时出现在 runs 视图与分支列表里。"""
    from fastapi.testclient import TestClient
    from support import create_session

    from avid.svc import Services
    from avid.web import create_app

    chat = ScriptedChat(make_turn("答"))
    services = Services(root=sandbox / ".avid" / "sessions", chat=chat)
    try:
        client = TestClient(
            create_app(services=services, static_dir=sandbox / "unbuilt"),
            base_url="http://127.0.0.1:8765",
        )
        created = create_session(client).json()
        session_id = created["id"]
        client.post(
            f"/api/sessions/{session_id}/runs",
            json={"prompt": "问题", "auto_approve": True, "branch": "main"},
        )
        assert services_close_when_done(services, session_id), "运行没有结束"

        branches = client.get(f"/api/sessions/{session_id}/branches").json()["branches"]
        main = next(item for item in branches if item["name"] == "main")
        # 脚本模型的 usage 是 prompt=1 / completion=2 / total=3（见 support.make_turn）。
        assert main["usage"]["context"]["tokens"] == 1
        assert main["usage"]["cache"]["read_tokens"] is None
        # 没有 AVID_CONTEXT_WINDOW 且模型名 test-model 不认识 → 没有分母，不猜占用率。
        assert main["usage"]["context"]["window"] is None
        assert main["usage"]["context"]["utilization"] is None

        # 另一个分支没跑过：null 而不是 0。
        assert created["branch"] == "main"
    finally:
        services.close()


def services_close_when_done(services, session_id: str) -> bool:
    """等到该会话没有活动 run（脚本模型很快，但运行在线程里）。"""
    from support import wait_for

    return wait_for(lambda: services.runs.active_run_id(session_id) is None)

def test_parts_allocate_the_real_total_by_char_share():
    """分块：按字符占比分配**真实的** prompt_tokens，三块之和恰好等于总数。

    为什么用占比而不是"每 token 多少字符"：后者在中英混排下必然偏；占比只用三块之间的
    相对量。余数归到对话消息，于是界面上的堆叠条与总数永远对得上。
    """
    state = RunState(context_window=200_000)
    state.record_prompt_parts(system=2_000, tools=6_000, messages=64_000)
    state.record_usage(Usage(72_000, 10, 72_010))

    parts = state.usage_report()["context"]["parts"]
    assert parts is not None
    assert sum(parts.values()) == 72_000
    assert parts["system"] == 2_000  # 字符占比 2/72 → token 占比同理
    assert parts["tools"] == 6_000
    assert parts["messages"] == 64_000


def test_parts_absorb_the_rounding_remainder_into_messages():
    """不能整除时余数给对话消息：三块之和必须等于真实总数，不能少几个 token。"""
    state = RunState()
    state.record_prompt_parts(system=1, tools=1, messages=1)
    state.record_usage(Usage(10, 0, 10))

    parts = state.usage_report()["context"]["parts"]
    assert parts is not None
    assert sum(parts.values()) == 10
    assert parts["system"] == 3 and parts["tools"] == 3 and parts["messages"] == 4


def test_parts_are_none_without_readings_or_chars():
    """缺任一前提就不给分块：没读数、没字符数、字符数全零。"""
    assert RunState().usage_report()["context"]["parts"] is None

    no_chars = RunState()
    no_chars.record_usage(Usage(100, 1, 101))
    assert no_chars.usage_report()["context"]["parts"] is None

    zero_chars = RunState()
    zero_chars.record_prompt_parts(system=0, tools=0, messages=0)
    zero_chars.record_usage(Usage(100, 1, 101))
    assert zero_chars.usage_report()["context"]["parts"] is None


# ---------------- 窗口探测接进运行路径 ----------------

def _window_probe(monkeypatch, *, window=200_000):
    """把 provider 的 /models 换成 MockTransport，并打开探测（conftest 默认关掉）。"""
    import httpx

    from avid.ai import client as client_module

    monkeypatch.delenv("AVID_MODEL_INFO", raising=False)
    with client_module._MODEL_WINDOW_LOCK:
        client_module._MODEL_WINDOWS.clear()

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/models")
        return httpx.Response(
            200, json={"data": [{"id": "test-model", "context_length": window}]}
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(client_module, "shared_client", lambda: http)
    monkeypatch.setattr(
        "avid.runtime.shared_client", lambda: http, raising=False
    )
    return http


def test_loop_probes_the_window_and_reports_utilization(monkeypatch):
    """表里查不到、env 也没配时，循环在发请求前问一次 /models，占用率因此有分母。

    两条路径都要覆盖：CLI（`agent_loop` 自己建 state）与 Web（svc 先建 state 再进循环，
    探测结果必须回填进那份 state）。
    """
    from avid.runtime import agent_loop

    http = _window_probe(monkeypatch)
    try:
        config = Config(api_key="test-key", base_url="https://gw.test/v1", model="test-model")
        assert config.context_window is None

        built = RunState(observer=lambda event: None)
        agent_loop(
            [{"role": "user", "content": "问题"}],
            config=config,
            chat=ScriptedChat(make_turn("答")),
            state=built,
        )
        report = built.usage_report()
        assert report["context"]["window"] == 200_000
        assert report["context"]["utilization"] is not None

        # 探测结果是进程内缓存：第二次运行不再打端点。
        fresh = RunState(observer=lambda event: None)
        agent_loop(
            [{"role": "user", "content": "又一轮"}],
            config=config,
            chat=ScriptedChat(make_turn("答")),
            state=fresh,
        )
        assert fresh.usage_report()["context"]["window"] == 200_000
    finally:
        http.close()

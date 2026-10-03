"""B1 / B2 / B3：事件序列与重放（无 UI 也能验收）。

F0 的价值不依赖前端：它把「工具是否开始过」「压缩是否发生过」「审批被谁拒绝」
变成可断言的事实。这里跑的是真的 ``svc`` 注册表 + 真的会话落盘，只把模型换成
脚本、把工具换成记录器。
"""

from __future__ import annotations

import threading
import time

import pytest
from support import (
    RecordingTools,
    ScriptedChat,
    collect,
    make_turn,
    new_session,
    tool_call,
    wait_for,
    wait_handle_released,
    wait_terminal,
)

from avid.agent.events import (
    APPROVAL_REQUESTED,
    ASSISTANT_DELTA,
    ASSISTANT_MESSAGE,
    DELTA_EVENT_TYPES,
    REASONING_DELTA,
    RESYNC,
    RUN_FAILED,
    RUN_FINISHED,
    RUN_STARTED,
    RUN_STATUS,
    TODO_REMINDER,
    TOOL_CALL_FINISHED,
    TOOL_CALL_STARTED,
    TOOL_RESULT_MESSAGE,
    USER_MESSAGE,
)
from avid.providers.client import LLMError
from avid.services import Services, runs
from avid.services.errors import RunNotFound
from avid.session import SessionStorageError, messages_for_branch
from avid.session.types import NOTICE_ENTRY


def build(root, chat, tools=None, **kwargs) -> Services:
    return Services(
        root=root / ".avid" / "sessions", chat=chat, tool_registry=tools, **kwargs
    )


def run_to_end(services: Services, prompt: str = "跑一下"):
    session_id = new_session(services)
    record = services.runs.start(session_id, prompt)
    assert wait_terminal(record), f"运行没结束：{record.status}"
    return record


def many_rounds(count: int = 4) -> ScriptedChat:
    turns = [
        make_turn("", [tool_call("read_file", '{"path": "f.txt"}', f"call_{i}")])
        for i in range(1, count + 1)
    ]
    return ScriptedChat(*turns, make_turn("完成"))


# ---------------- B1 ----------------


def test_event_sequence_is_ordered_and_durable_seq_is_monotonic(sandbox):
    tools = RecordingTools().registry("read_file")
    services = build(sandbox, many_rounds(2), tools)
    record = run_to_end(services)

    got = collect(services, record.run_id)
    types = [event.type for event in got]

    assert types[0] == RUN_STARTED
    assert types[-1] == RUN_FINISHED
    assert USER_MESSAGE in types
    assert APPROVAL_REQUESTED not in types  # 只读工具不需要审批
    assert types.count(TOOL_CALL_STARTED) == 2
    assert types.count(TOOL_CALL_FINISHED) == 2
    assert types.count(TOOL_RESULT_MESSAGE) == 2
    assert types.count(ASSISTANT_MESSAGE) == 3

    # durable 的 seq 严格递增、不重复、从 1 开始
    seqs = [event.seq for event in got if event.seq is not None]
    assert seqs == list(range(1, len(seqs) + 1))
    assert record.next_seq == seqs[-1] + 1

    # transient 不带 seq
    transient = [event for event in got if event.type == RUN_STATUS]
    assert transient
    assert all(event.seq is None for event in transient)

    # 同一 run 的事件都带同一个 run_id；消息事件都带 entry_id
    assert {event.run_id for event in got} == {record.run_id}
    for event in got:
        if event.type in (
            USER_MESSAGE,
            ASSISTANT_MESSAGE,
            TOOL_RESULT_MESSAGE,
        ):
            assert event.data["entry_id"]
            assert event.data["message"]["role"] in ("user", "assistant", "tool")

    # run_finished 是提示，权威事实是注册表状态 + 已提交条目
    assert record.status == "finished"
    assert record.text == "完成"
    assert services.sessions.get(record.session_id)["message_count"] == len(
        [e for e in got if e.type in (USER_MESSAGE, ASSISTANT_MESSAGE, TOOL_RESULT_MESSAGE)]
    )


def test_tool_events_carry_tool_call_id_and_no_full_content_on_the_wire(sandbox):
    tools = RecordingTools({"read_file": "内容" * 10}).registry("read_file")
    services = build(sandbox, ScriptedChat(make_turn("", [tool_call("read_file")]), make_turn("好")), tools)
    record = run_to_end(services)

    finished = [e for e in collect(services, record.run_id) if e.type == TOOL_CALL_FINISHED]
    assert len(finished) == 1
    assert finished[0].data["tool_call_id"] == "call_1"
    assert finished[0].data["truncated"] is False
    assert finished[0].data["duration_ms"] >= 0


# ---------------- B2 ----------------


def test_cursor_replay_is_exact(sandbox):
    tools = RecordingTools().registry("read_file")
    services = build(sandbox, many_rounds(4), tools)
    record = run_to_end(services)

    everything = collect(services, record.run_id)
    total = [event.seq for event in everything if event.seq is not None]
    assert len(total) >= 12, f"样本太小，补齐测不出东西：{total}"

    for after in (0, 5, 11):
        replayed = collect(services, record.run_id, after=after)
        seqs = [event.seq for event in replayed if event.seq is not None]
        assert seqs == [seq for seq in total if seq > after]
        assert len(seqs) == len(set(seqs))


# ---------------- B3 ----------------


def test_buffer_eviction_is_explicit_resync(sandbox):
    tools = RecordingTools().registry("read_file")
    services = build(sandbox, many_rounds(4), tools, buffer_size=2)
    record = run_to_end(services)

    got = collect(services, record.run_id, after=0)
    assert got, "订阅应当至少收到一条 resync"
    assert got[0].type == RESYNC
    assert got[0].data["reason"] == "buffer_evicted"
    assert got[0].seq is not None  # resync 自己是 durable 的


def test_fresh_cursor_within_buffer_does_not_resync(sandbox):
    tools = RecordingTools().registry("read_file")
    services = build(sandbox, ScriptedChat(make_turn("直接回答")), tools)
    record = run_to_end(services)

    got = collect(services, record.run_id, after=0)
    assert RESYNC not in [event.type for event in got]


# ---------------- B1（F3）：delta 通道 ----------------


def streaming_reply(pieces: tuple[str, ...], reply: str):
    """假的流式调用：按分片回调，最后返回一条与非流式同形的 Turn。"""

    def fake_stream(config, messages, *, on_delta=None, **kwargs):
        for piece in pieces:
            if on_delta is not None:
                on_delta(piece)
        return make_turn(reply)

    return fake_stream


def test_deltas_are_opt_in_and_carry_no_seq(sandbox, monkeypatch):
    """C13 的内核侧：同一批 delta，订阅了才投递；不参与游标补齐，也不改最终状态（I5）。

    delta 不重放（I15），所以只能在**流式过程中**观察它。用一道闸门把假模型卡在模型
    调用里，等两个订阅者（一个订阅、一个不订阅）都进入实时跟随后再放行。
    """
    entered = threading.Event()
    release = threading.Event()

    def fake_stream(config, messages, *, on_delta=None, **kwargs):
        entered.set()
        release.wait(timeout=5)
        on_delta("你")
        on_delta("好")
        return make_turn("你好")

    monkeypatch.setattr("avid.services.runs.stream_completion", fake_stream)
    services = Services(root=sandbox / ".avid" / "sessions")
    session_id = new_session(services)
    record = services.runs.start(session_id, "打个招呼")
    assert entered.wait(timeout=5), "运行没走到模型调用"

    def consume(*, deltas: bool):
        got: list = []
        attached = threading.Event()

        def pump():
            for event in services.runs.subscribe(record.run_id, deltas=deltas):
                if event is None:
                    continue
                got.append(event)
                attached.set()

        thread = threading.Thread(target=pump, daemon=True)
        thread.start()
        assert attached.wait(timeout=5), "订阅没拿到重放的事件"
        return got, thread

    with_deltas, watcher = consume(deltas=True)
    without_deltas, silent = consume(deltas=False)
    release.set()
    watcher.join(timeout=5)
    silent.join(timeout=5)

    deltas = [event for event in with_deltas if event.type == ASSISTANT_DELTA]
    assert [event.data["text"] for event in deltas] == ["你", "好"]
    assert all(event.seq is None for event in deltas), "delta 不参与游标补齐（I4）"
    assert ASSISTANT_DELTA not in [event.type for event in without_deltas]

    # durable 消息仍带**完整**内容：delta 全丢也不影响正确性（I5）。
    finals = [event for event in with_deltas if event.type == ASSISTANT_MESSAGE]
    assert finals[-1].data["message"]["content"] == "你好"

    # delta 不重放：跑完之后从 0 补齐也拿不到它（I15）。
    assert wait_terminal(record)
    replayed = collect(services, record.run_id, after=0, deltas=True)
    assert ASSISTANT_DELTA not in [event.type for event in replayed]


def test_many_deltas_do_not_evict_durable_events(sandbox, monkeypatch):
    """重放预算按 durable 计数：一次长回复的 delta 不该把 durable 挤出缓冲。

    否则 delta 的**多少**会左右 I5 的语义——流得久一点，重连的客户端就平白收到
    resync。这里 50 条 delta + 4 条 durable，缓冲上限压到 10：按总条数淘汰会丢掉
    durable，按 durable 计数则一条都不丢。
    """
    monkeypatch.setattr(
        "avid.services.runs.stream_completion",
        streaming_reply(tuple(f"片{index}" for index in range(50)), "".join(f"片{index}" for index in range(50))),
    )
    services = Services(root=sandbox / ".avid" / "sessions", buffer_size=10)
    session_id = new_session(services)
    record = services.runs.start(session_id, "长回复")
    assert wait_terminal(record)

    assert record.evicted_upto == 0, "delta 把 durable 挤出了重放缓冲"
    got = collect(services, record.run_id, after=0)
    assert RESYNC not in [event.type for event in got]


# ---------------- 会话句柄的并发（阶段 18 修掉的竞态） ----------------


def test_reading_while_a_run_starts_never_double_opens_the_session(sandbox):
    """读路径与运行路径会同时想开会话，而会话层只允许一个句柄。

    修之前：``start`` 先登记 ``_active`` 再在运行线程里 ``open``，窗口期内读取
    看到"有活动 run 但拿不到句柄"，就自己开——同一会话被开两次，运行刚起就
    failed（``会话已经打开``），或读取 500（``会话已关闭``）。修法是把
    「开句柄」放进每会话一把的句柄锁，并让 ``_active`` 与句柄同时可见。
    """
    services = build(sandbox, ScriptedChat(make_turn("答")))
    session_id = new_session(services)
    failures: list[str] = []
    stop = threading.Event()

    def reader() -> None:
        while not stop.is_set():
            try:
                services.sessions.get(session_id)
                services.sessions.list_sessions()
            except Exception as exc:  # 读路径任何异常都算失败
                failures.append(f"{type(exc).__name__}: {exc}")
                return

    readers = [threading.Thread(target=reader) for _ in range(3)]
    for thread in readers:
        thread.start()
    try:
        record = services.runs.start(session_id, "跑一下")
        assert wait_terminal(record), record.status
    finally:
        stop.set()
        for thread in readers:
            thread.join(timeout=5)

    assert failures == []
    assert record.status == "finished", record.status
    assert services.sessions.get(session_id)["message_count"] >= 2


# ---------------- 运行记录的取消与统计 ----------------


def test_reasoning_delta_is_a_delta_tier_event(sandbox):
    """A2：思维链增量走 delta 档——默认不投递、不落盘、不占重放预算。"""
    services = build(sandbox, ScriptedChat(make_turn("答")), buffer_size=8)
    record = services.runs.get(services.runs.start(new_session(services), "跑").run_id)

    services.runs.emit_delta(
        record, services.runs, "它在想", event_type=REASONING_DELTA
    )

    deltas = [event for event in record.events if event.seq is None]
    assert [event.type for event in deltas] == [REASONING_DELTA]
    assert deltas[0].data == {"text": "它在想"}
    assert REASONING_DELTA in DELTA_EVENT_TYPES


def test_delta_bookkeeping_keeps_the_buffer_consistent(sandbox):
    """delta 记账必须与"从头重算"完全一致。

    以前每个 delta 都全量扫一遍缓冲来数 durable（O(n²)，实测 8000 分片 1.05 s，
    而且跑在读模型 SSE 的线程里）。现在增量维护 `durable_index`——这条用例把
    增量结果与一次全量重算对齐，防止记账写错。
    """
    services = build(sandbox, ScriptedChat(make_turn("答")), buffer_size=8)
    record = services.runs.get(services.runs.start(new_session(services), "跑").run_id)

    for index in range(50):
        services.runs.emit(record, RUN_STATUS, round=index)  # transient
        services.runs.emit_delta(record, services.runs, f"t{index}")
        if index % 7 == 0:
            services.runs.emit(record, TOOL_RESULT_MESSAGE, entry_id=str(index))

    # 与全量重算对齐：耐久事件数不超上限、绝对下标自洽、evicted_upto 是被丢掉的最高 seq。
    durable = [event for event in record.events if event.seq is not None]
    assert len(durable) <= 8
    assert record.durable_index == [
        record.dropped + position
        for position, event in enumerate(record.events)
        if event.seq is not None
    ]
    assert record.absolute_index() == record.dropped + len(record.events)
    if record.evicted_upto:
        assert all(event.seq is None or event.seq > record.evicted_upto for event in record.events)


def test_finished_runs_and_session_locks_are_reclaimed(sandbox):
    """终态记录与句柄锁都要能被回收，而且不许动刚结束的记录。

    没有回收时，长驻的 `avid web` 会一直攒：每条记录带着最长 buffer_size 条
    durable 事件与期间的全部 delta，每个访问过的会话还留一把锁。
    """
    services = build(sandbox, ScriptedChat(make_turn("答")), max_runs=1)
    record = run_to_end(services)

    # 刚结束的记录还在保留窗口内：条数兜底不许动它（订阅者可能还在消费缓冲）。
    assert services.runs.get(record.run_id) is record
    services.runs._sweep()
    assert services.runs.get(record.run_id) is record, "刚结束的记录不该被兜底淘汰"
    assert services.runs._session_locks == {}, "运行结束后句柄锁应当已被摘掉"

    # 过了保留窗口：记录连同缓冲一起收掉，回查变成 404（前端按条目重建视图）。
    services.runs._retention_ms = 0
    record.finished_at = 0
    services.runs._sweep()
    with pytest.raises(RunNotFound):
        services.runs.get(record.run_id)
    assert record.events == [], "缓冲要一起释放"
    assert services.runs._session_locks == {}


# ---------------- 失败路径：五种 code 都要变成可观察的终态 ----------------


def _raising_chat(exc: Exception):
    def chat(config, messages, **kwargs):
        raise exc

    return chat


@pytest.mark.parametrize(
    "exc,code",
    [
        (LLMError("模型挂了"), "llm_error"),
        (RuntimeError("程序错误"), "internal"),
    ],
    ids=["llm_error", "internal"],
)
def test_chat_failures_become_a_terminal_event_with_the_right_code(sandbox, exc, code):
    """失败必须是可观察的终态：record 上有 code，事件流里有 run_failed。

    审查发现这五条失败路径**零测试**：唯一的"守护"是 test_web_boundaries 里一条
    grep 断言（只证明字面量存在），而前端按 code 分支的错误面完全没被验证。
    """
    services = build(sandbox, _raising_chat(exc))
    record = services.runs.start(new_session(services), "跑")

    assert wait_terminal(record), record.status
    assert record.status == "failed"
    assert record.error is not None
    assert record.error["code"] == code
    assert str(exc) in record.error["message"]

    frames = collect(services, record.run_id)
    failed = [event for event in frames if event.type == RUN_FAILED]
    assert failed, [event.type for event in frames]
    assert failed[-1].data["code"] == code
    assert RUN_FINISHED not in [event.type for event in frames]


def test_a_run_longer_than_the_old_cap_still_finishes(sandbox):
    """内核里没有轮数上限：8 轮工具调用（比旧上限多）照常跑到模型自己收尾。

    回归用例：旧代码写死 8 轮，这条运行会被判成 `run_failed{round_limit}`。
    """
    tools = RecordingTools().registry("read_file")
    services = build(sandbox, many_rounds(8), tools)
    record = services.runs.start(new_session(services), "跑")

    assert wait_terminal(record), record.status
    assert record.status == "finished"
    assert record.text == "完成"


def test_a_run_of_forty_rounds_still_finishes(sandbox):
    """再往上也没有截止点：40 轮的工具往返仍然是"跑完"，不是"预算耗尽"。"""
    tools = RecordingTools().registry("read_file")
    services = build(sandbox, many_rounds(40), tools)
    record = services.runs.start(new_session(services), "跑")

    assert wait_terminal(record), record.status
    assert record.status == "finished"
    assert record.error is None


def test_config_error_failure_is_mapped(sandbox, monkeypatch):
    """模型配置缺失发生在运行线程里，必须是 config_error 而不是让线程裸死。"""
    # BYOK 配置指向不存在的文件：resolve_chat 报「还没有模型配置」
    monkeypatch.setenv("AVID_BYOK_CONFIG", str(sandbox / "none" / "models.json"))
    services = build(sandbox, ScriptedChat(make_turn("答")))
    record = services.runs.start(new_session(services), "跑")

    assert wait_terminal(record), record.status
    assert record.error["code"] == "config_error"


def test_session_error_failure_is_mapped(sandbox, monkeypatch):
    """会话层失败（读历史时文件坏了）映射成 session_error。"""

    def boom(session, branch):
        raise SessionStorageError("会话文件坏了")

    monkeypatch.setattr(runs, "messages_for_branch", boom)
    services = build(sandbox, ScriptedChat(make_turn("答")))
    record = services.runs.start(new_session(services), "跑")

    assert wait_terminal(record), record.status
    assert record.error["code"] == "session_error"


# ---------------- 缓冲边界：跟随期缺口与事件总数上限 ----------------


class BlockingChat:
    """卡住第一轮模型调用，好让测试在"运行还在进行"时操作缓冲。"""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self, config, messages, **kwargs):
        self.entered.set()
        assert self.release.wait(5), "测试自己没放行"
        return make_turn("答")


def test_live_follower_is_told_when_its_cursor_is_evicted(sandbox):
    """订阅时检查一次不够：慢消费者还在跟的时候缓冲翻页，同样要显式 resync（I5）。

    以前跟随循环用 ``max(0, index - dropped)`` 把越界下标夹到队首，于是缺口被
    静默跳过——客户端丢了一段历史却收不到任何提示。
    """
    chat = BlockingChat()
    services = build(sandbox, chat, buffer_size=2)
    record = services.runs.start(new_session(services), "跑")
    assert chat.entered.wait(5)

    got: list[str] = []
    consumed = threading.Event()

    def consume() -> None:
        # 慢消费者：每次 next() 之间停一下，模拟"跟不上的客户端"。
        for event in services.runs.subscribe(record.run_id, after=0):
            if event is not None:
                got.append(event.type)
                consumed.set()
            time.sleep(0.05)

    thread = threading.Thread(target=consume, daemon=True)
    thread.start()
    # 先让订阅者把已有事件消费完并进入跟随（此刻还没有任何淘汰）。
    assert consumed.wait(5), "订阅者没开始消费"
    assert record.evicted_upto == 0, "这一步不该有缺口，否则测不到跟随期路径"

    # 订阅者正在 sleep，此刻灌入远超 buffer_size 的 durable：它的游标会被淘汰。
    for index in range(6):
        services.runs.emit(
            record,
            TOOL_CALL_STARTED,
            tool="bash",
            arguments={},
            round=1,
            tool_call_id=f"c{index}",
        )

    assert wait_for(lambda: RESYNC in got, 5), got
    chat.release.set()
    assert wait_terminal(record)
    thread.join(timeout=5)


def test_delta_flood_cannot_grow_the_buffer_without_bound(sandbox):
    """一次长回复的 delta 也要有上限：每条 delta 都是一个 RunEvent。

    上限淘汰最旧的前缀；被连带丢掉的 durable 必须记进 ``evicted_upto``，
    否则重连的客户端会看到静默缺口。
    """
    chat = BlockingChat()
    services = build(sandbox, chat, buffer_size=16, max_events=64)
    record = services.runs.start(new_session(services), "跑")
    assert chat.entered.wait(5)

    for index in range(400):
        services.runs.emit(
            record,
            TOOL_CALL_STARTED,
            tool="bash",
            arguments={},
            round=1,
            tool_call_id=f"c{index}",
        )
        services.runs.emit_delta(record, services.runs, "x")

    assert len(record.events) <= 64, len(record.events)
    assert record.evicted_upto > 0, "被丢掉的 durable 必须记账，否则是静默缺口"
    assert record.durable_index == [
        record.dropped + position
        for position, event in enumerate(record.events)
        if event.seq is not None
    ]
    assert record.absolute_index() == record.dropped + len(record.events)

    chat.release.set()
    assert wait_terminal(record)


def test_cancel_arriving_before_the_state_exists_is_not_lost(sandbox, monkeypatch):
    """取消在 ``record.state`` 装上之前到达时必须补一次，否则永久丢失。

    ``cancel()`` 只在 state 已就绪时调 ``state.cancel()``，而循环检查的是
    ``state.cancelled``。线程启动到 ``RunState.for_run`` 之间有一段真实工作
    （装配记录器、读历史），此间的取消以前会静默丢掉：202 已返回、取消标志
    为真，运行却照跑完。
    """
    entered = threading.Event()
    release = threading.Event()
    real = runs.messages_for_branch

    def slow(session, branch):
        entered.set()
        assert release.wait(5), "测试自己没放行"
        return real(session, branch)

    monkeypatch.setattr(runs, "messages_for_branch", slow)

    services = build(sandbox, ScriptedChat(make_turn("答")))
    session_id = new_session(services)
    record = services.runs.start(session_id, "跑一下")
    assert entered.wait(5), "运行线程没走到读历史那一步"

    services.runs.cancel(record.run_id)  # 此刻 record.state 还是 None
    release.set()

    assert wait_terminal(record), record.status
    assert record.status == "cancelled", record.status
    assert record.cancel_reason == "user"


def test_run_record_reports_the_real_round_and_tokens(sandbox):
    """``GET /runs/{id}`` 的 round/tokens 曾经恒为 0：权威在 state，读数在 RunRecord。

    ``many_rounds(2)`` 是"两轮工具 + 一轮收尾"，所以 round 到 3；tokens 每轮累加。
    """
    tools = RecordingTools().registry("read_file")
    services = build(sandbox, many_rounds(2), tools)
    session_id = new_session(services)
    record = services.runs.start(session_id, "跑一下")
    assert wait_terminal(record), record.status

    payload = services.runs.get(record.run_id).to_dict()
    assert payload["round"] >= 3, payload
    assert payload["tokens"] > 0, payload


# ---------------- 注入提醒的条目类型 ----------------


def test_plan_travels_in_the_tail_not_in_the_history(sandbox):
    """计划不再以 user 提醒注入：它走每轮重渲染的 tail 块，不落库、不发事件。

    旧机制把「[提醒] 连续 N 轮……」当 user 消息塞进对话并落库，渲染侧只能靠 notice
    类型把它和用户输入分开；tail 不进消息通道，这条约束整个消失。
    """
    tools = RecordingTools().registry("read_file")
    services = build(sandbox, many_rounds(4), tools)
    record = run_to_end(services)

    entries = services.sessions.entries(record.session_id, order="asc")["entries"]
    assert [entry for entry in entries if entry["type"] == NOTICE_ENTRY] == []

    kinds = [event.type for event in collect(services, record.run_id)]
    assert kinds.count(TODO_REMINDER) == 0
    assert kinds.count(USER_MESSAGE) == 1

    # 投影不变：落库的历史里没有内核写的提醒文本。
    #
    # 直读会话文件要先等运行线程交还句柄，再持句柄锁打开：`record.terminal` 说的是
    # "注册表已定终态"，句柄是紧接着才交还的（见 `support.wait_handle_released`）。
    wait_handle_released(services, record.session_id)
    with services.runs.session_lock(record.session_id):
        session = services.repo.open(services.runs.find_metadata(record.session_id))
        try:
            history = [
                str(message.get("content") or "")
                for message in messages_for_branch(session)
            ]
        finally:
            session.close()
    assert not any("[提醒]" in text for text in history)


# ---------------- 阶段 22：用量快照 ----------------

#: 脚本模型的用量（`support.make_turn`）：prompt=1 / completion=2 / total=3。
SCRIPT_USAGE = {"context": {"tokens": 1, "window": None, "utilization": None}}


def test_run_status_carries_the_usage_snapshot_every_round(sandbox):
    """每轮模型调用后都有一份统一 schema 的快照——界面据此实时显示占用与命中。"""
    tools = RecordingTools().registry("read_file")
    services = build(
        sandbox,
        ScriptedChat(make_turn("", [tool_call("read_file")]), make_turn("好")),
        tools,
    )
    record = run_to_end(services)

    snapshots = [
        event.data["usage"]
        for event in collect(services, record.run_id)
        if event.type == RUN_STATUS and "usage" in event.data
    ]
    assert len(snapshots) == 2  # 两轮，两份
    context = snapshots[-1]["context"]
    assert context["tokens"] == SCRIPT_USAGE["context"]["tokens"]
    assert context["window"] is None
    # 分块：三块之和 = 真实总数（系统提示与工具定义各占一部分，对话消息拿走余数）
    parts = context["parts"]
    assert parts is not None
    assert sum(parts.values()) == context["tokens"]
    # 测试模型不在内置窗口表里 → 没有分母，也不猜占用率。
    assert snapshots[-1]["context"]["window"] is None
    # 脚本模型没上报缓存计数：None（「—」），不是 0。
    assert snapshots[-1]["cache"] == {
        "read_tokens": None,
        "write_tokens": None,
        "hit_ratio": None,
    }
    assert snapshots[-1]["compaction"]["count"] == 0


def test_terminal_event_and_rest_view_share_the_final_snapshot(sandbox):
    """run_finished 带最终快照（durable，流里就拿到），REST 视图同源。"""
    tools = RecordingTools().registry("read_file")
    services = build(
        sandbox,
        ScriptedChat(make_turn("", [tool_call("read_file")]), make_turn("好")),
        tools,
    )
    record = run_to_end(services)
    events_list = collect(services, record.run_id)

    finished = next(e for e in events_list if e.type == RUN_FINISHED)
    assert finished.data["usage"] == record.usage
    # 出口原因（阶段 40）：每个出口都叫得出名字（StopReason）
    assert finished.data["reason"] == "final_text"
    assert record.usage["context"]["tokens"] == 1
    # 累计量仍是运行账单（两轮 × total 3），与"占用"不是一回事。
    assert record.tokens == 6
    assert services.runs.get(record.run_id).to_dict()["usage"] == record.usage


def test_usage_is_persisted_into_the_session_for_the_branch(sandbox):
    """落盘：分支列表带该分支最近一次运行的快照（刷新与重启后靠它）。"""
    from avid.session import USAGE_NS, branch_usage

    tools = RecordingTools().registry("read_file")
    services = build(sandbox, ScriptedChat(make_turn("答")), tools)
    record = run_to_end(services)

    listed = services.sessions.list_branches(record.session_id)["branches"]
    main = next(item for item in listed if item["name"] == "main")
    assert main["usage"]["context"]["tokens"] == 1

    # 直读会话文件：先等句柄交还，再持锁打开（与上面那条同理）。
    wait_handle_released(services, record.session_id)
    with services.runs.session_lock(record.session_id):
        session = services.repo.open(services.runs.find_metadata(record.session_id))
        try:
            stored = session.scan_values(USAGE_NS)
        finally:
            session.close()
    assert [item.key for item in stored] == ["main"]
    assert stored[0].value == main["usage"]
    # 地址构造器与写入侧用的是同一个（namespace 与 key 都得对得上）。
    assert branch_usage("main").key == "main"


def test_terminal_flag_and_terminal_event_land_together(sandbox):
    """`record.terminal` 为真 ⇒ 终态事件**已经在缓冲里**。

    订阅者就是按这条判断"还有没有后续事件"的（`record.terminal` 且缓冲没有新事件
    就直接返回）。两者分开写——先置状态、中间再干别的活（阶段 22 起中间有一次
    会话写入）——订阅者会在那段时间里看到"已终态 + 缓冲里没有终态事件"，于是
    静默少收一条终态。所以这里**什么也不等**：终态刚置位就立刻订阅。
    """
    tools = RecordingTools().registry("read_file")
    services = build(sandbox, many_rounds(2), tools)
    record = run_to_end(services)
    assert record.terminal

    got = collect(services, record.run_id)
    assert got, "订阅一条事件都没收到"
    assert got[-1].type == RUN_FINISHED


def test_loop_records_the_three_prompt_parts(sandbox):
    """分块真的被记下来：系统提示词与工具定义从不发给前端，只有内核在发请求前算得到。

    脚本模型的 usage 是 1 个 token（`support.make_turn`），整数分配下三块里只有一个能
    拿到 1——所以这里显式给一份**大**用量，才看得出三块都有份额。
    """
    from avid.providers.client import Turn, Usage

    turn = Turn(
        message={"role": "assistant", "content": "好"},
        text="好",
        tool_calls=[],
        usage=Usage(prompt_tokens=1_000, completion_tokens=5, total_tokens=1_005),
        model="m",
        finish_reason="stop",
    )
    tools = RecordingTools().registry("read_file")
    services = build(sandbox, ScriptedChat(turn), tools)
    record = run_to_end(services)

    snapshots = [
        event.data["usage"]["context"]
        for event in collect(services, record.run_id)
        if event.type == RUN_STATUS and "usage" in event.data
    ]
    parts = snapshots[-1]["parts"]
    assert parts is not None
    assert sum(parts.values()) == 1_000
    assert parts["system"] > 0, parts   # 系统提示词
    assert parts["tools"] > 0, parts    # 工具定义（15 个工具的 JSON）
    assert parts["messages"] > 0, parts  # 对话消息（余数在这里）

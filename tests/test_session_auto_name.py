"""会话标题：新会话按用户第一条消息自动命名（服务层，不走 HTTP）。

失败清单（逐条对应用例）：① 不命名（名字空着）；② 命名了但被第二、第三条消息改写；③ 覆盖用户
自己起的名字；④ 续接旧会话时被改名；⑤ 命名失败把整轮 run 带崩；⑥ 只发图没有文字时名字空着；
⑦ 名字没进索引（列表/搜索里还是旧的）。
"""

from __future__ import annotations

from pathlib import Path

from support import ScriptedChat, bound_workspace, make_turn, wait_terminal

from avid.services import Services
from avid.services.runs import TITLE_CHARS, session_title


def build(root, chat) -> Services:
    return Services(root=root / ".avid" / "sessions", chat=chat)


def run(services: Services, session_id: str, prompt: str = "", **kwargs):
    record = services.runs.start(session_id, prompt, **kwargs)
    assert wait_terminal(record), f"运行没结束：{record.status}"
    return record


def name_of(services: Services, session_id: str) -> str | None:
    return services.sessions.get(session_id)["name"]


def test_first_user_message_becomes_the_title(sandbox):
    services = build(sandbox, ScriptedChat(make_turn("好")))
    workspace = bound_workspace(services)
    session_id = services.sessions.create(workspace=workspace)["id"]

    run(services, session_id, "读一下 pyproject.toml，用一句话告诉我项目名和 Python 版本要求")

    title = name_of(services, session_id)
    assert title is not None
    assert title.startswith("读一下 pyproject.toml")
    assert title.endswith("…")
    assert len(title) == TITLE_CHARS + 1


def test_the_title_is_one_line_and_whitespace_collapsed(sandbox):
    services = build(sandbox, ScriptedChat(make_turn("好")))
    workspace = bound_workspace(services)
    session_id = services.sessions.create(workspace=workspace)["id"]

    run(services, session_id, "第一行\n\n   第二行  第三个")

    assert name_of(services, session_id) == "第一行 第二行 第三个"


def test_later_messages_do_not_rename(sandbox):
    services = build(sandbox, ScriptedChat(make_turn("好"), make_turn("嗯")))
    workspace = bound_workspace(services)
    session_id = services.sessions.create(workspace=workspace)["id"]

    run(services, session_id, "第一个问题")
    run(services, session_id, "第二个问题")

    assert name_of(services, session_id) == "第一个问题"


def test_an_explicit_name_is_never_overwritten(sandbox):
    services = build(sandbox, ScriptedChat(make_turn("好")))
    workspace = bound_workspace(services)
    session_id = services.sessions.create(workspace=workspace, name="我自己起的")["id"]

    run(services, session_id, "另一个问题")

    assert name_of(services, session_id) == "我自己起的"


def test_an_image_only_message_still_gets_a_title(sandbox):
    services = build(sandbox, ScriptedChat(make_turn("好")))
    workspace = bound_workspace(services)
    session_id = services.sessions.create(workspace=workspace)["id"]
    image = {"type": "image", "mime": "image/png", "data": "aGk="}

    run(services, session_id, content=[image])

    assert name_of(services, session_id) == "（图片）"


def test_a_blank_message_leaves_the_session_unnamed(sandbox):
    services = build(sandbox, ScriptedChat(make_turn("好")))
    workspace = bound_workspace(services)
    session_id = services.sessions.create(workspace=workspace)["id"]

    run(services, session_id, "   \n  ")

    assert name_of(services, session_id) is None


def test_the_name_lands_in_the_session_file(sandbox):
    """名字是会话值，落库后重开也该读得回来（索引侧的通知走既有 notify_index）。"""
    services = build(sandbox, ScriptedChat(make_turn("好")))
    workspace = bound_workspace(services)
    session_id = services.sessions.create(workspace=workspace)["id"]

    run(services, session_id, "这个名字要落盘")

    raw = "".join(p.read_text(encoding="utf-8") for p in Path(services.root).rglob("*.jsonl"))
    assert "avid.session.name" in raw and "这个名字要落盘" in raw


def test_session_title_pure_helper():
    assert session_title("  a  b ") == "a b"
    assert session_title("x" * 40) == "x" * TITLE_CHARS + "…"
    assert session_title([{"type": "text", "text": "看图"}]) == "看图"
    assert session_title([]) is None
    assert session_title([{"type": "image"}]) == "（图片）"
    assert session_title(12345) is None

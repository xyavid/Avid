"""Inbox 的用例：两种 mode、原子领取、降级、撤销、幂等。

先于实现编写（§6）。这一层是纯内存表，所以用例盯的是**边界**而不是流程：
谁能在什么时候拿走哪一条、拿不走时它去哪、以及「被接受的输入永不消失」这条不变量。
"""

from __future__ import annotations

import pytest

from avid.services.inbox import SessionInbox


def add(inbox, content="看一眼", mode="after", client_id=None, **params):
    """投一条输入；params 是它起 run 时要用的开关。"""
    return inbox.add(mode=mode, content=content, params=params, client_id=client_id)


def test_a_new_inbox_is_empty():
    inbox = SessionInbox("s-1")

    assert inbox.pending() == []


def test_an_accepted_input_is_visible_with_its_id_and_time():
    inbox = SessionInbox("s-1")
    added = add(inbox, "改一下", mode="after", client_id="c-1")

    assert added.input_id.startswith("in_")
    assert added.created_at > 0
    assert [entry.input_id for entry in inbox.pending()] == [added.input_id]
    assert inbox.pending()[0].content == "改一下"


def test_steers_are_taken_in_submission_order():
    inbox = SessionInbox("s-1")
    first = add(inbox, "先这个", mode="now")
    second = add(inbox, "再这个", mode="now")
    add(inbox, "排队的", mode="after")

    taken = inbox.take_steers()

    assert [entry.input_id for entry in taken] == [first.input_id, second.input_id]
    # 领取即离队：队里只剩那条排队的（已领走的东西留在会话条目里，不留在队里）
    assert [entry.content for entry in inbox.pending()] == ["排队的"]


def test_taking_steers_leaves_queued_items_alone():
    inbox = SessionInbox("s-1")
    add(inbox, "插入的", mode="now")
    queued = add(inbox, "排队的", mode="after")

    inbox.take_steers()

    assert [entry.input_id for entry in inbox.pending()] == [queued.input_id]


def test_a_queued_item_is_claimed_atomically():
    inbox = SessionInbox("s-1")
    queued = add(inbox, "下一件事", mode="after")

    assert inbox.take_for_run(queued.input_id) is not None
    # 第二个标签页同抢：拿不到，也不会拿到半条
    assert inbox.take_for_run(queued.input_id) is None
    assert inbox.pending() == []


def test_take_for_run_also_works_for_a_steer_that_found_no_run():
    """空闲时投的 now：客户端拿它去起一个 run（语义是「最早可能被处理的时刻」）。"""
    inbox = SessionInbox("s-1")
    now = add(inbox, "现在就做", mode="now")

    assert inbox.take_for_run(now.input_id) is not None


def test_downgrade_turns_unclaimed_steers_into_the_next_turn():
    """run 结束前没赶上的插入不报「未送达」，而是降级为排队项——不变量是不消失。"""
    inbox = SessionInbox("s-1")
    kept = add(inbox, "本来就排队", mode="after")
    delivered = add(inbox, "已经领走", mode="now")
    assert [entry.input_id for entry in inbox.take_steers()] == [delivered.input_id]
    missed = add(inbox, "边界之后才投的", mode="now")

    downgraded = inbox.downgrade_steers()

    assert [entry.input_id for entry in downgraded] == [missed.input_id]
    assert downgraded[0].mode == "after"
    assert downgraded[0].missed is True
    # 已在队里的排队项保持原样（不重复标记）
    kept_entry = next(entry for entry in inbox.pending() if entry.input_id == kept.input_id)
    assert kept_entry.missed is False
    # 已领走的那条不在降级名单里，也不在队里
    assert all(entry.input_id != delivered.input_id for entry in inbox.pending())


def test_removing_only_touches_an_unclaimed_item():
    inbox = SessionInbox("s-1")
    queued = add(inbox, "算了", mode="after")
    taken = add(inbox, "领走了", mode="now")
    inbox.take_steers()

    assert inbox.remove(queued.input_id) is True
    assert inbox.remove(queued.input_id) is False  # 第二次没有可撤销的东西
    assert inbox.remove(taken.input_id) is False  # 已经领取的不归撤销管


def test_the_same_client_id_is_not_accepted_twice():
    """前端因超时重发同一条：返回同一条，不产生第二条。"""
    inbox = SessionInbox("s-1")
    first = add(inbox, "发一次", client_id="c-9")
    again = add(inbox, "发一次", client_id="c-9")

    assert again.input_id == first.input_id
    assert len(inbox.pending()) == 1


def test_client_ids_are_scoped_to_the_inbox_they_were_accepted_in():
    first = SessionInbox("s-1")
    second = SessionInbox("s-2")
    one = add(first, "A", client_id="c-1")
    two = add(second, "B", client_id="c-1")

    assert one.input_id != two.input_id


def test_ids_are_unique_across_many_items():
    inbox = SessionInbox("s-1")
    ids = {add(inbox, f"{index}", mode="after").input_id for index in range(50)}

    assert len(ids) == 50


def test_pending_snapshot_is_a_copy():
    inbox = SessionInbox("s-1")
    add(inbox, "一条", mode="after")

    snapshot = inbox.pending()
    snapshot.clear()

    assert len(inbox.pending()) == 1


@pytest.mark.parametrize("mode", ["now", "after", "later", ""])
def test_only_the_two_documented_modes_are_accepted(mode):
    inbox = SessionInbox("s-1")
    if mode in ("now", "after"):
        assert add(inbox, "x", mode=mode).mode == mode
    else:
        with pytest.raises(ValueError):
            add(inbox, "x", mode=mode)


def test_a_failed_claim_can_be_put_back_at_the_head():
    """起 run 失败时把刚领走的那条放回队首：输入不消失，顺序也不乱。"""
    inbox = SessionInbox("s-1")
    first = add(inbox, "先来", mode="after")
    second = add(inbox, "后来", mode="after")

    claimed = inbox.take_for_run(second.input_id)
    assert claimed is not None
    inbox.restore(claimed)

    assert [entry.input_id for entry in inbox.pending()] == [second.input_id, first.input_id]
    # 已经在队里的不会被插出重复
    inbox.restore(claimed)
    assert len(inbox.pending()) == 2

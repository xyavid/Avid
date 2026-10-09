"""SessionInbox tests: two modes, atomic claims, downgrade, removal, and idempotency.

The invariant under test is that an accepted input never disappears: who may take which entry, when, and where an unclaimed one goes.
"""

from __future__ import annotations

import pytest

from avid.services.inbox import SessionInbox


def add(inbox, content="看一眼", mode="after", client_id=None, **params):
    """Submit one input; params are the switches to use when it starts a run."""
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
    # Claiming removes from the queue: only the parked item stays (claimed ones live on as session entries)
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
    # A second tab racing for it gets nothing, not a half-claimed entry
    assert inbox.take_for_run(queued.input_id) is None
    assert inbox.pending() == []


def test_take_for_run_also_works_for_a_steer_that_found_no_run():
    """A now posted while idle is claimed by the client to start a run."""
    inbox = SessionInbox("s-1")
    now = add(inbox, "现在就做", mode="now")

    assert inbox.take_for_run(now.input_id) is not None


def test_downgrade_turns_unclaimed_steers_into_the_next_turn():
    """A steer that missed the run is downgraded to a queued item, not reported undelivered — accepted input never disappears."""
    inbox = SessionInbox("s-1")
    kept = add(inbox, "本来就排队", mode="after")
    delivered = add(inbox, "已经领走", mode="now")
    assert [entry.input_id for entry in inbox.take_steers()] == [delivered.input_id]
    missed = add(inbox, "边界之后才投的", mode="now")

    downgraded = inbox.downgrade_steers()

    assert [entry.input_id for entry in downgraded] == [missed.input_id]
    assert downgraded[0].mode == "after"
    assert downgraded[0].missed is True
    # Already-queued items are left alone (not flagged twice)
    kept_entry = next(entry for entry in inbox.pending() if entry.input_id == kept.input_id)
    assert kept_entry.missed is False
    # The claimed entry is neither downgraded nor back in the queue
    assert all(entry.input_id != delivered.input_id for entry in inbox.pending())


def test_removing_only_touches_an_unclaimed_item():
    inbox = SessionInbox("s-1")
    queued = add(inbox, "算了", mode="after")
    taken = add(inbox, "领走了", mode="now")
    inbox.take_steers()

    assert inbox.remove(queued.input_id) is True
    assert inbox.remove(queued.input_id) is False  # nothing left to remove on the second call
    assert inbox.remove(taken.input_id) is False  # claimed items are out of removal's reach


def test_the_same_client_id_is_not_accepted_twice():
    """A client resending the same id after a timeout gets the same entry, not a second one."""
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
    """A failed run start puts the claimed item back at the head: nothing is lost and the order holds."""
    inbox = SessionInbox("s-1")
    first = add(inbox, "先来", mode="after")
    second = add(inbox, "后来", mode="after")

    claimed = inbox.take_for_run(second.input_id)
    assert claimed is not None
    inbox.restore(claimed)

    assert [entry.input_id for entry in inbox.pending()] == [second.input_id, first.input_id]
    # Restoring an item already queued does not duplicate it
    inbox.restore(claimed)
    assert len(inbox.pending()) == 2

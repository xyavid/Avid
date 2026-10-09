"""The facade list: ``avid.session.__all__`` is exactly ``EXPECTED``, so adding a name is a public
interface change, while internals stay importable from their submodules.
"""

from __future__ import annotations

import avid.session as session

EXPECTED = {
    # errors
    "SessionError",
    "SessionNotFoundError",
    "SessionExistsError",
    "SessionAlreadyOpenError",
    "SessionClosedError",
    "SessionBusyError",
    "SessionLockedError",
    "SessionInvariantError",
    "SessionStorageError",
    "SessionInvalidIdError",
    "SessionInvalidBranchError",
    "SessionBranchExistsError",
    "SessionUnknownTargetError",
    "SessionInvalidMessageError",
    # data plane
    "Entry",
    "NewEntry",
    "EntryWrite",
    "CommittedEntry",
    "CommittedValueSet",
    "EntryQuery",
    "BranchScan",
    "SessionStats",
    "SessionMetadata",
    "JsonlSessionMetadata",
    "STORAGE_VERSION",
    # values
    "DEFAULT_BRANCH",
    "USAGE_NS",
    "COMPACTION_NS",
    "SCRATCH_NS",
    "session_scratch",
    "branch_tip",
    "branch_usage",
    "branch_compaction",
    "entry_label",
    "session_name",
    "set_value",
    "value",
    # repositories
    "MemorySessionRepo",
    "JsonlSessionRepo",
    # writing and projection
    "SessionRecorder",
    "MutationLine",
    "messages_for_branch",
    "entries_to_messages",
    "repair_incomplete_batches",
    # validation and ids
    "validate_message",
    "validate_session_id",
    "UuidV7Generator",
    "new_uuidv7",
    "now_ms",
}

INTERNAL = (
    "JsonlStorage",
    "MemoryStorage",
    "JsonlHeader",
    "PreparedCommit",
    "CommittedWrite",
    "CommittedValueDelete",
    "ValueSetWrite",
    "ValueDeleteWrite",
    "ValueAddress",
    "StoredValue",
    "StorageBackedSession",
    "SessionBranch",
    "SessionMutation",
    "Branch",
    "Storage",
    "Session",
    "SessionRepo",
    "IdGenerator",
    "CommitResult",
    "EntryType",
    "MESSAGE_ENTRY",
)


def test_the_facade_is_exactly_this_list():
    assert set(session.__all__) == EXPECTED
    assert len(session.__all__) == len(set(session.__all__)), "清单里没有重复名"
    for name in session.__all__:
        assert hasattr(session, name), f"{name} 在清单里但没被导入"


def test_storage_internals_are_not_advertised_but_still_importable():
    """Internals stay out of ``__all__`` but remain importable from their submodules."""
    for name in INTERNAL:
        assert name not in session.__all__, f"{name} 不该出现在门面上"

    from avid.session.jsonl import JsonlStorage
    from avid.session.session import StorageBackedSession
    from avid.session.values import ValueAddress

    assert JsonlStorage is not None
    assert StorageBackedSession is not None
    assert ValueAddress is not None

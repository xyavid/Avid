"""门面清单（P2-17）：`avid.session` 对外就这一份名字。

为什么要一条测试钉住一份清单：门面是"哪些名字算公开接口"的唯一答案，而它以前是
68 个名字的堆（26 个在 `src/` 与 `tests/` 里从没被用过）。清单写在这里，加名字就是
一次需要被看见的公开接口变更；改内部实现不再看起来像改接口。

名字仍然可以从子模块导入（`from avid.session.jsonl import JsonlStorage`），只是不放
在门面上。
"""

from __future__ import annotations

import avid.session as session

EXPECTED = {
    # 错误
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
    # 数据面
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
    # 值
    "DEFAULT_BRANCH",
    "branch_tip",
    "entry_label",
    "session_name",
    "set_value",
    "value",
    # 仓库
    "MemorySessionRepo",
    "JsonlSessionRepo",
    # 写入与投影
    "SessionRecorder",
    "MutationLine",
    "messages_for_branch",
    "entries_to_messages",
    "repair_incomplete_batches",
    # 校验与 id
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
    """内部件不进 `__all__`，但仍可从子模块导入——门面收窄不是删功能。"""
    for name in INTERNAL:
        assert name not in session.__all__, f"{name} 不该出现在门面上"

    from avid.session.jsonl import JsonlStorage
    from avid.session.session import StorageBackedSession
    from avid.session.values import ValueAddress

    assert JsonlStorage is not None
    assert StorageBackedSession is not None
    assert ValueAddress is not None

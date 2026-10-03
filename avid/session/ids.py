"""Session ids and the clock: time-ordered UUIDv7-shaped ids from the standard library, plus integer-millisecond time."""

from __future__ import annotations

import secrets
import time
import uuid

from .errors import SessionInvalidIdError

MAX_ID_LENGTH = 200


def now_ms() -> int:
    """Current wall-clock time in integer milliseconds since the epoch."""
    return int(time.time() * 1000)


def validate_session_id(session_id: object) -> str:
    """Whether a session id can safely name a file; both backends share this one check."""
    if not isinstance(session_id, str) or not session_id.strip():
        raise SessionInvalidIdError(session_id, "必须是非空字符串")
    if len(session_id) > MAX_ID_LENGTH:
        raise SessionInvalidIdError(session_id, f"长度不能超过 {MAX_ID_LENGTH}")
    for char in ("/", "\\", "\u0000"):
        if char in session_id:
            raise SessionInvalidIdError(session_id, f"不能含 {char!r}")
    return session_id


def new_uuidv7(timestamp_ms: int) -> str:
    """Assemble a UUIDv7 string in the RFC 9562 layout, with the millisecond timestamp in the high 48 bits."""
    random_bits = secrets.token_bytes(10)
    # Field packing: 48-bit timestamp, 4-bit version, 12 rand_a bits, 2-bit variant, 62 rand_b bits.
    value = (timestamp_ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= int.from_bytes(random_bits[:2], "big") & 0x0FFF
    value |= 0b10 << 62
    value |= int.from_bytes(random_bits[2:], "big") & ((1 << 62) - 1)
    return str(uuid.UUID(int=value))


class UuidV7Generator:
    """Default id generator; ``now`` returns integer milliseconds and is injectable for deterministic tests."""

    def __init__(self, now=now_ms) -> None:
        self._now = now

    def next(self) -> str:
        return new_uuidv7(self._now())

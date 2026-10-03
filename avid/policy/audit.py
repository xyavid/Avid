"""Append-only JSONL audit trail recording which rule, target and verdict applied to each action."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .userdirs import avid_home

logger = logging.getLogger("avid.policy.audit")

AUDIT_DIR_ENV = "AVID_AUDIT_DIR"
AUDIT_FILE_PREFIX = "audit-"

# Only the credential value is masked: the command text itself must stay in the record.
_INLINE_SECRET = re.compile(
    r"(?i)\b(authorization|token|api[_-]?key|password|passwd|secret)\b(\s*[:=]\s*)"
    r"(?:bearer\s+)?(\S+)"
)
_BEARER = re.compile(r"(?i)\bbearer\s+(\S+)")
# Longest value kept per field; longer ones are truncated so one record stays bounded.
_MAX_FIELD_CHARS = 2000


def default_audit_dir(home: str | Path | None = None) -> Path:
    """Return the audit directory, preferring AVID_AUDIT_DIR, then AVID_HOME, then the host home."""
    override = os.environ.get(AUDIT_DIR_ENV)
    if override:
        return Path(override).expanduser()
    if os.environ.get("AVID_HOME"):
        return avid_home() / "audit"
    base = Path(home) if home is not None else Path.home()
    return base / ".avid" / "audit"


def redact_inline(text: str) -> str:
    """Mask inline credential values while keeping the surrounding command structure readable."""
    text = _INLINE_SECRET.sub(r"\1\2***", text)
    return _BEARER.sub("Bearer ***", text)


def _clean(value: Any) -> Any:
    # Redact and truncate every field recursively so no single record grows without bound.
    if isinstance(value, str):
        text = redact_inline(value)
        return text if len(text) <= _MAX_FIELD_CHARS else text[:_MAX_FIELD_CHARS] + "…"
    if isinstance(value, Mapping):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)


@dataclass
class AuditLog:
    """Audit writer for a single run; thread-safe because one batch may write concurrently."""

    directory: Path | None = None
    run_tag: str = ""
    run_id: str = ""
    mode: str = ""
    axes: Mapping[str, str] = field(default_factory=dict)
    sandbox: Mapping[str, Any] = field(default_factory=dict)
    # Injected clock keeps record timestamps and the daily file rollover testable.
    clock: Callable[[], float] = time.time
    failures: int = 0
    written: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def path(self) -> Path | None:
        """Return today's audit file, or None when the audit directory is disabled."""
        if self.directory is None:
            return None
        stamp = datetime.fromtimestamp(self.clock(), tz=UTC).strftime("%Y-%m-%d")
        return self.directory / f"{AUDIT_FILE_PREFIX}{stamp}.jsonl"

    def write(self, kind: str, **fields: Any) -> dict[str, Any] | None:
        """Append one record and return it; any write failure is counted and never raised."""
        record: dict[str, Any] = {
            "ts": int(self.clock() * 1000),
            "kind": kind,
            "run": self.run_id or self.run_tag,
            "mode": self.mode,
            "axes": _clean(dict(self.axes)),
        }
        if self.sandbox:
            record["sandbox"] = _clean(dict(self.sandbox))
        record.update({key: _clean(value) for key, value in fields.items() if value is not None})

        target = self.path()
        if target is None:
            return record
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        try:
            with self._lock:
                # Serialize appends so concurrent writers cannot interleave a single JSON line.
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("a", encoding="utf-8") as handle:
                    handle.write(line)
            self.written += 1
        except OSError as exc:
            self.failures += 1
            logger.warning("审计写不进去（%s），本次运行继续：%s", target, exc)
            return record
        return record

    def summary(self) -> dict[str, Any]:
        """Report counters into the run's final state so a silently failing audit stays visible."""
        return {
            "path": str(self.path()) if self.directory is not None else None,
            "written": self.written,
            "failures": self.failures,
        }


__all__ = [
    "AUDIT_DIR_ENV",
    "AUDIT_FILE_PREFIX",
    "AuditLog",
    "default_audit_dir",
    "redact_inline",
]

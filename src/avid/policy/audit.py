"""审计：把"谁按什么规则、对什么目标、做了什么裁决"写成只追加的一行行 JSON。

Agent Safety 的最后一项是 **Audit**——没有它，前面几层都只是"相信它按规则跑了"。

落点 ``~/.avid/audit/audit-YYYY-MM-DD.jsonl``：

* 按天分文件，纯追加（``O_APPEND``），不重写、不轮转删除；
* 在沙箱的**掩蔽清单**里（``~/.avid``）且被 ADMIN 规则 deny，模型读写不了它；
* 每条自带运行标识与三轴快照，因此"这次运行到底关没关沙箱"是可查的历史事实，
  而不是界面上的一句话。

两条取舍：

1. **审计要留下命令本身**：抹掉命令原文等于抹掉审计的用途。但内联凭据
   （``Authorization: Bearer …``、``token=…``）会被抹成 ``***``——审计文件不该成为
   第二份凭据副本。
2. **写失败不改结论**：磁盘满、目录不可写都只记计数与一条日志，绝不打断运行。
   审计失败是"少了一条记录"，不是"这次调用该失败"。代价是"审计静默失效"这一风险，
   所以 :attr:`AuditLog.failures` 会被带进运行终态，界面能看出"这次没留下记录"。
"""

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

#: 内联凭据（不是整份参数打码：命令原文要留下，只抹掉值）。
_INLINE_SECRET = re.compile(
    r"(?i)\b(authorization|token|api[_-]?key|password|passwd|secret)\b(\s*[:=]\s*)"
    r"(?:bearer\s+)?(\S+)"
)
_BEARER = re.compile(r"(?i)\bbearer\s+(\S+)")
_MAX_FIELD_CHARS = 2000


def default_audit_dir(home: str | Path | None = None) -> Path:
    """审计目录。优先级：``AVID_AUDIT_DIR`` > ``AVID_HOME/audit`` > ``<宿主家>/.avid/audit``。

    中间那一档让"整个用户级目录换掉"（测试、多宿主）一次性生效，不必逐个环境变量对齐。
    """
    override = os.environ.get(AUDIT_DIR_ENV)
    if override:
        return Path(override).expanduser()
    if os.environ.get("AVID_HOME"):
        return avid_home() / "audit"
    base = Path(home) if home is not None else Path.home()
    return base / ".avid" / "audit"


def redact_inline(text: str) -> str:
    """抹掉内联凭据的值，保留命令结构。"""
    text = _INLINE_SECRET.sub(r"\1\2***", text)
    return _BEARER.sub("Bearer ***", text)


def _clean(value: Any) -> Any:
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
    """一次运行的审计写入口。线程安全（批内并发调用会同时写）。"""

    directory: Path | None = None
    run_tag: str = ""
    run_id: str = ""
    mode: str = ""
    axes: Mapping[str, str] = field(default_factory=dict)
    sandbox: Mapping[str, Any] = field(default_factory=dict)
    clock: Callable[[], float] = time.time
    failures: int = 0
    written: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def path(self) -> Path | None:
        """今天该写的文件。``directory`` 为 None（禁用）时返回 None。"""
        if self.directory is None:
            return None
        stamp = datetime.fromtimestamp(self.clock(), tz=UTC).strftime("%Y-%m-%d")
        return self.directory / f"{AUDIT_FILE_PREFIX}{stamp}.jsonl"

    def write(self, kind: str, **fields: Any) -> dict[str, Any] | None:
        """写一条记录。**永不抛**：审计失败不该改变任何裁决。"""
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
        """随终态带出去：让"审计静默失效"可见。"""
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

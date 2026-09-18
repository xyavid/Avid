"""传输适配（F1）：HTTP 路由、DTO、SSE 编帧、静态资源与 SPA fallback。

本包是**唯一** import FastAPI 的地方（不变量 A1/A2）。``svc/`` 决定业务时序，
这里决定线格式：DTO 由显式 mapper 从内核 dataclass 构造（内核类型保持普通
dataclass，不是 pydantic），错误信封与事件分档也在这里定型。
"""

from __future__ import annotations

from .app import create_app

__all__ = ["create_app"]

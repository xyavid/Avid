"""Route package whose ``current_services`` helper is the only way routes obtain the service container."""

from __future__ import annotations

from fastapi import Request, WebSocket

from ...services import Services


def current_services(request: Request | WebSocket) -> Services:
    # WebSocket 也从 app.state 取容器（终端桥等 WS 端点与 HTTP 路由同一套服务）。
    return request.app.state.services


__all__ = ["current_services"]

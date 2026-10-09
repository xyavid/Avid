"""Route package; ``current_services`` is the only way routes obtain the service container."""

from __future__ import annotations

from fastapi import Request, WebSocket

from ...services import Services


def current_services(request: Request | WebSocket) -> Services:
    # WebSocket endpoints read the container from app.state too, the same Services as HTTP routes.
    return request.app.state.services


__all__ = ["current_services"]

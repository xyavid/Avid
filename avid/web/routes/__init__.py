"""Route package whose ``current_services`` helper is the only way routes obtain the service container."""

from __future__ import annotations

from fastapi import Request

from ...services import Services


def current_services(request: Request) -> Services:
    return request.app.state.services


__all__ = ["current_services"]

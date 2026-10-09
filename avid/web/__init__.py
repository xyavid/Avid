"""Transport adapter and the sole owner of the wire format: HTTP routes, DTOs, SSE framing,
static assets and the SPA fallback."""

from __future__ import annotations

from .app import create_app

__all__ = ["create_app"]

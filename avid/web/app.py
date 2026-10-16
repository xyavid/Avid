"""Assembles the FastAPI application: the trust boundary, the error envelope and the routers."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from ..services import API_VERSION, Services
from ..services.errors import ServiceError
from .routes import (
    approvals,
    events,
    files,
    inputs,
    meta,
    runs,
    search,
    sessions,
    settings,
    terminal,
    workspaces,
)
from .schemas import ErrorBody, ErrorOut

logger = logging.getLogger("avid.web.app")

# A trust boundary rather than authentication: it stops DNS rebinding through the Host header and
# cross-site requests through the Origin header, while command line clients send neither.
LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1"})
# Escape hatches for non-loopback deployments, read as a comma-separated list of hostnames.
ALLOWED_HOSTS_ENV = "AVID_ALLOWED_HOSTS"
ALLOWED_ORIGINS_ENV = "AVID_ALLOWED_ORIGINS"

# What a browser preflight is told it may send: Content-Type is the only non-safelisted header the
# client sets, and a longer list would grant callers that do not exist.
CORS_ALLOW_METHODS = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
CORS_ALLOW_HEADERS = "Content-Type"
CORS_MAX_AGE_SECONDS = 600

# No document is served from here, so these guard the API's own responses (nosniff above all); the
# UI's document carries whatever policy its host sets. Inline styles: Radix injects them at runtime.
SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        # Nothing here embeds anything; frame-ancestors 'none' guards the other direction.
        "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


def _hostname_of(value: str) -> str:
    """Reduces a Host or Origin value to a bare lowercase hostname, dropping any port."""
    if not value:
        return ""
    text = value.strip()
    # An Origin is a full URL, so the hostname lives in its netloc.
    if "//" in text:
        text = urlsplit(text).netloc or ""
    # IPv6 literals keep the address inside brackets, as in [::1]:8765.
    if text.startswith("["):
        return text[1:].split("]", 1)[0].lower()
    return text.rsplit(":", 1)[0].lower()


def _collect(
    env_names: tuple[str, ...], extra: frozenset[str] | None, *, loopback: bool
) -> frozenset[str]:
    """Collects hostnames from the named environment variables, loopback, and any explicit extras."""
    allowed = set(LOOPBACK_HOSTS) if loopback else set()
    for name in env_names:
        for item in os.environ.get(name, "").split(","):
            if item.strip():
                allowed.add(_hostname_of(item.strip()))
    if extra:
        allowed.update(extra)
    return frozenset(allowed)


def trusted_hosts(extra: frozenset[str] | None = None) -> frozenset[str]:
    """Hostnames this process answers to: the Host header, loopback always included."""
    return _collect((ALLOWED_HOSTS_ENV,), extra, loopback=True)


def trusted_origins(extra: frozenset[str] | None = None) -> frozenset[str]:
    """Hostnames whose pages may call the API: the Origin header, widened rather than narrowed.

    A name this process answers to is also a name whose pages may call it, and AVID_ALLOWED_ORIGINS
    covers the page hosted where this process is never addressed — the normal case now.
    """
    return _collect((ALLOWED_HOSTS_ENV, ALLOWED_ORIGINS_ENV), extra, loopback=True)


def declared_origins(extra: frozenset[str] | None = None) -> frozenset[str]:
    """Hostnames whose pages may open a terminal: declared only, and never the loopback default.

    A PTY is a shell as this user, so it does not inherit the convenience that lets any loopback
    page read sessions — same comparison, one tier stricter, and the refusal names itself.
    """
    return _collect((ALLOWED_HOSTS_ENV, ALLOWED_ORIGINS_ENV), extra, loopback=False)


def cors_headers(origin: str, *, preflight: bool) -> dict[str, str]:
    """Grants one browser origin. No cookies exist, so credentials are never allowed."""
    headers = {"Access-Control-Allow-Origin": origin}
    if preflight:
        headers["Access-Control-Allow-Methods"] = CORS_ALLOW_METHODS
        headers["Access-Control-Allow-Headers"] = CORS_ALLOW_HEADERS
        headers["Access-Control-Max-Age"] = str(CORS_MAX_AGE_SECONDS)
    return headers


class TrustBoundaryMiddleware(BaseHTTPMiddleware):
    """Refuses a Host or Origin outside its allowlists, and grants the origins that are inside.

    Refusal and grant are two halves of one decision — a page is either allowed to read the answer
    or it is not — so they share this class instead of splitting the allowlist in two.
    """

    def __init__(
        self, app: Any, allowed_hosts: frozenset[str], allowed_origins: frozenset[str]
    ) -> None:
        super().__init__(app)
        self.allowed_hosts = allowed_hosts
        self.allowed_origins = allowed_origins

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        host = _hostname_of(request.headers.get("host", ""))
        if host not in self.allowed_hosts:
            logger.warning("拒绝 Host 不在白名单内的请求：%s", host)
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content=_envelope(
                    "host_rejected", f"Host 不在允许列表内：{host or '(空)'}"
                ),
            )
        origin = request.headers.get("origin")
        if origin and _hostname_of(origin) not in self.allowed_origins:
            logger.warning("拒绝跨站来源：%s", origin)
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content=_envelope("origin_rejected", f"Origin 不在允许列表内：{origin}"),
            )
        headers = dict(SECURITY_HEADERS)
        # Set whether or not an Origin was sent: a cache must not reuse a granted answer ungranted.
        headers["Vary"] = "Origin"
        if origin:
            # Validated just above, so echoing the value back is the grant, not reflected input.
            headers.update(cors_headers(origin, preflight=request.method == "OPTIONS"))
            if request.method == "OPTIONS":
                # A preflight asks about the request that follows: no route declares OPTIONS.
                return Response(status_code=status.HTTP_204_NO_CONTENT, headers=headers)

        response = await call_next(request)
        for name, value in headers.items():
            response.headers.setdefault(name, value)
        return response

# Fallback JSON 404 for unknown API paths: this process serves no page, so no request falls back to
# one and every method gets JSON.
UNKNOWN_API = ErrorOut(
    error=ErrorBody(code="not_found", message="未知的 API 路径")
).model_dump()


def _envelope(code: str, message: str, detail: dict[str, Any] | None = None) -> dict:
    return {"error": {"code": code, "message": message, "detail": detail or {}}}


# The three fields that say where a body went wrong; pydantic also fills "input" with the offending
# value and "ctx" with the validator's own objects.
_SCHEMA_ERROR_FIELDS = ("type", "loc", "msg")


def _schema_errors(errors: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return each validation error by location and reason only.

    The value under "input" is the request body field, so echoing it hands a rejected secret
    (the settings endpoints take an api_key) back to the caller; "ctx" can hold objects that
    are not JSON at all.
    """
    return [
        {name: error[name] for name in _SCHEMA_ERROR_FIELDS if name in error} for error in errors
    ]


def create_app(
    *,
    services: Services | None = None,
    workspace_root: str | Path | None = None,
    allowed_hosts: frozenset[str] | None = None,
    allowed_origins: frozenset[str] | None = None,
) -> FastAPI:
    """Builds the application: trust middleware, error handlers and the routers."""
    assembled = services or Services(workspace_root=workspace_root)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        # Ctrl-C and SIGTERM reach here before the loop closes, and this is the only exit `avid
        # web` has: the runs still in flight are cancelled and waited for, then the layers they
        # write through are closed. In a thread, because that wait must not block the loop the
        # open streams are shutting down on.
        await asyncio.to_thread(assembled.close)

    app = FastAPI(
        title="Avid",
        version=f"api-v{API_VERSION}",
        description="Avid 本地编程 agent 的 Web API 与事件流",
        lifespan=lifespan,
    )
    # All three lists go on app.state: the terminal's WebSocket handshake never reaches this
    # middleware, so that endpoint reads the strictest one itself.
    app.state.allowed_hosts = trusted_hosts(allowed_hosts)
    app.state.allowed_origins = trusted_origins(allowed_origins)
    app.state.declared_origins = declared_origins(allowed_origins)
    app.add_middleware(
        TrustBoundaryMiddleware,
        allowed_hosts=app.state.allowed_hosts,
        allowed_origins=app.state.allowed_origins,
    )
    # A workspace root is only meaningful for self-assembly: given, the process is single-workspace.
    app.state.services = assembled

    @app.exception_handler(ServiceError)
    async def _service_error(_: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content=_envelope(exc.code, exc.message, exc.detail),
        )

    @app.exception_handler(RequestValidationError)
    async def _schema_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_envelope(
                "invalid_schema", "请求体不符合 schema", {"errors": _schema_errors(exc.errors())}
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope("http_error", str(exc.detail)),
        )

    @app.exception_handler(Exception)
    async def _internal_error(_: Request, exc: Exception) -> JSONResponse:
        # Return only a correlation id: echoing the exception type and text leaks kernel internals.
        error_id = uuid.uuid4().hex[:12]
        logger.exception("未处理的服务端错误（error_id=%s）：%s", error_id, exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_envelope(
                "internal",
                "内核内部错误（详情见服务端日志）",
                {"error_id": error_id},
            ),
        )

    for module in (
        meta,
        search,
        sessions,
        runs,
        inputs,
        approvals,
        events,
        workspaces,
        settings,
        terminal,
        files,
    ):
        app.include_router(module.router, prefix="/api")

    @app.api_route(
        "/api/{rest:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    async def unknown_api(rest: str) -> JSONResponse:
        return JSONResponse(status_code=404, content=UNKNOWN_API)

    @app.get("/", include_in_schema=False)
    async def root() -> JSONResponse:
        """Opening the port in a browser is now the likeliest wrong turn, so name it in the answer."""
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=_envelope(
                "api_only", "这个进程只提供 /api：界面由你托管的那份前端提供，这里没有页面可开。"
            ),
        )

    return app


__all__ = [
    "ALLOWED_HOSTS_ENV",
    "ALLOWED_ORIGINS_ENV",
    "CORS_ALLOW_HEADERS",
    "CORS_ALLOW_METHODS",
    "CORS_MAX_AGE_SECONDS",
    "LOOPBACK_HOSTS",
    "SECURITY_HEADERS",
    "TrustBoundaryMiddleware",
    "cors_headers",
    "create_app",
    "declared_origins",
    "trusted_hosts",
    "trusted_origins",
]

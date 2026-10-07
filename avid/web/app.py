"""Assembles the FastAPI application: routers, error envelope, static assets and the SPA fallback."""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from ..services import API_VERSION, Services
from ..services.errors import ServiceError
from .routes import approvals, events, meta, runs, sessions, settings, terminal, workspaces
from .schemas import ErrorBody, ErrorOut

logger = logging.getLogger("avid.web.app")

STATIC_DIR = Path(__file__).parent / "static"

# A trust boundary rather than authentication: it stops DNS rebinding through the Host header and
# cross-site form posts through the Origin header, while command line clients send neither.
LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1"})
# Escape hatch for non-loopback deployments, read as a comma-separated list of extra hostnames.
ALLOWED_HOSTS_ENV = "AVID_ALLOWED_HOSTS"

# Inline styles are needed because Radix injects scroll-lock styles at runtime; data: images are favicons.
SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        # frame-src 放开 http/https：dock 的浏览器面板按用户输入内嵌任意站点。
        # frame-ancestors 'none' 不变——它拦的是别人把我们嵌进去，方向相反。
        "frame-src 'self' http: https:; "
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


def trusted_hosts(extra: frozenset[str] | None = None) -> frozenset[str]:
    """Collects the allowed hostnames: loopback, the environment variable, and any explicit extras."""
    allowed = set(LOOPBACK_HOSTS)
    for item in os.environ.get(ALLOWED_HOSTS_ENV, "").split(","):
        if item.strip():
            allowed.add(_hostname_of(item.strip()))
    if extra:
        allowed.update(extra)
    return frozenset(allowed)


class TrustBoundaryMiddleware(BaseHTTPMiddleware):
    """Rejects requests whose Host or Origin is not allowed, then attaches the security headers."""

    def __init__(self, app: Any, allowed_hosts: frozenset[str]) -> None:
        super().__init__(app)
        self.allowed_hosts = allowed_hosts

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
        if origin and _hostname_of(origin) not in self.allowed_hosts:
            logger.warning("拒绝跨站来源：%s", origin)
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content=_envelope("origin_rejected", f"Origin 不在允许列表内：{origin}"),
            )
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

# Fallback JSON 404 for unknown API paths, which must never fall back to the SPA shell.
UNKNOWN_API = ErrorOut(
    error=ErrorBody(code="not_found", message="未知的 API 路径")
).model_dump()


def load_build_info(static_dir: Path) -> dict[str, Any]:
    """Reads the build stamp that says whether the bundle was packaged or served from a checkout."""
    stamp = static_dir / ".build.json"
    if stamp.is_file():
        try:
            data = json.loads(stamp.read_text(encoding="utf-8"))
            return {
                "git_sha": data.get("git_sha"),
                "built_at": data.get("built_at"),
                "source": "bundled",
            }
        except (OSError, ValueError):
            logger.warning("构建戳读不了：%s", stamp)
    return {"git_sha": None, "built_at": None, "source": "dev"}


def _envelope(code: str, message: str, detail: dict[str, Any] | None = None) -> dict:
    return {"error": {"code": code, "message": message, "detail": detail or {}}}


def create_app(
    *,
    services: Services | None = None,
    static_dir: str | Path | None = None,
    workspace_root: str | Path | None = None,
    allowed_hosts: frozenset[str] | None = None,
) -> FastAPI:
    """Builds the application: trust middleware, error handlers, routers and the static fallback."""
    app = FastAPI(
        title="Avid",
        version=f"api-v{API_VERSION}",
        description="自建 agent 运行时（harness）的 Web API 与事件流",
    )
    # 白名单挂上 state：WebSocket 端点（终端桥）做同样的信任校验要用同一份。
    app.state.allowed_hosts = trusted_hosts(allowed_hosts)
    app.add_middleware(
        TrustBoundaryMiddleware, allowed_hosts=app.state.allowed_hosts
    )
    # A workspace root is only meaningful for self-assembly: given, the process is single-workspace.
    app.state.services = services or Services(workspace_root=workspace_root)
    app.state.static_dir = Path(static_dir) if static_dir is not None else STATIC_DIR
    app.state.build = load_build_info(app.state.static_dir)

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
            content=_envelope("invalid_schema", "请求体不符合 schema", {"errors": exc.errors()}),
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

    for module in (meta, sessions, runs, approvals, events, workspaces, settings, terminal):
        app.include_router(module.router, prefix="/api")

    @app.api_route(
        "/api/{rest:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    async def unknown_api(rest: str) -> JSONResponse:
        return JSONResponse(status_code=404, content=UNKNOWN_API)

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> Any:
        root: Path = app.state.static_dir
        if root.is_dir():
            target = (root / path).resolve()
            # Resolving first keeps a traversal path from escaping the static directory.
            if target.is_file() and root.resolve() in target.parents:
                return FileResponse(target)
            # A missing file with an extension is a missing asset, not a route: the fallback would
            # make the browser parse HTML as script, so it gets an explicit 404 instead.
            if "." in path.rsplit("/", 1)[-1]:
                return JSONResponse(
                    status_code=status.HTTP_404_NOT_FOUND,
                    content=_envelope("asset_not_found", f"静态资源不存在：{path}"),
                )
            index = root / "index.html"
            if index.is_file():
                return FileResponse(index)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=_envelope(
                "static_missing",
                "前端产物不存在：前端尚未重建，没有可服务的静态资源。",
                {"static_dir": str(root)},
            ),
        )

    return app


__all__ = [
    "ALLOWED_HOSTS_ENV",
    "SECURITY_HEADERS",
    "LOOPBACK_HOSTS",
    "STATIC_DIR",
    "TrustBoundaryMiddleware",
    "create_app",
    "load_build_info",
    "trusted_hosts",
]

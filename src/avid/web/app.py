"""装配 FastAPI 应用：路由、错误信封、静态资源与 SPA fallback。

两条路由规则（设计文档 §4.3）：

* ``/api/*`` **永远**返回 JSON 或 SSE，未知路径返回 JSON 404，绝不回落到 SPA
  外壳——否则前端会把 404 当 HTML 解析，错误变成静默；
* 其余路径先找静态文件，找不到再回落 ``index.html``（SPA fallback）。

产物漂移的对冲：``copy-dist.mjs`` 写 ``static/.build.json``（git_sha + built_at），
``GET /api/meta`` 返回它，UI 页脚显示。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from ..svc import API_VERSION, Services
from ..svc.errors import ServiceError
from . import routes  # noqa: F401 - 只为让 routes 包可见
from .routes import approvals, events, meta, runs, sessions, tasks, workspaces
from .schemas import ErrorBody, ErrorOut

logger = logging.getLogger("avid.web.app")

STATIC_DIR = Path(__file__).parent / "static"

# 信任边界（I-P10 / architecture-criteria §12）：这不是鉴权（本地单用户工具没有
# 账号体系），而是把两类"浏览器替别人发请求"的路堵掉。
#
# * **DNS rebinding**：恶意页面把某个域名解析到 127.0.0.1，浏览器的同源策略就
#   认为它在跟自己的源说话——此时 Host 头是那个域名，白名单外直接拒。
# * **CSRF**：跨站表单与 `fetch` 的"简单请求"不做预检就能打到无 body 的写端点
#   （`POST /api/workspaces/pick` 会在宿主机弹文件夹选择器、`POST
#   /api/runs/{id}/cancel` 会取消任务）。浏览器对跨源请求一定带 Origin，白名单外
#   拒掉即可；命令行与测试不带 Origin，因此不受影响。
#
# `AVID_ALLOWED_HOSTS`（逗号分隔）是给非回环部署的显式逃生口，见 `cli._run_web`。
LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1"})
ALLOWED_HOSTS_ENV = "AVID_ALLOWED_HOSTS"


def _hostname_of(value: str) -> str:
    """从 Host / Origin 里取出主机名：去掉端口、方括号、大小写。"""
    if not value:
        return ""
    text = value.strip()
    if "//" in text:  # Origin 形态
        text = urlsplit(text).netloc or ""
    if text.startswith("["):  # IPv6 字面量 [::1]:8765
        return text[1:].split("]", 1)[0].lower()
    return text.rsplit(":", 1)[0].lower()


def trusted_hosts(extra: frozenset[str] | None = None) -> frozenset[str]:
    """允许的 Host / Origin 主机名：回环 + 环境变量追加 + 装配时显式给的那些。"""
    allowed = set(LOOPBACK_HOSTS)
    for item in os.environ.get(ALLOWED_HOSTS_ENV, "").split(","):
        if item.strip():
            allowed.add(_hostname_of(item.strip()))
    if extra:
        allowed.update(extra)
    return frozenset(allowed)


class TrustBoundaryMiddleware(BaseHTTPMiddleware):
    """Host 白名单 + Origin 校验。放行要两条都过。"""

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
        return await call_next(request)

# 未知 /api 路径的兜底 JSON 404。绝不回落 SPA（B10）。
UNKNOWN_API = ErrorOut(
    error=ErrorBody(code="not_found", message="未知的 API 路径")
).model_dump()


def load_build_info(static_dir: Path) -> dict[str, Any]:
    """构建戳：产物是 bundling 进来的还是从 checkout 直接跑的。"""
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
    app = FastAPI(
        title="Avid",
        version=f"api-v{API_VERSION}",
        description="自建 agent 运行时（harness）的 Web API 与事件流",
    )
    app.add_middleware(
        TrustBoundaryMiddleware, allowed_hosts=trusted_hosts(allowed_hosts)
    )
    # ``workspace_root`` 只在自装配时有用：指定它就是单工作区模式，
    # 不给则是多工作区模式（建会话必须指定归属）。
    app.state.services = services or Services(workspace_root=workspace_root)
    app.state.static_dir = Path(static_dir) if static_dir is not None else STATIC_DIR
    app.state.build = load_build_info(app.state.static_dir)

    # ---------------- 错误信封 ----------------

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
        logger.exception("未处理的服务端错误：%s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_envelope("internal", f"{type(exc).__name__}: {exc}"),
        )

    # ---------------- 路由 ----------------

    for module in (meta, sessions, runs, approvals, events, tasks, workspaces):
        app.include_router(module.router, prefix="/api")

    @app.api_route(
        "/api/{rest:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    async def unknown_api(rest: str) -> JSONResponse:
        return JSONResponse(status_code=404, content=UNKNOWN_API)

    # ---------------- 静态资源与 SPA fallback ----------------

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> Any:
        root: Path = app.state.static_dir
        if root.is_dir():
            target = (root / path).resolve()
            if target.is_file() and root.resolve() in target.parents:
                return FileResponse(target)
            # 带扩展名却找不到的文件是**缺失资源**，不是前端路由：回落 index.html 会让
            # 浏览器把 HTML 当 JS 解析（陈旧缓存页的典型失败），必须显式 404。
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
                "前端产物不存在：先 `pnpm -C web build`，再 `pnpm -C web run copy:dist`。",
                {"static_dir": str(root)},
            ),
        )

    return app


__all__ = [
    "ALLOWED_HOSTS_ENV",
    "LOOPBACK_HOSTS",
    "STATIC_DIR",
    "TrustBoundaryMiddleware",
    "create_app",
    "load_build_info",
    "trusted_hosts",
]

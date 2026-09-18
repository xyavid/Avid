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
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..svc import API_VERSION, Services
from ..svc.errors import ServiceError
from . import routes  # noqa: F401 - 只为让 routes 包可见
from .routes import approvals, events, meta, runs, sessions, tasks, workspaces
from .schemas import ErrorBody, ErrorOut

logger = logging.getLogger("avid.web.app")

STATIC_DIR = Path(__file__).parent / "static"

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
) -> FastAPI:
    app = FastAPI(
        title="Avid",
        version=f"api-v{API_VERSION}",
        description="自建 agent 运行时（harness）的 Web API 与事件流",
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


__all__ = ["STATIC_DIR", "create_app", "load_build_info"]

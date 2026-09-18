"""``/api/meta`` · ``/api/health`` · ``/api/skills``：版本、特性表与能力面。

按特性分支、不按版本号分支：客户端读 ``features`` 决定启用哪些能力，只在
``api_version`` **不兼容**时失败收敛（§6.3）。构建戳由这里补上——它是静态资源
目录的事实，属于传输适配层，不属于 ``svc/``。
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from ...runtime.events import now_ms
from ..schemas import HealthOut, MetaOut, SkillListOut
from . import current_services

router = APIRouter()


@router.get("/meta", response_model=MetaOut)
def get_meta(request: Request) -> dict:
    services = current_services(request)
    meta = services.meta()
    meta["build"] = request.app.state.build
    return meta


@router.get("/health", response_model=HealthOut)
def get_health(request: Request) -> dict:
    services = current_services(request)
    return {
        "status": "ok",
        "api_version": services.meta()["api_version"],
        "uptime_ms": max(0, now_ms() - services.started_at),
    }


@router.get("/skills", response_model=SkillListOut)
def get_skills(request: Request) -> dict:
    """技能目录：name + 一行描述，与 system prompt 同源。"""
    return {"skills": current_services(request).skills()}


__all__ = ["router"]

"""Meta endpoints describing the API version, the feature flags and the capability surface."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ...runtime.events import now_ms
from ...svc import API_VERSION
from ..schemas import HealthOut, MetaOut, SkillListOut
from . import current_services

router = APIRouter()


@router.get("/meta", response_model=MetaOut)
def get_meta(request: Request) -> dict:
    """Returns service metadata with the build stamp attached, which belongs to this layer."""
    services = current_services(request)
    meta = services.meta()
    meta["build"] = request.app.state.build
    return meta


@router.get("/health", response_model=HealthOut)
def get_health(request: Request) -> dict:
    services = current_services(request)
    return {
        "status": "ok",
        # The version is a module constant, so health avoids a metadata call that scans the skills.
        "api_version": API_VERSION,
        "uptime_ms": max(0, now_ms() - services.started_at),
    }


@router.get("/skills", response_model=SkillListOut)
def get_skills(request: Request) -> dict:
    """Lists the skill directory entries, taken from the same source as the system prompt."""
    return {"skills": current_services(request).skills()}


__all__ = ["router"]

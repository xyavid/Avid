"""Model settings endpoints: read the effective connection, write the UI overlay, reset to env.

The overlay file lives at `~/.avid/model.toml` (or `AVID_MODEL_CONFIG`) and takes
precedence over environment variables; `load_config` re-reads it on every run, so a
save takes effect for the next message without a restart.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Response, status

from ...ai.config import (
    DEFAULT_BASE_URLS,
    ENV_API_KEY,
    ENV_BASE_URL,
    ENV_MODEL,
    ENV_PROVIDER,
    clear_model_settings,
    read_model_settings,
    write_model_settings,
)
from ..schemas import ModelSettingsIn, ModelSettingsOut

router = APIRouter()


def _effective() -> dict:
    """Effective values as the config loader would see them; never exposes the key itself.

    叠加规则与 load_config 一致：界面覆盖层（短名）优先，其次环境变量。
    """
    overlay = read_model_settings()

    def pick(short: str, env_name: str) -> str | None:
        value = overlay.get(short) or os.environ.get(env_name, "").strip()
        return value or None

    provider = pick("provider", ENV_PROVIDER)
    base_url = pick("base_url", ENV_BASE_URL)
    if base_url is None and provider in DEFAULT_BASE_URLS:
        base_url = DEFAULT_BASE_URLS[provider]
    return {
        "model": pick("model", ENV_MODEL),
        "base_url": base_url,
        "provider": provider,
        "api_key_set": pick("api_key", ENV_API_KEY) is not None,
        "overlay_active": bool(overlay),
    }


@router.get("/settings/model", response_model=ModelSettingsOut)
def get_model_settings() -> dict:
    return _effective()


@router.put("/settings/model", response_model=ModelSettingsOut)
def put_model_settings(body: ModelSettingsIn) -> dict:
    """Merge the given fields into the overlay; an empty string clears that field back to env."""
    values = read_model_settings()
    for name in ("model", "base_url", "provider", "api_key"):
        incoming = getattr(body, name)
        if incoming is None:
            continue
        if incoming == "":
            values.pop(name, None)
        else:
            values[name] = incoming
    write_model_settings(values)
    return _effective()


@router.delete("/settings/model", status_code=status.HTTP_204_NO_CONTENT)
def delete_model_settings() -> Response:
    """Drop the overlay so environment variables take over again."""
    clear_model_settings()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]

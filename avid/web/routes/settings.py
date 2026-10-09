"""BYOK settings endpoints: read the whole config, save it wholesale, run a two-step connectivity
check and drop both files on reset.

This is the only source of model connections, and the plaintext key is write-only — it never rides
back in any response and an invalid config never leaves half a key on disk.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from fastapi import APIRouter, Request, Response, status

from ...providers import byok
from ...providers.byok import (
    CAPABILITY_FIELDS,
    ByokConfig,
    ModelCapabilities,
    ModelDecl,
    ProviderDecl,
)
from ...providers.config import ConfigError
from ...providers.verify import verify_provider
from ...security import userdirs
from ...services.errors import InvalidRequest
from ..schemas import (
    ByokModel,
    ByokProviderIn,
    ByokProviderOut,
    ByokSettingsIn,
    ByokSettingsOut,
    ByokTestIn,
    ByokTestOut,
    CapabilityFlags,
    SessionsDirIn,
    SessionsDirOut,
    VerifyStepOut,
)
from . import current_services

router = APIRouter()


def _to_decl(body: ByokProviderIn) -> ProviderDecl:
    """Wire payload → in-memory declaration; the plaintext key never rides along."""
    return ProviderDecl(
        id=body.id,
        label=body.label,
        protocol=body.protocol,
        base_url=body.base_url,
        headers=dict(body.headers),
        extra_body=dict(body.extra_body),
        enabled=body.enabled,
        models=tuple(
            ModelDecl(
                id=m.id,
                label=m.label,
                context_window=m.context_window,
                max_output=m.max_output,
                capabilities=ModelCapabilities(**m.capabilities.model_dump()),
            )
            for m in body.models
        ),
    )


def _provider_out(provider: ProviderDecl, secrets: dict[str, str]) -> ByokProviderOut:
    return ByokProviderOut(
        id=provider.id,
        label=provider.label,
        protocol=provider.protocol,
        base_url=provider.base_url,
        headers=dict(provider.headers),
        extra_body=dict(provider.extra_body),
        enabled=provider.enabled,
        # Field-by-field ModelDecl -> ByokModel mapping keeps the key out of the config.
        models=[
            ByokModel(
                id=m.id,
                label=m.label,
                context_window=m.context_window,
                max_output=m.max_output,
                capabilities=CapabilityFlags(
                    **{name: getattr(m.capabilities, name) for name in CAPABILITY_FIELDS}
                ),
            )
            for m in provider.models
        ],
        key_set=bool(secrets.get(provider.id)),
    )


def _out(config: ByokConfig) -> ByokSettingsOut:
    secrets = byok.read_secrets()
    return ByokSettingsOut(
        providers=[_provider_out(p, secrets) for p in config.providers.values()],
        bindings=dict(config.bindings),
    )


@router.get("/settings/byok", response_model=ByokSettingsOut)
def get_byok_settings() -> ByokSettingsOut:
    config = byok.load_byok()
    if config is None:
        return ByokSettingsOut(bindings={"chat": None})
    return _out(config)


@router.put("/settings/byok", response_model=ByokSettingsOut)
def put_byok_settings(body: ByokSettingsIn) -> ByokSettingsOut:
    unknown = set(body.bindings) - {"chat"}
    if unknown:
        raise InvalidRequest(f"未知的绑定槽位：{'、'.join(sorted(unknown))}")
    providers: dict[str, ProviderDecl] = {}
    for item in body.providers:
        if item.id in providers:
            raise InvalidRequest(f"provider id 重复：{item.id}")
        providers[item.id] = _to_decl(item)
    config = ByokConfig(providers=providers, bindings={"chat": body.bindings.get("chat")})

    # Save the validated config, then the write-only key, so a bad config leaves no partial key.
    try:
        byok.save_byok(config)
    except ConfigError as exc:
        raise InvalidRequest(str(exc)) from exc
    for item in body.providers:
        if item.api_key is None:
            continue
        if item.api_key == "":
            byok.delete_secret(item.id)
        else:
            byok.set_secret(item.id, item.api_key)
    return _out(config)


@router.post("/settings/byok/test", response_model=ByokTestOut)
def post_byok_test(body: ByokTestIn) -> ByokTestOut:
    """Two-step connectivity check against the payload, so it can run before the config is saved."""
    provider = _to_decl(body.provider)
    report = verify_provider(provider, body.model_id, secret=body.provider.api_key)
    return ByokTestOut(
        ok=report.ok,
        steps=[
            VerifyStepOut(step=step.step, ok=step.ok, detail=step.detail) for step in report.steps
        ],
    )


@router.delete("/settings/byok", status_code=status.HTTP_204_NO_CONTENT)
def delete_byok_settings() -> Response:
    """Drop both files; runs will fail with the no-config error until a new config is saved."""
    for target in (byok.config_path(), byok.secrets_path()):
        with contextlib.suppress(FileNotFoundError):
            target.unlink()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _sessions_out() -> SessionsDirOut:
    source = userdirs.sessions_dir_source()
    return SessionsDirOut(
        dir=str(userdirs.sessions_dir().expanduser()),
        default_dir=str(userdirs.default_sessions_dir()),
        source=source,
        # The env var beats the file, so the UI shows those fields read-only.
        editable=source != "env",
    )


def _prepare_sessions_dir(raw: str) -> Path:
    """Turns a UI-supplied path into a usable session directory: absolute only, created in place."""
    text = raw.strip()
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise InvalidRequest(f"要一个绝对路径：{text}")
    if path.exists() and not path.is_dir():
        raise InvalidRequest(f"这个路径上已经有一个文件：{path}")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise InvalidRequest(f"建不出这个目录：{path}（{exc}）") from exc
    return path.resolve()


@router.get("/settings/sessions", response_model=SessionsDirOut)
def get_sessions_settings() -> SessionsDirOut:
    return _sessions_out()


@router.put("/settings/sessions", response_model=SessionsDirOut)
def put_sessions_settings(request: Request, body: SessionsDirIn) -> SessionsDirOut:
    """Changes only where new sessions are written: create the directory, persist it and rebind
    cached repos, leaving existing sessions untouched.
    """
    if userdirs.sessions_dir_source() == "env":
        raise InvalidRequest(
            f"环境变量 {userdirs.SESSIONS_DIR_ENV} 已经定了会话目录，写配置也不生效；"
            "先去掉那个环境变量再在这里改。"
        )
    # A null value drops the key and falls back to the default, hence the None case below.
    patch: dict[str, str | None]
    if body.dir.strip():
        target = _prepare_sessions_dir(body.dir)
        patch = {userdirs.SESSIONS_DIR_KEY: str(target)}
    else:
        patch = {userdirs.SESSIONS_DIR_KEY: None}
    try:
        userdirs.write_settings(patch)
    except OSError as exc:
        raise InvalidRequest(f"设置写不进 {userdirs.settings_path()}（{exc}）") from exc
    # Cached repos would keep writing to the old directory without this rebind.
    # An active run refuses with 409 session_busy; see Services.rebind_session_store.
    current_services(request).rebind_session_store()
    return _sessions_out()


__all__ = ["router"]

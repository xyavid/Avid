"""BYOK 设置端点：读整份配置、整体保存、两步连通校验、重置回落。

- GET /api/settings/byok：providers + bindings + 每家的 key_set；密钥明文**只入不出**，
  任何响应都不回传。这是模型连接的唯一来源——没有配置时运行会报 config_error。
- PUT /api/settings/byok：整体保存。载荷里的 api_key（只入）剥出写进 secrets.json
  （按 provider id 存；鉴权隐式：有密钥就按协议标准头发送），validate 不过就不落盘
  （invalid_request），也不会留下半份密钥。
- POST /api/settings/byok/test：对载荷里的提供商+模型跑两步探测（真请求：一次
  max_tokens=1 + 一次工具冒烟）；密钥走载荷，不读也不写密钥文件。
- DELETE /api/settings/byok：删配置与密钥两份文件（之后运行会报「还没有模型配置」）。

生效路径：`resolve_chat` 每次运行都重读文件，保存后对下一条消息立即生效，无需重启。

会话目录（阶段 56）另有两个端点：

- GET /api/settings/sessions：会话目录的当前值、默认值与生效来源；
- PUT /api/settings/sessions：改目录（就地建好、写 settings.json、解绑缓存仓库）。
  只改「新会话写哪」，不搬已有会话——搬数据是 `avid session migrate` 的事。
  环境变量 AVID_SESSIONS_DIR 在时界面只读（它赢过配置文件）。
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
        # ModelDecl → ByokModel；逐字段搬，保持「密钥不落配置」的边界。
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

    # 先 validate 并落盘配置；成功后才把只入的 api_key 写进密钥文件，
    # 这样非法配置不会留下半份密钥。
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
    """两步连通校验；针对请求载荷而不是已保存的配置，保存前就能测。"""
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
        # 环境变量赢过文件：那两栏在界面上就是只读的，改文件也不生效。
        editable=source != "env",
    )


def _prepare_sessions_dir(raw: str) -> Path:
    """把界面给的路径变成可用的会话目录：要绝对路径，就地建好。"""
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
    """只改「新会话写哪」：目录就地建好、写进设置文件、解绑缓存仓库；已有会话不动。"""
    if userdirs.sessions_dir_source() == "env":
        raise InvalidRequest(
            f"环境变量 {userdirs.SESSIONS_DIR_ENV} 已经定了会话目录，写配置也不生效；"
            "先去掉那个环境变量再在这里改。"
        )
    # None 值 = 删掉这个键（回落默认），所以这里允许 None。
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
    # 仓库按工作区缓存着：不重新绑定的话，下一条消息还会写进旧目录。
    # 有活动 run 时这一步会拒绝（409 session_busy）——见 Services.rebind_session_store。
    current_services(request).rebind_session_store()
    return _sessions_out()


__all__ = ["router"]

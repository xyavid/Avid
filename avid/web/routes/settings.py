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
"""

from __future__ import annotations

import contextlib

from fastapi import APIRouter, Response, status

from ...ai import byok
from ...ai.byok import (
    CAPABILITY_FIELDS,
    ByokConfig,
    ModelCapabilities,
    ModelDecl,
    ProviderDecl,
)
from ...ai.config import ConfigError
from ...ai.verify import verify_provider
from ...svc.errors import InvalidRequest
from ..schemas import (
    ByokModel,
    ByokProviderIn,
    ByokProviderOut,
    ByokSettingsIn,
    ByokSettingsOut,
    ByokTestIn,
    ByokTestOut,
    CapabilityFlags,
    VerifyStepOut,
)

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


__all__ = ["router"]

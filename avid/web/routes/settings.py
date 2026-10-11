"""BYOK settings endpoints: read the whole config, save it wholesale, run a two-step connectivity
check and drop both files on reset.

This is the only source of model connections, and the plaintext key is write-only — it never rides
back in any response and an invalid config never leaves half a key on disk.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from fastapi import APIRouter, Request, Response, status

from ...agent.tools import web as web_tools
from ...providers import byok
from ...providers.byok import (
    CAPABILITY_FIELDS,
    ByokConfig,
    ModelCapabilities,
    ModelDecl,
    ProviderDecl,
)
from ...providers.client import list_models
from ...providers.config import ConfigError
from ...providers.protocol import LLMError
from ...providers.verify import classify_error, verify_provider
from ...security import userdirs
from ...services.errors import InvalidRequest
from ...web_settings import (
    DEFAULT_SEARCH_URL,
    DEFAULT_TIMEOUT_SECONDS,
    ENV_ALLOW_PRIVATE,
    ENV_SEARCH_URL,
    ENV_TIMEOUT,
    MAX_TIMEOUT_SECONDS,
    MIN_TIMEOUT_SECONDS,
    SECRET_REF,
    SIGNUP_URL,
    allow_private_hosts,
    delete_search_key,
    read_search_key,
    resolved_search_url,
    resolved_timeout,
    save_settings,
    write_search_key,
)
from ..schemas import (
    ByokFileOut,
    ByokModel,
    ByokModelsIn,
    ByokModelsOut,
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
    WebSettingsIn,
    WebSettingsOut,
    WebTestIn,
    WebTestOut,
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
                reasoning_efforts=tuple(m.reasoning_efforts),
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
                reasoning_efforts=list(m.reasoning_efforts),
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


@router.get("/settings/byok/file", response_model=ByokFileOut)
def get_byok_file() -> ByokFileOut:
    """The config file itself: path plus the raw JSON, so the panel can show what is on disk and
    a hand-edited file is easy to check. A missing file is a normal state, not an error.
    """
    path = byok.config_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ByokFileOut(path=str(path), exists=False)
    except OSError as exc:
        raise InvalidRequest(f"读不了 {path}（{exc}）") from exc
    return ByokFileOut(path=str(path), exists=True, raw=raw)


@router.post("/settings/byok/models", response_model=ByokModelsOut)
def post_byok_models(body: ByokModelsIn) -> ByokModelsOut:
    """Ask the endpoint which models it offers, so the settings panel can fill the ids in.

    Runs against the payload rather than saved config (same as the connectivity check), and an
    unsupported or misconfigured endpoint reports a reason instead of raising.
    """
    provider = _to_decl(body.provider)
    try:
        config = byok.config_from_provider(provider, "", secret=body.provider.api_key)
    except ConfigError as exc:
        return ByokModelsOut(ok=False, detail=str(exc))
    try:
        models = list_models(config)
    except LLMError as exc:
        return ByokModelsOut(ok=False, detail=classify_error(str(exc)))
    if not models:
        return ByokModelsOut(ok=False, detail="端点没有返回任何模型（它可能不提供 /models 列表）")
    return ByokModelsOut(ok=True, models=models, detail=f"端点提供了 {len(models)} 个模型")


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


# ---- web tools (search + fetch) ----

#: A canned query, so the connectivity check costs exactly one search.
TEST_QUERY = "avid connectivity check"


def _web_out() -> WebSettingsOut:
    url, url_source = resolved_search_url()
    timeout, timeout_source = resolved_timeout()
    return WebSettingsOut(
        key_set=bool(read_search_key()),
        search_url=url,
        default_search_url=DEFAULT_SEARCH_URL,
        search_url_source=url_source,
        timeout_seconds=timeout,
        default_timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
        timeout_source=timeout_source,
        allow_private_hosts=allow_private_hosts(),
        env_search_url=ENV_SEARCH_URL,
        env_timeout=ENV_TIMEOUT,
        env_allow_private=ENV_ALLOW_PRIVATE,
        signup_url=SIGNUP_URL,
        secret_ref=SECRET_REF,
        secrets_file=str(byok.secrets_path()),
    )


@router.get("/settings/web", response_model=WebSettingsOut)
def get_web_settings() -> WebSettingsOut:
    return _web_out()


@router.put("/settings/web", response_model=WebSettingsOut)
def put_web_settings(body: WebSettingsIn) -> WebSettingsOut:
    """Validates before writing, so a rejected endpoint never leaves half of it saved; a field an
    environment variable owns is refused rather than stored into a setting that cannot win."""
    url = body.search_url.strip()
    if url and not url.startswith(("http://", "https://")):
        raise InvalidRequest(f"检索端点是接口根地址，要带协议头（http:// 或 https://）：{url}")
    timeout = body.timeout_seconds
    if timeout is not None and not MIN_TIMEOUT_SECONDS <= timeout <= MAX_TIMEOUT_SECONDS:
        raise InvalidRequest(
            f"超时要落在 {MIN_TIMEOUT_SECONDS:g} 到 {MAX_TIMEOUT_SECONDS:g} 秒之间：{timeout:g}"
        )
    key = (body.api_key or "").strip()
    if key and not key.isascii():
        raise InvalidRequest("Key 含非 ASCII 字符，不是有效的 Key；值里只放 Key 本身。")

    effective_url, url_source = resolved_search_url()
    if url_source == "env" and url != effective_url:
        raise InvalidRequest(
            f"环境变量 {ENV_SEARCH_URL} 已经定了检索端点，写设置也不生效；"
            "先去掉那个环境变量再在这里改。"
        )
    effective_timeout, timeout_source = resolved_timeout()
    if timeout_source == "env" and timeout is not None and timeout != effective_timeout:
        raise InvalidRequest(
            f"环境变量 {ENV_TIMEOUT} 已经定了超时，写设置也不生效；先去掉那个环境变量再在这里改。"
        )

    if body.api_key is not None:
        if key:
            write_search_key(key)
        else:
            delete_search_key()
    try:
        # An env-owned field is left alone: writing the env value into the file would take over
        # silently the day that variable is removed.
        save_settings(
            search_url=None if url_source == "env" else (url or None),
            timeout_seconds=None if timeout_source == "env" else timeout,
        )
    except OSError as exc:
        raise InvalidRequest(f"设置写不进 {userdirs.settings_path()}（{exc}）") from exc
    return _web_out()


@router.post("/settings/web/test", response_model=WebTestOut)
def test_web_settings(body: WebTestIn) -> WebTestOut:
    """Runs a real search through the same core the model's tools use, so the panel cannot report
    a working setup the tools would then fail on."""
    key = (body.api_key or "").strip() or read_search_key()
    if not key:
        return WebTestOut(ok=False, message="还没有 Key：先在上面填入并保存，再测试。")
    if not key.isascii():
        return WebTestOut(ok=False, message="Key 含非 ASCII 字符，不是有效的 Key。")
    url = (body.search_url or "").strip() or resolved_search_url()[0]

    result = web_tools.search_once(TEST_QUERY, key=key, url=url, max_results=1)
    if result.startswith("错误："):
        return WebTestOut(ok=False, message=result)
    return WebTestOut(ok=True, message=f"连得上：{result.splitlines()[0]}")


__all__ = ["router"]

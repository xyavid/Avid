"""Shared fixtures: a temp workspace root and a plausible BYOK model config for every run."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from avid.agent.tools import workspace
from avid.services.workspace_registry import AVID_HOME_ENV


@pytest.fixture(autouse=True)
def model_env(request, tmp_path, monkeypatch):
    """Every test gets a plausible BYOK config: svc resolves the chat model on the run thread.

    The `eval` / `eval_smoke` markers are skipped so real-model runs keep the real config under
    `~/.avid`: forced to `test-key` they all return 401, which those markers do not count as an
    instrument error (`support.INFRA_STATUSES`) and would read as "all red but green".
    """
    if request.node.get_closest_marker("eval") or request.node.get_closest_marker("eval_smoke"):
        return
    byok_dir = tmp_path / "byok"
    byok_dir.mkdir()
    # Names match support.real_config_or_skip, which treats them as the fake config.
    (byok_dir / "models.json").write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "id": "test",
                        "label": "Test",
                        "protocol": "openai-compatible",
                        "base_url": "https://test.example/v1",
                        "models": [{"id": "test-model"}],
                    }
                ],
                "bindings": {"chat": "test/test-model"},
            }
        ),
        encoding="utf-8",
    )
    (byok_dir / "secrets.json").write_text(json.dumps({"test": "test-key"}), encoding="utf-8")
    monkeypatch.setenv("AVID_BYOK_CONFIG", str(byok_dir / "models.json"))
    monkeypatch.setenv("AVID_BYOK_SECRETS", str(byok_dir / "secrets.json"))
    # Legacy identity env vars are no longer read; removing them keeps nothing picking them up.
    for name in ("AVID_API_KEY", "AVID_MODEL", "AVID_BASE_URL", "AVID_PROVIDER", "AVID_CONTEXT_WINDOW"):
        monkeypatch.delenv(name, raising=False)
    # No window probing: unit tests must not hit the provider (test_usage.py enables it with
    # an injected MockTransport).
    monkeypatch.setenv("AVID_MODEL_INFO", "off")


@pytest.fixture(autouse=True)
def avid_home(tmp_path, monkeypatch):
    """User-level dir -> tmp_path: the workspace registry must never write the real ~/.avid."""
    monkeypatch.setenv(AVID_HOME_ENV, str(tmp_path / "avid-home"))


@pytest.fixture
def hook_registry(monkeypatch):
    """A per-test hook registry: DEFAULT_HOOKS is replaced, so nothing leaks between cases."""
    from avid.agent import hooks as hooks_module
    from avid.agent.hooks import HookRegistry

    registry = HookRegistry()
    monkeypatch.setattr(hooks_module, "DEFAULT_HOOKS", registry)
    return registry


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    """Workspace root -> tmp_path so sessions, tasks and spills land in the temp dir.

    autouse: isolation must fail closed. A case that missed it would write straight into the real
    working directory (spill paths are named by in-process sequence number).
    """
    monkeypatch.setattr(workspace, "WORKSPACE_ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def store(tmp_path) -> Path:
    """A session dir with two workspace subdirs (w-alpha / w-beta)."""
    root = tmp_path / "sessions"
    for name in ("w-alpha", "w-beta"):
        (root / name).mkdir(parents=True)
    return root


@pytest.fixture
def indexer(store, tmp_path):
    """An indexer over ``store`` with its DB in tmp_path; the worker is stopped at teardown."""
    from index_cases import WORKSPACES

    from avid.index import SessionIndexer
    from avid.index import db as index_db

    conn = index_db.open_db(tmp_path / "index.sqlite")
    instance = SessionIndexer(
        conn=conn,
        roots=lambda: [store],
        lookup_workspace=lambda wid: WORKSPACES.get(wid),
        now=lambda: 1_700_000_000_000,
    )
    try:
        yield instance
    finally:
        instance.stop(flush=False)
        instance.close()
        conn.close()

"""REST field names must match in three places: the pydantic models in ``web/schemas.py``, the
TS interfaces in ``web/src/api/types.ts``, and real responses.

The frontend may neither miss a field nor declare one the server lacks, live payloads must carry
every declared field (catching handwritten svc dicts that drift from the schema), and types and
nullability are deliberately not compared.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from support import ScriptedChat, make_turn

from avid.services import Services
from avid.web import create_app
from avid.web.schemas import (
    AnswerApprovalOut,
    ApprovalOut,
    BranchListOut,
    BranchOut,
    BuildInfo,
    ByokModel,
    ByokProviderIn,
    ByokProviderOut,
    ByokSettingsIn,
    ByokSettingsOut,
    ByokTestOut,
    CacheUsageOut,
    CancelOut,
    Capabilities,
    CapabilityFlags,
    CompactionUsageOut,
    ContextPartsOut,
    ContextUsageOut,
    EntryOut,
    EntryPageOut,
    ErrorOut,
    HealthOut,
    MetaOut,
    ModelCandidate,
    PickFolderOut,
    RunCreatedOut,
    RunOut,
    SearchHitOut,
    SearchResultOut,
    SessionDetail,
    SessionsDirIn,
    SessionsDirOut,
    SessionSummary,
    SkillOut,
    StreamInfo,
    UsageOut,
    VerifyStepOut,
    WorkspaceOut,
    WorkspaceRef,
)

ROOT = Path(__file__).resolve().parents[1]
TYPES_TS = ROOT / "web" / "src" / "api" / "types.ts"

# (server model, frontend interface name); pairs whose names differ are listed explicitly.
PAIRS: list[tuple[type, str]] = [
    # sessions
    (SessionSummary, "SessionSummary"),
    (SessionDetail, "SessionDetail"),
    (EntryOut, "Entry"),
    (EntryPageOut, "EntryPage"),
    (BranchOut, "Branch"),
    (BranchListOut, "BranchList"),
    # usage ledger: four models form one set, field names equal in all three places
    (UsageOut, "UsageReport"),
    (ContextUsageOut, "ContextUsage"),
    (ContextPartsOut, "ContextParts"),
    (CacheUsageOut, "CacheUsage"),
    (CompactionUsageOut, "CompactionUsage"),
    # runs and approvals
    (RunOut, "Run"),
    (RunCreatedOut, "RunCreated"),
    (CancelOut, "CancelResult"),
    (ApprovalOut, "Approval"),
    (AnswerApprovalOut, "ApprovalAnswer"),
    # workspaces
    (WorkspaceRef, "WorkspaceRef"),
    (WorkspaceOut, "WorkspaceSummary"),
    (PickFolderOut, "PickFolderResult"),
    # meta
    (MetaOut, "Meta"),
    (Capabilities, "Capabilities"),
    (ModelCandidate, "ModelCandidate"),
    # BYOK model config: keys go in only; Out carries key_set, never api_key
    (ByokSettingsOut, "ByokSettings"),
    (ByokSettingsIn, "ByokSettingsInput"),
    (ByokProviderOut, "ProviderEntry"),
    (ByokProviderIn, "ProviderInput"),
    (ByokModel, "ModelEntry"),
    (CapabilityFlags, "CapabilityFlags"),
    (ByokTestOut, "ByokTestResult"),
    (VerifyStepOut, "VerifyStep"),
    # sessions dir: its source may be an env var, in which case editable is false
    (SessionsDirOut, "SessionsDir"),
    (SessionsDirIn, "SessionsDirInput"),
    # search: hits carry entry locators, behind says how far the index lags
    (SearchResultOut, "SearchResult"),
    (SearchHitOut, "SearchHit"),
    (StreamInfo, "StreamInfo"),
    (HealthOut, "Health"),
    (SkillOut, "Skill"),
    (BuildInfo, "BuildInfo"),
    (ErrorOut, "ErrorEnvelope"),
]


def ts_fields(interface: str, *, seen: set[str] | None = None) -> set[str]:
    """Field names of one TS interface, including ``extends`` parents."""
    seen = seen or set()
    assert interface not in seen, f"interface 继承成环：{interface}"
    seen.add(interface)
    text = TYPES_TS.read_text(encoding="utf-8")
    match = re.search(
        rf"export interface {interface}(?:\s+extends\s+(\w+))?\s*\{{(.*?)\n\}}",
        text,
        re.S,
    )
    assert match, f"前端没有 interface {interface}"
    parent, body = match.group(1), match.group(2)
    names = set(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\??\s*:", body, re.M))
    if parent:
        names |= ts_fields(parent, seen=seen)
    return names


@pytest.mark.parametrize("model, interface", PAIRS, ids=[m.__name__ for m, _ in PAIRS])
def test_dto_field_names_match_the_frontend_types(model, interface):
    """Both sides must match: a missing field is unusable, an extra one makes the type lie."""
    python_side = set(model.model_fields)
    typescript_side = ts_fields(interface)

    assert python_side - typescript_side == set(), (
        f"{interface} 缺字段：{sorted(python_side - typescript_side)}"
    )
    assert typescript_side - python_side == set(), (
        f"{interface} 多出服务端没有的字段：{sorted(typescript_side - python_side)}"
    )


@pytest.fixture
def client(sandbox):
    services = Services(
        root=sandbox / ".avid" / "sessions", chat=ScriptedChat(make_turn("答"))
    )
    try:
        yield (
            TestClient(
                create_app(services=services, static_dir=sandbox / "unbuilt"),
                base_url="http://127.0.0.1:8765",
            ),
            services,
        )
    finally:
        services.close()


def test_live_payloads_carry_every_declared_field(client):
    """Live responses must carry every declared field, catching handwritten dict vs schema drift."""
    http, _ = client
    workspace = http.get("/api/workspaces").json()["workspaces"][0]
    created = http.post("/api/sessions", json={"workspace": workspace["id"]}).json()
    session_id = created["id"]
    http.post(
        f"/api/sessions/{session_id}/runs",
        json={"prompt": "问题", "auto_approve": True},
    )

    checks = [
        (SessionSummary, http.get("/api/sessions").json()["sessions"][0]),
        (SessionDetail, http.get(f"/api/sessions/{session_id}").json()),
        (EntryPageOut, http.get(f"/api/sessions/{session_id}/entries").json()),
        # The branch list carries the usage snapshot; main is always there as the implicit default.
        (BranchListOut, http.get(f"/api/sessions/{session_id}/branches").json()),
        (
            BranchOut,
            http.get(f"/api/sessions/{session_id}/branches").json()["branches"][0],
        ),
        (WorkspaceOut, workspace),
    ]
    for model, payload in checks:
        missing = set(model.model_fields) - set(payload)
        assert not missing, f"{model.__name__} 的响应缺字段 {sorted(missing)}：{payload}"

    # RunOut: look up by run_id after a run (round/tokens values are pinned by test_run_events).
    run_id = http.get(f"/api/sessions/{session_id}").json()["active_run_id"]
    if run_id is None:
        listed = http.get("/api/sessions").json()["sessions"]
        run_id = next(item["active_run_id"] for item in listed if item["id"] == session_id)
    # The run may already be over (scripted models are fast), so query the registry entry directly.
    if run_id is not None:
        run = http.get(f"/api/runs/{run_id}")
        if run.status_code == 200:
            missing = set(RunOut.model_fields) - set(run.json())
            assert not missing, f"RunOut 的响应缺字段：{sorted(missing)}"

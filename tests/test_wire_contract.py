"""REST 线格式的字段名三处一致（A7 的扩展）。

事件类型名有 `test_event_contract.py` 守着，但 **REST DTO 一直没有机械检查**：
同一个概念在 `svc/` 里是手写 dict、在 `web/schemas.py` 里是 pydantic 模型、在
`web/src/api/types.ts` 里是 TS interface。加一个字段要改三处两种语言，全靠人记得。

漂移已经发生过两次（审查里记的）：`Capabilities` 前端缺 `workspace_picker`；
`RunOut.round/tokens` 服务端恒为 0（那是值不对，不是字段缺，字段名这条门禁抓不到
第二类——所以这里只声明它守什么）。

三处都查：
1. pydantic 模型的字段名 ⊆ TS interface 的字段名（前端不能少字段）；
2. TS interface 也不能多出服务端没有的字段（那会让类型撒谎）；
3. **真实响应**里必须带上模型声明的每个字段（抓 svc 手写 dict 与 schema 漂移）。

不做的部分：不比对类型与可空性（两套类型系统没法直接映射），也不生成代码——
DTO 就这几个，生成器会把两边的可读性都换掉。数量或变更频率上去了再考虑生成。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from support import ScriptedChat, make_turn

from avid.svc import Services
from avid.web import create_app
from avid.web.schemas import (
    AnswerApprovalOut,
    ApprovalOut,
    BranchListOut,
    BranchOut,
    BuildInfo,
    CancelOut,
    Capabilities,
    EntryOut,
    EntryPageOut,
    ErrorOut,
    HealthOut,
    MetaOut,
    PickFolderOut,
    RunCreatedOut,
    RunOut,
    SessionDetail,
    SessionSummary,
    SkillOut,
    StreamInfo,
    TaskOut,
    WorkspaceOut,
    WorkspaceRef,
)

ROOT = Path(__file__).resolve().parents[1]
TYPES_TS = ROOT / "web" / "src" / "api" / "types.ts"

# (服务端模型, 前端 interface 名)。名字不同的那几对在这里显式写出来。
PAIRS: list[tuple[type, str]] = [
    # 会话
    (SessionSummary, "SessionSummary"),
    (SessionDetail, "SessionDetail"),
    (EntryOut, "Entry"),
    (EntryPageOut, "EntryPage"),
    (BranchOut, "Branch"),
    (BranchListOut, "BranchList"),
    # 运行与审批
    (RunOut, "Run"),
    (RunCreatedOut, "RunCreated"),
    (CancelOut, "CancelResult"),
    (ApprovalOut, "Approval"),
    (AnswerApprovalOut, "ApprovalAnswer"),
    # 工作区
    (WorkspaceRef, "WorkspaceRef"),
    (WorkspaceOut, "WorkspaceSummary"),
    (PickFolderOut, "PickFolderResult"),
    # 元信息
    (MetaOut, "Meta"),
    (Capabilities, "Capabilities"),
    (StreamInfo, "StreamInfo"),
    (HealthOut, "Health"),
    (SkillOut, "Skill"),
    (BuildInfo, "BuildInfo"),
    (ErrorOut, "ErrorEnvelope"),
    # 任务
    (TaskOut, "Task"),
]


def ts_fields(interface: str, *, seen: set[str] | None = None) -> set[str]:
    """一个 TS interface 的字段名（含 `extends` 的父接口）。"""
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
    """两侧字段名必须一致：少一个前端就用不到，多一个类型就在撒谎。"""
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
    """真实响应必须带上模型声明的每个字段——抓的是 svc 手写 dict 与 schema 漂移。"""
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
        (WorkspaceOut, workspace),
    ]
    for model, payload in checks:
        missing = set(model.model_fields) - set(payload)
        assert not missing, f"{model.__name__} 的响应缺字段 {sorted(missing)}：{payload}"

    # RunOut：跑完一次之后按 run_id 查（round/tokens 的值由 test_run_events 守）。
    run_id = http.get(f"/api/sessions/{session_id}").json()["active_run_id"]
    if run_id is None:
        listed = http.get("/api/sessions").json()["sessions"]
        run_id = next(item["active_run_id"] for item in listed if item["id"] == session_id)
    # 运行可能已经结束（脚本模型很快），直接查注册表里的那条。
    if run_id is not None:
        run = http.get(f"/api/runs/{run_id}")
        if run.status_code == 200:
            missing = set(RunOut.model_fields) - set(run.json())
            assert not missing, f"RunOut 的响应缺字段：{sorted(missing)}"

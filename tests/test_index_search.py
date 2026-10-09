"""全文检索：中文（trigram）、两字词（LIKE 回落）、定位、作用域、把输入当字面量。

这组用例同时钉住「索引哪些文本」这条规则（extract.py 的取舍）——搜得到什么、
搜不到什么，都是有意为之而不是副作用。
"""

from __future__ import annotations

from index_cases import ALPHA, BETA, make_session, read_byte_range

from avid.index import queries
from avid.session import SessionRecorder


def test_finds_a_chinese_phrase_and_hands_back_a_snippet(indexer, store):
    make_session(
        store,
        session_id="s-cn",
        messages=(
            {"role": "user", "content": "帮我把会话数据搬到专用目录里"},
            {"role": "assistant", "content": "我会话存储的位置已经改好了"},
        ),
    )
    indexer.index_all()

    hits = queries.search_entries(indexer.conn, "会话数据")

    assert len(hits) == 1
    assert hits[0].session_id == "s-cn"
    assert "会话数据" in hits[0].snippet
    assert hits[0].role == "user"


def test_a_two_character_query_goes_through_the_fallback(indexer, store):
    """trigram 看不见两字词：查询侧对 <3 字回落 LIKE 扫描（中文里两字词最常见）。"""
    make_session(store, session_id="s-two", messages=({"role": "user", "content": "把索引建起来"},))
    indexer.index_all()

    hits = queries.search_entries(indexer.conn, "索引")

    assert [hit.session_id for hit in hits] == ["s-two"]


def test_a_hit_can_be_read_back_from_the_file(indexer, store):
    """命中 = 能跳回原文：会话 + 条目 id + 行偏移，全都在。"""
    file = make_session(
        store, session_id="s-jump", messages=({"role": "user", "content": "记住这句话：紫色犀牛"},)
    )
    indexer.index_all()

    hit = queries.search_entries(indexer.conn, "紫色犀牛")[0]

    record = read_byte_range(file, hit.byte_offset, hit.byte_length)
    assert record["id"] == hit.entry_id
    assert record["message"]["content"] == "记住这句话：紫色犀牛"
    assert queries.entry_location(indexer.conn, hit.session_id, hit.entry_id)["file_path"] == str(file)


def test_search_scopes_to_a_session_and_a_workspace(indexer, store):
    make_session(store, workspace=ALPHA, session_id="s-a1", messages=({"role": "user", "content": "共同话题 alpha"},))
    make_session(store, workspace=ALPHA, session_id="s-a2", messages=({"role": "user", "content": "共同话题 第二次"},))
    make_session(store, workspace=BETA, session_id="s-b1", messages=({"role": "user", "content": "共同话题 在另一个工作区"},))
    indexer.index_all()

    everything = queries.search_entries(indexer.conn, "共同话题")
    assert {hit.session_id for hit in everything} == {"s-a1", "s-a2", "s-b1"}

    one_session = queries.search_entries(indexer.conn, "共同话题", session_id="s-a2")
    assert [hit.session_id for hit in one_session] == ["s-a2"]

    one_workspace = queries.search_entries(indexer.conn, "共同话题", workspace_id=BETA)
    assert [hit.session_id for hit in one_workspace] == ["s-b1"]


def test_input_is_treated_as_literal_text(indexer, store):
    """FTS 语法不进用户手里：引号、AND、星号都只当字符（不能既报错又给错表达式）。"""
    make_session(
        store,
        session_id="s-literal",
        messages=(
            {"role": "user", "content": '我说的是 "引号里的东西" 以及 AND 这个词'},
            {"role": "assistant", "content": "星号 * 和中划线 _ 也算普通字符"},
        ),
    )
    indexer.index_all()

    assert queries.search_entries(indexer.conn, '"引号里的东西"')
    assert queries.search_entries(indexer.conn, "AND")
    assert queries.search_entries(indexer.conn, "星号 *")
    # 只有标点、没有词元：空结果而不是语法错误
    assert queries.search_entries(indexer.conn, "***") == []
    assert queries.search_entries(indexer.conn, "") == []


def test_tool_output_is_indexed_up_to_the_limit(indexer, store):
    """工具结果截前 2000 字符：够定位，不值得为全文付体积与敏感面。"""
    head, tail = "浅色开头" * 10, "深色结尾"
    make_session(
        store,
        session_id="s-tool",
        messages=(
            {"role": "user", "content": "跑个命令"},
            {"role": "tool", "tool_call_id": "c1", "content": head + "x" * 3000 + tail},
        ),
    )
    indexer.index_all()

    assert queries.search_entries(indexer.conn, "浅色开头")
    assert queries.search_entries(indexer.conn, "深色结尾") == []


def test_tool_call_arguments_are_searchable(indexer, store):
    make_session(
        store,
        session_id="s-args",
        messages=(
            {"role": "user", "content": "改一下配置"},
            {
                "role": "assistant",
                "content": "好",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "write_file", "arguments": '{"path": "pyproject.toml"}'},
                    }
                ],
            },
        ),
    )
    indexer.index_all()

    hits = queries.search_entries(indexer.conn, "pyproject.toml")

    assert [hit.session_id for hit in hits] == ["s-args"]
    assert hits[0].role == "assistant"


def test_notice_and_error_entries_are_searchable(indexer, store):
    from avid.session import JsonlSessionRepo
    from avid.session.recorder import SessionRecorder
    from avid.session.types import ERROR_ENTRY, NOTICE_ENTRY

    make_session(store, session_id="s-types", messages=({"role": "user", "content": "起个头"},))
    file = next((store / ALPHA).glob("*_s-types.jsonl"))
    repo = JsonlSessionRepo(file.parent, workspace=ALPHA)
    try:
        session = repo.open(next(item for item in repo.list() if item.id == "s-types"))
        recorder = SessionRecorder(session)
        recorder.on_message({"role": "assistant", "content": "该收尾了"}, entry_type=NOTICE_ENTRY)
        recorder.on_message({"role": "assistant", "content": "运行失败：提供方读超时"}, entry_type=ERROR_ENTRY)
    finally:
        repo.close()
    indexer.index_all()

    assert [hit.entry_type for hit in queries.search_entries(indexer.conn, "该收尾了")] == ["notice"]
    assert [hit.entry_type for hit in queries.search_entries(indexer.conn, "读超时")] == ["error"]


def test_a_session_name_is_not_searchable_text(indexer, store):
    """标题是会话级字段，不是条目文本——value 行不进 search_text（否则搜标题会命中一堆会话）。"""
    make_session(
        store,
        session_id="s-title",
        messages=({"role": "user", "content": "正文里没有那三个字"},),
        name="独一无二的标题词",
    )
    indexer.index_all()

    assert queries.get_session(indexer.conn, "s-title").title == "独一无二的标题词"
    assert queries.search_entries(indexer.conn, "独一无二的标题词") == []


def test_results_are_newest_session_first(indexer, store):
    import os

    first = make_session(store, session_id="s-old", messages=({"role": "user", "content": "同一个词 早"},))
    make_session(store, session_id="s-new", messages=({"role": "user", "content": "同一个词 晚"},))
    stat = first.stat()
    os.utime(first, ns=(stat.st_atime_ns, stat.st_mtime_ns - 10_000_000_000))
    indexer.index_all()

    hits = queries.search_entries(indexer.conn, "同一个词")

    assert [hit.session_id for hit in hits] == ["s-new", "s-old"]


def test_limit_caps_the_result_set(indexer, store):
    for index in range(5):
        make_session(
            store,
            session_id=f"s-{index}",
            messages=({"role": "user", "content": f"第 {index} 条：同样的关键词"},),
        )
    indexer.index_all()

    assert len(queries.search_entries(indexer.conn, "同样的关键词", limit=2)) == 2


def test_index_stats_counts_what_check_needs(indexer, store):
    make_session(store, session_id="s-stats", messages=({"role": "user", "content": "一"},))
    indexer.index_all()

    stats = queries.index_stats(indexer.conn)

    assert stats["sessions"] == 1
    assert stats["entries"] == 1
    assert stats["behind"] == 0
    assert stats["statuses"] == {"ok": 1}


# ---------------- 出口：CLI 与 REST（这一层只读索引，不调模型） ----------------


def test_cli_search_prints_hits_and_their_location(indexer, store, capsys, monkeypatch):
    from index_cases import WORKSPACES

    from avid import cli

    make_session(
        store,
        session_id="s-cli",
        messages=({"role": "user", "content": "把索引库放在专用目录里"},),
        name="索引演示",
    )
    monkeypatch.setattr(cli, "_session_indexer", lambda: indexer)

    assert cli.main(["session", "search", "专用目录"]) == 0

    out = capsys.readouterr().out
    assert "索引演示" in out and "alpha" in out
    assert "把索引库放在专用目录里" in out
    assert "s-cli" in out and "seq 1" in out
    assert WORKSPACES[ALPHA][1] in out


def test_cli_search_says_so_when_nothing_matches(indexer, store, capsys, monkeypatch):
    from avid import cli

    make_session(store, session_id="s-none", messages=({"role": "user", "content": "别的话"},))
    monkeypatch.setattr(cli, "_session_indexer", lambda: indexer)

    assert cli.main(["session", "search", "完全不相干的词"]) == 0
    assert "没有命中" in capsys.readouterr().out


def test_rest_search_returns_hits_and_the_backlog(tmp_path):
    from fastapi.testclient import TestClient
    from support import bound_workspace

    from avid.services import Services
    from avid.web import create_app

    services = Services(workspace_root=tmp_path)
    try:
        client = TestClient(
            create_app(services=services, static_dir=tmp_path / "unbuilt"),
            base_url="http://127.0.0.1:8765",
        )
        workspace = bound_workspace(services)
        created = client.post("/api/sessions", json={"workspace": workspace}).json()
        repo = services.workspaces.repo_for(
            next(ws for ws in services.workspaces.workspaces() if ws.id == workspace)
        )
        session = repo.open(next(item for item in repo.list() if item.id == created["id"]))
        SessionRecorder(session).on_message({"role": "user", "content": "端到端：检索得到这句话"})
        session.close()
        assert services.indexer.reconcile().indexed >= 1

        response = client.get("/api/search", params={"q": "检索得到"})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["behind"] == 0
        assert [hit["session_id"] for hit in body["hits"]] == [created["id"]]
        hit = body["hits"][0]
        assert "检索得到" in hit["snippet"]
        assert hit["entry_type"] == "message" and hit["role"] == "user"
        assert hit["byte_length"] > 0 and hit["workspace_id"] == workspace

        scoped = client.get("/api/search", params={"q": "检索得到", "session": "nope"}).json()
        assert scoped["hits"] == []
    finally:
        services.close()


def test_rest_search_rejects_an_empty_query(tmp_path):
    from fastapi.testclient import TestClient

    from avid.services import Services
    from avid.web import create_app

    services = Services(workspace_root=tmp_path)
    try:
        client = TestClient(
            create_app(services=services, static_dir=tmp_path / "unbuilt"),
            base_url="http://127.0.0.1:8765",
        )
        assert client.get("/api/search", params={"q": ""}).status_code == 422
        assert client.get("/api/search", params={"q": "x" * 501}).status_code == 422
    finally:
        services.close()


# ---------------- 评审修复（2026-10-09）：混合长短词必须是 AND ----------------


def test_a_mixed_query_requires_every_word(indexer, store):
    """长词 + 短词：两路各查一遍再合并只能得并集，与「多个词按 AND」的承诺矛盾。"""
    make_session(store, session_id="s-both", messages=({"role": "user", "content": "alpha 配置都在这儿"},))
    make_session(store, session_id="s-only-long", messages=({"role": "user", "content": "只有 alpha"},))
    make_session(store, session_id="s-only-short", messages=({"role": "user", "content": "只有配置"},))
    indexer.index_all()

    def hits_of(query: str) -> list[str]:
        # 三个会话的 updated_at 可能落在同一毫秒：只比集合，不比顺序（顺序另有用例管）。
        return sorted(hit.session_id for hit in queries.search_entries(indexer.conn, query))

    assert hits_of("alpha 配置") == ["s-both"]
    assert hits_of("alpha") == ["s-both", "s-only-long"]
    assert hits_of("配置") == ["s-both", "s-only-short"]


def test_a_compaction_summary_is_searchable(indexer, store):
    """压缩摘要在会话里只落在 value（游标值），不是条目——但它概括了被压掉的历史，得能搜到。"""
    from avid.session import JsonlSessionRepo
    from avid.session.values import branch_compaction

    make_session(store, session_id="s-compact", messages=({"role": "user", "content": "起个头"},))
    file = next((store / ALPHA).glob("*_s-compact.jsonl"))
    repo = JsonlSessionRepo(file.parent, workspace=ALPHA)
    try:
        session = repo.open(next(item for item in repo.list() if item.id == "s-compact"))
        session.set_value(
            branch_compaction("main"),
            {
                "through_seq": 1,
                "keep": 0,
                "summary": {
                    "facts": ["用户要求把索引放在 ~/.avid/index"],
                    "next": "继续做检索出口",
                },
            },
        )
    finally:
        repo.close()

    indexer.index_all()
    assert indexer.reconcile().failed == 0  # 再扫一遍：派生 id 要稳定，不能重复

    hits = queries.search_entries(indexer.conn, "检索出口")
    assert [hit.session_id for hit in hits] == ["s-compact"]
    assert hits[0].entry_type == "compaction"
    rows = queries.entries_of(indexer.conn, "s-compact")
    assert len([row for row in rows if row["type"] == "compaction"]) == 1


def test_an_image_message_is_searchable_by_its_marker_but_not_its_bytes(indexer, store):
    """Only the marker (name + mime + size) is indexed, not base64: screenshots are findable without bloating the index."""
    from avid.attachments import image_part

    part = image_part(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, name="设计稿.png")
    make_session(
        store,
        session_id="s-image",
        messages=(
            {"role": "user", "content": [{"type": "text", "text": "按这张调"}, part]},
        ),
    )
    indexer.index_all()

    assert [hit.session_id for hit in queries.search_entries(indexer.conn, "设计稿")] == ["s-image"]
    assert [hit.session_id for hit in queries.search_entries(indexer.conn, "按这张调")] == ["s-image"]
    stored = indexer.conn.execute(
        "SELECT search_text FROM entries WHERE session_id = ?", ("s-image",)
    ).fetchall()
    assert len(stored) == 1
    assert "[图片 设计稿.png image/png" in stored[0]["search_text"]
    assert part["data"] not in stored[0]["search_text"]
    assert part["data"][:24] not in stored[0]["search_text"]

"""全文检索端点：``GET /api/search``。

索引只服务检索——会话列表仍以 JSONL 为准（阶段 57 的取舍），所以这个端点是唯一的
「读索引」入口，也在这里如实报告索引有没有落后（``behind``）。

`q` 是用户随手敲的词，不是查询语言：`avid.index.queries` 会把输入拆成词元、当字面量
处理（引号/AND/星号都只是字符），所以这里不做二次校验，只限长度。
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Query, Request

from ...index.queries import index_stats, search_entries
from ...services.errors import SearchUnavailable
from ..schemas import SearchResultOut

router = APIRouter()

# 检索词上限：够长到能贴一整句，短到不会变成拒绝服务的入口。
MAX_QUERY_CHARS = 500
# 刚落库的消息也该搜得到：搜索前把通知队列排空（超时不算错，索引落后是允许的）。
SEARCH_FLUSH_SECONDS = 1.0


@router.get("/search", response_model=SearchResultOut)
def search(
    request: Request,
    q: str = Query(min_length=1, max_length=MAX_QUERY_CHARS),
    workspace: str | None = Query(default=None),
    session: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    indexer = request.app.state.services.indexer
    if indexer is None:
        raise SearchUnavailable(
            "这个进程里索引不可用（多半是索引目录写不了或库坏了）；"
            "会话本身不受影响，`avid index check` 能看原因、`avid index rebuild` 能重建。"
        )
    indexer.flush(SEARCH_FLUSH_SECONDS)
    hits = search_entries(
        indexer.conn, q, session_id=session, workspace_id=workspace, limit=limit
    )
    return {"hits": [asdict(hit) for hit in hits], "behind": index_stats(indexer.conn)["behind"]}


__all__ = ["MAX_QUERY_CHARS", "router"]

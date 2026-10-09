"""Full-text search endpoint (``GET /api/search``): the sole read-the-index entry point, reporting
how far the index lags in ``behind`` while session listing stays on JSONL.

``q`` is literal user text split into tokens by ``avid.index.queries`` (quotes, AND and asterisks
are just characters), so only its length is validated here.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Query, Request

from ...index.queries import index_stats, search_entries
from ...services.errors import SearchUnavailable
from ..schemas import SearchResultOut

router = APIRouter()

# Query cap: long enough for a whole sentence, short enough not to feed a denial-of-service.
MAX_QUERY_CHARS = 500
# Drain the queue so fresh messages are searchable; timing out is fine, lag is allowed.
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

"""在线搜索 API：用已导入的 Legado 书源检索，并登记进书库。

与本地搜索的分工
----------------
- ``GET /api/admin/books``：本地书库搜索（已有）
- ``POST /api/admin/online/search``：跨书源在线搜索（本模块）
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import online_search
from ..auth import require_admin_dep
from ..database import get_db

router = APIRouter(
    prefix="/api/admin/online",
    tags=["online"],
    dependencies=[Depends(require_admin_dep)],
)


class OnlineSearchIn(BaseModel):
    q: str = Field(min_length=1, max_length=100)
    # 不传则查询所有可用书源
    source_ids: list[int] | None = None
    page: int = Field(default=1, ge=1, le=50)
    limit: int = Field(default=10, ge=1, le=30)


class OnlineAddIn(BaseModel):
    source_id: int
    name: str = Field(min_length=1, max_length=200)
    author: str = Field(default="", max_length=100)
    book_url: str = Field(min_length=1, max_length=500)
    cover_url: str = Field(default="", max_length=500)
    intro: str = Field(default="", max_length=5000)
    kind: str = Field(default="", max_length=200)
    last_chapter: str = Field(default="", max_length=200)
    # 可能来自 "120万字" 这样的文本，交给后端解析
    word_count: int | str = 0


@router.get("/sources")
def online_sources(db: Session = Depends(get_db)) -> dict:
    """可用于在线搜索的书源列表。"""
    items = online_search.list_searchable_sources(db)
    return {"items": items, "total": len(items)}


@router.post("/search")
def online_search_api(payload: OnlineSearchIn, db: Session = Depends(get_db)) -> dict:
    """跨书源并发搜索，结果按书名+作者聚合。"""
    return online_search.search(
        db,
        payload.q,
        source_ids=payload.source_ids,
        page=payload.page,
        limit=payload.limit,
    )


@router.post("/add")
def online_add(payload: OnlineAddIn, db: Session = Depends(get_db)) -> dict:
    """把搜索结果登记进书库（只登记元数据，不抓正文）。"""
    try:
        return online_search.add_to_library(db, payload.model_dump())
    except online_search.OnlineSearchError as exc:
        raise HTTPException(400, str(exc)) from exc

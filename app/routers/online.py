"""在线搜索 API：用已导入的 Legado 书源检索，并登记进书库。

与本地搜索的分工
----------------
- ``GET /api/admin/books``：本地书库搜索（已有）
- ``POST /api/admin/online/search``：跨书源在线搜索（本模块）
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import online_reader, online_search
from ..auth import require_admin_dep
from ..database import get_db
from ..models import Book

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
    # 搜索阶段拿到的目录地址（可空，空时按书源 ruleBookInfo.tocUrl 解析）
    toc_url: str = Field(default="", max_length=1000)
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
    """把搜索结果登记进书库（只登记元数据，正文在阅读时按需抓取）。"""
    try:
        return online_search.add_to_library(db, payload.model_dump())
    except online_search.OnlineSearchError as exc:
        raise HTTPException(400, str(exc)) from exc


class OnlineTocIn(BaseModel):
    force: bool = False


@router.post("/books/{book_id}/toc")
def online_sync_toc(
    book_id: int,
    payload: OnlineTocIn | None = Body(default=None),
    db: Session = Depends(get_db),
) -> dict:
    """同步在线书目录（详情页/阅读器打开前调用；目录已存在时直接返回缓存）。"""
    book = db.get(Book, book_id)
    if book is None:
        raise HTTPException(404, "书籍不存在")
    if not online_search.is_online_book(book):
        raise HTTPException(400, "这本书不是在线书")
    try:
        result = online_reader.sync_toc(db, book, force=bool(payload and payload.force))
    except online_reader.OnlineReadError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"ok": True, "book_id": book_id, **result}


@router.post("/books/{book_id}/sources")
def online_book_sources(book_id: int, db: Session = Depends(get_db)) -> dict:
    """按书名/作者搜索其他书源，给阅读器「切换书源」列表用。"""
    book = db.get(Book, book_id)
    if book is None:
        raise HTTPException(404, "书籍不存在")
    if not online_search.is_online_book(book):
        raise HTTPException(400, "这本书不是在线书")
    try:
        result = online_reader.list_switch_sources(db, book)
    except online_reader.OnlineReadError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"ok": True, "book_id": book_id, **result}


class OnlineSwitchIn(BaseModel):
    source_id: int
    book_url: str = Field(min_length=1, max_length=1000)
    toc_url: str = Field(default="", max_length=1000)
    word_count: int = Field(default=0, ge=0)


@router.post("/books/{book_id}/switch-source")
def online_switch_source(
    book_id: int, payload: OnlineSwitchIn, db: Session = Depends(get_db)
) -> dict:
    """把在线书切换到另一个书源：重绑来源、重建目录、清空旧正文缓存。"""
    book = db.get(Book, book_id)
    if book is None:
        raise HTTPException(404, "书籍不存在")
    if not online_search.is_online_book(book):
        raise HTTPException(400, "这本书不是在线书")
    try:
        result = online_reader.switch_source(
            db,
            book,
            source_id=payload.source_id,
            book_url=payload.book_url,
            toc_url=payload.toc_url,
            word_count=payload.word_count,
        )
    except online_reader.OnlineReadError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"ok": True, "book_id": book_id, **result}

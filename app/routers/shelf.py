"""书架同步 / WebDAV 备份恢复 API。

- 备份文件：Legado 的 backup*.zip 或散放的 bookshelf.json / bookSource.json
- 恢复：书源写入 BookSource，书架写入独立的 LegadoShelfBook（不污染正文书库）
- 对照：共有 / 仅服务端 / 仅 Legado 三部分
- 合并书库：/books 同时返回本地书与「仅 Legado」在线书，供 Windows 浏览
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import backup_restore as br
from .. import webdav_settings
from ..auth import require_admin_dep
from ..database import get_db
from ..models import LegadoShelfBook
from ..serializers import resolve_base_url

router = APIRouter(
    prefix="/api/admin/shelf",
    tags=["shelf"],
    dependencies=[Depends(require_admin_dep)],
)


def _settings() -> dict:
    return webdav_settings.load()


@router.get("/status")
def shelf_status() -> dict:
    """上次恢复记录 + 当前是否已恢复过书架。"""
    state = br.load_state()
    return {"restored": bool(state), "state": state}


@router.get("/backups")
def shelf_backups() -> dict:
    """列出 WebDAV 上可用的备份文件（只读，不下载）。"""
    return br.list_backups(_settings())


@router.post("/preview")
def shelf_preview(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
) -> dict:
    """解析备份并预览三部分，不写任何数据。"""
    if not isinstance(payload, dict):
        raise HTTPException(400, "参数需为对象")
    try:
        parsed = br.parse_inputs(
            _settings(),
            files=payload.get("files"),
            contents=payload.get("contents"),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "ok": True,
        "backup_name": parsed["backup_name"],
        "files": parsed["files"],
        "warnings": parsed["warnings"],
        "parsed": {
            "sources": len(parsed["sources"]),
            "books": len(parsed["books"]),
            "source_sample": [
                {"name": s["bookSourceName"], "url": s["bookSourceUrl"]}
                for s in parsed["sources"][:10]
            ],
        },
        "compare": br.compare_preview(db, parsed["books"]),
    }


@router.post("/restore")
def shelf_restore(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
) -> dict:
    """从备份恢复书源 / 书架；返回统计与恢复后的三部分对照。"""
    if not isinstance(payload, dict):
        raise HTTPException(400, "参数需为对象")
    try:
        parsed = br.parse_inputs(
            _settings(),
            files=payload.get("files"),
            contents=payload.get("contents"),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return br.restore(
        db,
        parsed,
        restore_books=bool(payload.get("restore_books", True)),
        restore_book_sources=bool(payload.get("restore_sources", True)),
        overwrite_sources=bool(payload.get("overwrite_sources", False)),
        replace_shelf=bool(payload.get("replace_shelf", True)),
    )


@router.get("/compare")
def shelf_compare(
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> dict:
    """当前两个书架的三部分拆分。"""
    return br.compare(db, limit=limit)


@router.get("/books")
def shelf_books(
    request: Request,
    q: str | None = Query(default=None),
    origin: str = Query(default="all"),
    sort: str = Query(default="updated"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    fields: str | None = Query(default=None, max_length=500),
    read: bool = Query(default=False),
    category: str | None = Query(default=None),
    source: str | None = Query(default=None),
    status: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    scraped: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    """合并书架分页：本地书 + 仅 Legado 在线书，支持来源筛选。

    origin=all|both|server|legado；Windows 书库与网页书库共用此接口。
    """
    if origin not in ("all", "both", "server", "legado"):
        raise HTTPException(400, "origin 需为 all/both/server/legado")
    return br.merged_books(
        db,
        q=q or "",
        origin=origin,
        sort=sort,
        page=page,
        page_size=page_size,
        fields=fields,
        base=resolve_base_url(request),
        read=read,
        category=category,
        source=source,
        status=status,
        tag=tag,
        scraped=scraped,
    )


@router.post("/clear")
def shelf_clear(db: Session = Depends(get_db)) -> dict:
    """清空已恢复的 Legado 书架（不影响本地书库与书源）。"""
    rows = db.execute(select(LegadoShelfBook)).scalars().all()
    for row in rows:
        db.delete(row)
    db.commit()
    return {"deleted": len(rows)}


# —— 动态路径放在最后 ——

@router.get("/books/{shelf_id}")
def shelf_book_detail(shelf_id: int, request: Request, db: Session = Depends(get_db)) -> dict:
    """在线书详情（完整原始 JSON + 规范化字段），供 Windows 信息弹窗使用。"""
    row = db.get(LegadoShelfBook, shelf_id)
    if row is None:
        raise HTTPException(404, "书架条目不存在")
    return {**br.online_item(row, resolve_base_url(request)), "raw": row.raw_dict}


@router.delete("/books/{shelf_id}")
def shelf_book_delete(shelf_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.get(LegadoShelfBook, shelf_id)
    if row is None:
        raise HTTPException(404, "书架条目不存在")
    db.delete(row)
    db.commit()
    return {"deleted": shelf_id}


@router.post("/books/{shelf_id}/adopt")
def shelf_book_adopt(shelf_id: int, db: Session = Depends(get_db)) -> dict:
    """把手机书架里的在线书登记为服务端在线书，之后 PC/网页即可直接阅读。"""
    row = db.get(LegadoShelfBook, shelf_id)
    if row is None:
        raise HTTPException(404, "书架条目不存在")
    try:
        return br.adopt_shelf_book(db, row)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

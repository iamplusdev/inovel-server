"""书源管理接口：Legado 书源的增删改查、导入导出、校验与手机网络导入订阅。

- 管理端 /api/admin/book-sources/* 需登录会话
- 订阅端 GET /api/legado/sources?token=... 返回标准书源 JSON 数组，
  手机 Legado「书源管理 → 网络导入」直接填这个 URL
"""
from __future__ import annotations

import hmac
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Cookie, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import book_source_settings, book_source_utils
from ..auth import extract_token, require_admin_dep, verify_token
from ..config import settings
from ..database import get_db
from ..models import BookSource

router = APIRouter(
    prefix="/api/admin/book-sources",
    tags=["book-sources"],
    dependencies=[Depends(require_admin_dep)],
)
subscribe_router = APIRouter(prefix="/api/legado", tags=["legado"])

# 前端 wrapper 的控制字段，不该混进 Legado 书源原对象
_CONTROL_KEYS = {"overwrite", "remark", "probe", "exclude_id", "source", "sources", "text", "json", "url", "ids"}


def _strip_control(data: dict) -> dict:
    return {k: v for k, v in data.items() if k not in _CONTROL_KEYS}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _to_item(row: BookSource) -> dict:
    return {
        "id": int(row.id),
        "source_url": row.source_url,
        "name": row.name,
        "group_name": row.group_name,
        "source_type": int(row.source_type),
        "enabled": bool(row.enabled),
        "enabled_explore": bool(row.enabled_explore),
        "weight": int(row.weight),
        "remark": row.remark,
        "last_checked_at": row.last_checked_at,
        "last_check_ok": bool(row.last_check_ok),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _apply(row: BookSource, src: dict, *, remark: str | None = None) -> None:
    for key, value in book_source_utils.extract_fields(src).items():
        setattr(row, key, value)
    row.raw_json = json.dumps(src, ensure_ascii=False)
    if remark is not None:
        row.remark = remark[:500]
    row.updated_at = _now()


def _import_items(db: Session, items: list[dict], *, overwrite: bool = False, remark: str | None = None) -> dict:
    """批量写入；同一批次与 DB 中重复的 bookSourceUrl 按 overwrite 决定跳过/覆盖。"""
    created = updated = skipped = 0
    errors: list[str] = []
    seen: set[str] = set()
    for idx, item in enumerate(items, 1):
        try:
            src = book_source_utils.normalize_source(_strip_control(item))
        except book_source_utils.SourceParseError as exc:
            errors.append(f"第 {idx} 个：{exc}")
            continue
        url = src["bookSourceUrl"]
        if url in seen:
            skipped += 1
            continue
        seen.add(url)
        row = db.execute(select(BookSource).where(BookSource.source_url == url)).scalars().first()
        if row and not overwrite:
            skipped += 1
            continue
        if row:
            _apply(row, src)
            updated += 1
        else:
            row = BookSource(source_url=url, name=src["bookSourceName"], raw_json="{}", remark=remark or "")
            db.add(row)
            _apply(row, src)
            created += 1
    db.commit()
    return {
        "total": len(items),
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
    }


def _query_rows(
    db: Session, *, q: str = "", group: str = "", enabled: bool | None = None, ids: list[int] | None = None
) -> list[BookSource]:
    stmt = select(BookSource)
    if ids is not None:
        stmt = stmt.where(BookSource.id.in_(ids))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(BookSource.name.like(like), BookSource.source_url.like(like), BookSource.group_name.like(like)))
    if group:
        stmt = stmt.where(BookSource.group_name == group)
    if enabled is not None:
        stmt = stmt.where(BookSource.enabled.is_(enabled))
    return list(
        db.execute(stmt.order_by(BookSource.weight.desc(), BookSource.id.asc())).scalars().all()
    )


# —— 列表 / 设置 / 导出等静态路径必须注册在 /{sid} 之前 ——

@router.get("")
def list_sources(
    q: str = Query(default=""),
    group: str = Query(default=""),
    enabled: bool | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    rows = _query_rows(db, q=q, group=group, enabled=enabled)
    total = len(rows)
    start = (page - 1) * page_size
    groups = sorted({r.group_name for r in rows if r.group_name})
    return {
        "items": [_to_item(r) for r in rows[start : start + page_size]],
        "total": total,
        "page": page,
        "page_size": page_size,
        "groups": groups,
    }


@router.get("/settings")
def source_settings() -> dict:
    token = book_source_settings.ensure_token()
    cfg = book_source_settings.load()
    base = (settings.public_base_url or "").rstrip("/")
    path = f"/api/legado/sources?token={token}"
    return {
        "subscribe_token": token,
        "public_base_url": base,
        "subscribe_path": path,
        "subscribe_url": f"{base}{path}",
        "token_created_at": cfg.get("created_at", ""),
    }


@router.post("/settings/reset-token")
def reset_subscribe_token() -> dict:
    book_source_settings.reset_token()
    return source_settings()


@router.get("/export")
def export_sources(
    ids: str = Query(default=""),
    group: str = Query(default=""),
    enabled_only: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> JSONResponse:
    id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()] or None
    rows = _query_rows(db, group=group, enabled=True if enabled_only else None, ids=id_list)
    payload = [r.raw_dict for r in rows]
    filename = f"bookSources_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    return JSONResponse(
        payload,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/import")
def import_sources(payload: Any = Body(...), db: Session = Depends(get_db)) -> dict:
    """导入 Legado 书源：JSON 文本 / 数组 / 网络 URL，支持覆盖同名（bookSourceUrl）。"""
    if not isinstance(payload, dict):
        raise HTTPException(400, "导入参数需为对象")
    overwrite = bool(payload.get("overwrite", False))
    raw: Any = payload.get("sources")
    if raw is None:
        raw = payload.get("source")
    if raw is None:
        url = str(payload.get("url") or "").strip()
        text = payload.get("text")
        if text is None:
            text = payload.get("json")
        if url:
            try:
                raw = book_source_utils.fetch_text(url)
            except book_source_utils.SourceParseError as exc:
                raise HTTPException(400, str(exc)) from exc
        else:
            raw = text
    if raw is None or raw == "":
        raise HTTPException(400, "请提供书源 JSON 文本、数组或网络导入地址")
    try:
        items = book_source_utils.parse_sources(raw)
    except book_source_utils.SourceParseError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not items:
        raise HTTPException(400, "没有解析到任何书源")
    return _import_items(db, items, overwrite=overwrite)


@router.post("/validate")
def validate_sources(payload: Any = Body(...), db: Session = Depends(get_db)) -> dict:
    """校验收到的书源（不落库），或按 ids 批量校验已存书源并回写校验状态。"""
    if not isinstance(payload, dict):
        raise HTTPException(400, "校验参数需为对象")
    probe = bool(payload.get("probe", False))
    ids = payload.get("ids")
    if isinstance(ids, list) and ids:
        id_list = [int(x) for x in ids if str(x).isdigit()][:50]
        rows = db.execute(select(BookSource).where(BookSource.id.in_(id_list))).scalars().all()
        raws = [r.raw_dict for r in rows]
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda src: book_source_utils.validate_source(src, probe=probe), raws))
        for row, result in zip(rows, results):
            row.last_checked_at = _now()
            row.last_check_ok = bool(result["ok"])
        db.commit()
        return {
            "items": [
                {"id": int(r.id), "name": r.name, **res}
                for r, res in zip(rows, results)
            ]
        }
    raw = _strip_control(payload.get("source", payload))
    if not isinstance(raw, dict):
        raise HTTPException(400, "请提供要校验的书源对象")
    try:
        src = book_source_utils.normalize_source(raw)
    except book_source_utils.SourceParseError as exc:
        raise HTTPException(400, str(exc)) from exc
    duplicate: set[str] = set()
    exclude_id = payload.get("exclude_id")
    if src["bookSourceUrl"]:
        stmt = select(BookSource.source_url).where(BookSource.source_url == src["bookSourceUrl"])
        if exclude_id:
            stmt = stmt.where(BookSource.id != int(exclude_id))
        if db.execute(stmt).first():
            duplicate.add(src["bookSourceUrl"])
    return book_source_utils.validate_source(src, probe=probe, duplicate_urls=duplicate)


@router.post("/batch")
def batch_action(payload: Any = Body(...), db: Session = Depends(get_db)) -> dict:
    """批量启用 / 禁用 / 删除 / 改分组。"""
    if not isinstance(payload, dict):
        raise HTTPException(400, "批量参数需为对象")
    action = str(payload.get("action") or "")
    id_list = [int(x) for x in payload.get("ids", []) if str(x).isdigit()]
    if action not in ("enable", "disable", "delete", "group"):
        raise HTTPException(400, "不支持的批量操作")
    if not id_list:
        return {"affected": 0}
    rows = db.execute(select(BookSource).where(BookSource.id.in_(id_list))).scalars().all()
    affected = 0
    for row in rows:
        if action == "delete":
            db.delete(row)
        else:
            raw = row.raw_dict
            if action == "enable":
                row.enabled = True
                raw["enabled"] = True
            elif action == "disable":
                row.enabled = False
                raw["enabled"] = False
            elif action == "group":
                name = str(payload.get("group") or "").strip()[:100]
                row.group_name = name
                raw["bookSourceGroup"] = name
            row.raw_json = json.dumps(raw, ensure_ascii=False)
            row.updated_at = _now()
        affected += 1
    db.commit()
    return {"affected": affected}


@router.post("")
def create_source(payload: Any = Body(...), db: Session = Depends(get_db)) -> dict:
    """新增单个/多个书源（body 可为书源对象或 {sources:[...]}）。"""
    if isinstance(payload, dict):
        overwrite = bool(payload.get("overwrite", False))
        raw = payload.get("sources", payload.get("source", payload))
        remark = str(payload.get("remark") or "")[:500] or None
    else:
        overwrite, raw, remark = False, payload, None
    try:
        items = book_source_utils.parse_sources(raw)
    except book_source_utils.SourceParseError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not items:
        raise HTTPException(400, "没有解析到任何书源")
    return _import_items(db, items, overwrite=overwrite, remark=remark)


# —— 动态路径 ——

@router.get("/{sid}")
def get_source(sid: int, db: Session = Depends(get_db)) -> dict:
    row = db.get(BookSource, sid)
    if row is None:
        raise HTTPException(404, "书源不存在")
    return {**_to_item(row), "raw": row.raw_dict}


@router.put("/{sid}")
def update_source(sid: int, payload: Any = Body(...), db: Session = Depends(get_db)) -> dict:
    row = db.get(BookSource, sid)
    if row is None:
        raise HTTPException(404, "书源不存在")
    if not isinstance(payload, dict):
        raise HTTPException(400, "书源内容需为对象")
    raw = payload.get("source", payload)
    if not isinstance(raw, dict) or not raw.get("bookSourceUrl"):
        raise HTTPException(400, "请提供完整的书源 JSON（含 bookSourceUrl）")
    try:
        src = book_source_utils.normalize_source(_strip_control(raw))
    except book_source_utils.SourceParseError as exc:
        raise HTTPException(400, str(exc)) from exc
    conflict = db.execute(
        select(BookSource.id).where(BookSource.source_url == src["bookSourceUrl"], BookSource.id != sid)
    ).first()
    if conflict:
        raise HTTPException(409, "该 bookSourceUrl 已存在于其它书源")
    remark = payload.get("remark")
    _apply(row, src, remark=str(remark)[:500] if remark is not None else None)
    db.commit()
    return {**_to_item(row), "raw": row.raw_dict}


@router.patch("/{sid}")
def patch_source(sid: int, payload: Any = Body(...), db: Session = Depends(get_db)) -> dict:
    """快速改启用状态/分组/权重/备注；同步写回 raw_json，避免导出时被旧值覆盖。"""
    row = db.get(BookSource, sid)
    if row is None:
        raise HTTPException(404, "书源不存在")
    if not isinstance(payload, dict):
        raise HTTPException(400, "参数需为对象")
    raw = row.raw_dict
    if "enabled" in payload:
        value = bool(payload["enabled"])
        row.enabled = value
        raw["enabled"] = value
    if "enabled_explore" in payload:
        value = bool(payload["enabled_explore"])
        row.enabled_explore = value
        raw["enabledExplore"] = value
    if "group_name" in payload:
        value = str(payload["group_name"] or "")[:100]
        row.group_name = value
        raw["bookSourceGroup"] = value
    if "weight" in payload:
        try:
            value = int(payload["weight"])
        except (TypeError, ValueError):
            raise HTTPException(400, "weight 需为整数") from None
        row.weight = value
        raw["weight"] = value
    if "remark" in payload:
        row.remark = str(payload["remark"] or "")[:500]
    row.raw_json = json.dumps(raw, ensure_ascii=False)
    row.updated_at = _now()
    db.commit()
    return _to_item(row)


@router.delete("/{sid}")
def delete_source(sid: int, db: Session = Depends(get_db)) -> dict:
    row = db.get(BookSource, sid)
    if row is None:
        raise HTTPException(404, "书源不存在")
    db.delete(row)
    db.commit()
    return {"deleted": sid}


@router.post("/{sid}/validate")
def validate_stored_source(sid: int, payload: Any = Body(default={}), db: Session = Depends(get_db)) -> dict:
    row = db.get(BookSource, sid)
    if row is None:
        raise HTTPException(404, "书源不存在")
    probe = bool(isinstance(payload, dict) and payload.get("probe", False))
    result = book_source_utils.validate_source(row.raw_dict, probe=probe)
    row.last_checked_at = _now()
    row.last_check_ok = bool(result["ok"])
    db.commit()
    return {"id": sid, "name": row.name, **result}


# —— 手机 Legado 网络导入订阅 ——

def verify_subscribe(
    token: str | None = Query(default=None),
    key: str | None = Query(default=None),
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    x_session_token: str | None = Header(default=None),
    ainovel_session: str | None = Cookie(default=None),
) -> str:
    """三通道鉴权：订阅令牌 / READ_API_KEY / 已登录会话（便于浏览器预览）。"""
    candidate = (token or key or x_api_key or "").strip()
    expected = book_source_settings.load().get("subscribe_token", "")
    if expected and candidate and hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8")):
        return "subscribe-token"
    read_key = (settings.read_api_key or "").strip()
    if read_key and candidate and hmac.compare_digest(candidate.encode("utf-8"), read_key.encode("utf-8")):
        return "api-key"
    session = extract_token(authorization, x_session_token, ainovel_session)
    if session:
        try:
            return verify_token(session)
        except HTTPException:
            pass
    raise HTTPException(401, "书源订阅链接无效，请在书源管理页重新复制导入链接")


@subscribe_router.get("/sources")
def subscribe_sources(
    group: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _auth: str = Depends(verify_subscribe),
) -> JSONResponse:
    """返回所有启用书源的 Legado 标准 JSON 数组（根节点必须是数组）。"""
    stmt = select(BookSource).where(BookSource.enabled.is_(True))
    if group:
        stmt = stmt.where(BookSource.group_name == group)
    rows = db.execute(stmt.order_by(BookSource.weight.desc(), BookSource.id.asc())).scalars().all()
    return JSONResponse(
        [r.raw_dict for r in rows],
        headers={"Cache-Control": "no-cache", "Content-Disposition": "inline; filename=bookSources.json"},
    )

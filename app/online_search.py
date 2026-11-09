"""在线搜索：用已导入的 Legado 书源在服务端检索，并把书登记进书库。

范围约定（与产品选择一致）
--------------------------
- **搜索**：服务端执行书源的 ``searchUrl`` + ``ruleSearch``，聚合多源结果
- **加入书库**：只登记书名/作者/封面/简介/最新章节与来源链接，**不抓正文**

不抓正文的原因：抓正文要再执行 ``ruleToc`` / ``ruleContent``，并对每本书发起
成百上千次外部请求，属于独立的抓取任务；这里只保证「搜得到、登记得下、来源可追溯」。

登记去重靠 ``Book.source_path = "online:<书籍URL>"``：同一本书重复加入直接返回原 id，
不会在书库里堆出多条记录。
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import legado_rules
from .models import Book, BookSource

# 单次查询最多并发查询的书源数：太多会拖长响应且更容易触发站点风控
MAX_SOURCES_PER_QUERY = 12
DEFAULT_TIMEOUT = 8.0
DEFAULT_PER_SOURCE_LIMIT = 10
DEFAULT_MAX_WORKERS = 4
# 在线书的来源标记前缀，与本地 TXT 的相对路径互不冲突
ONLINE_PREFIX = "online:"


class OnlineSearchError(ValueError):
    """在线搜索参数或状态错误。"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_word_count(text: Any) -> int:
    """把「120万字」「1.2万」「800千」之类解析成整数。"""
    raw = str(text or "")
    match = re.search(r"(\d+(?:\.\d+)?)\s*([万wWkK千]?)", raw)
    if not match:
        return 0
    try:
        value = float(match.group(1))
    except ValueError:
        return 0
    unit = match.group(2)
    if unit in ("万", "w", "W"):
        value *= 10000
    elif unit in ("千", "k", "K"):
        value *= 1000
    return max(0, int(value))


def list_searchable_sources(db: Session) -> list[dict]:
    """可参与在线搜索的书源（启用 + 文本类型 + 配了 searchUrl）。"""
    stmt = select(BookSource).where(
        BookSource.enabled.is_(True),
        BookSource.source_type == 0,
    )
    rows = db.execute(stmt.order_by(BookSource.weight.desc(), BookSource.id.asc())).scalars().all()
    out: list[dict] = []
    for row in rows:
        raw = row.raw_dict
        search_url = str(raw.get("searchUrl") or "").strip()
        if not search_url:
            continue
        out.append(
            {
                "id": int(row.id),
                "name": row.name,
                "group": row.group_name,
                "source_url": row.source_url,
                "has_rules": bool((raw.get("ruleSearch") or {}).get("bookList")),
                "last_check_ok": bool(row.last_check_ok),
            }
        )
    return out


def _run_source(
    source_id: int,
    source_name: str,
    source_url: str,
    raw: dict,
    keyword: str,
    *,
    page: int,
    limit: int,
    timeout: float,
) -> tuple[int, str, str, list[dict], str]:
    """在单个书源上搜索，异常收敛成错误串，不向上冒泡。"""
    try:
        items = legado_rules.search_source(
            raw,
            keyword,
            page=page,
            timeout=timeout,
            limit=limit,
        )
        return source_id, source_name, source_url, items, ""
    except legado_rules.RuleError as exc:
        return source_id, source_name, source_url, [], str(exc)
    except Exception as exc:  # noqa: BLE001 单个书源不能拖垮整体
        return source_id, source_name, source_url, [], f"{type(exc).__name__}: {exc}"


def search(
    db: Session,
    keyword: str,
    *,
    source_ids: list[int] | None = None,
    page: int = 1,
    limit: int = DEFAULT_PER_SOURCE_LIMIT,
    timeout: float = DEFAULT_TIMEOUT,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> dict:
    """并发查询多个书源，按「书名+作者」聚合去重。"""
    keyword = (keyword or "").strip()
    if not keyword:
        return {"q": "", "items": [], "sources": [], "total": 0}

    stmt = select(BookSource).where(
        BookSource.enabled.is_(True),
        BookSource.source_type == 0,
    )
    if source_ids:
        stmt = stmt.where(BookSource.id.in_(source_ids))
    rows = db.execute(stmt.order_by(BookSource.weight.desc(), BookSource.id.asc())).scalars().all()

    # 先在主线程把 raw 字典取出来：SQLAlchemy 的 session 不能跨线程使用
    picked: list[tuple[BookSource, dict]] = []
    for row in rows:
        raw = row.raw_dict
        if str(raw.get("searchUrl") or "").strip():
            picked.append((row, raw))
        if len(picked) >= MAX_SOURCES_PER_QUERY:
            break

    if not picked:
        return {"q": keyword, "items": [], "sources": [], "total": 0, "hint": "没有可用的书源"}

    status = [
        {"id": int(row.id), "name": row.name, "count": 0, "error": ""} for row, _ in picked
    ]
    by_id = {item["id"]: item for item in status}

    results: list[tuple[int, str, str, list[dict], str]] = []
    workers = max(1, min(max_workers, len(picked)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(
                _run_source,
                int(row.id),
                row.name,
                row.source_url,
                raw,
                keyword,
                page=max(1, page),
                limit=max(1, limit),
                timeout=timeout,
            )
            for row, raw in picked
        ]
        for future in as_completed(futures):
            results.append(future.result())

    merged: dict[str, dict] = {}
    for source_id, source_name, source_url, items, error in results:
        slot_status = by_id.get(source_id)
        if slot_status is not None:
            slot_status["count"] = len(items)
            slot_status["error"] = error
        for item in items:
            key = f"{item.get('name', '')}|{item.get('author', '')}"
            if not item.get("name"):
                continue
            entry = merged.get(key)
            if entry is None:
                entry = {
                    "key": key,
                    "name": item.get("name", ""),
                    "author": item.get("author", ""),
                    "cover_url": "",
                    "intro": "",
                    "kind": "",
                    "last_chapter": "",
                    "word_count": 0,
                    "sources": [],
                }
                merged[key] = entry
            # 用首个有值的来源补全公共字段
            for field in ("cover_url", "intro", "kind", "last_chapter"):
                if not entry[field] and item.get(field):
                    entry[field] = item[field]
            if not entry["word_count"]:
                entry["word_count"] = _parse_word_count(item.get("word_count"))
            entry["sources"].append(
                {
                    "source_id": source_id,
                    "source_name": source_name,
                    "source_url": source_url,
                    "book_url": item.get("book_url", ""),
                    "toc_url": item.get("toc_url", ""),
                    "cover_url": item.get("cover_url", ""),
                    "intro": item.get("intro", ""),
                    "kind": item.get("kind", ""),
                    "last_chapter": item.get("last_chapter", ""),
                }
            )

    items = sorted(merged.values(), key=lambda x: (-len(x["sources"]), x["name"]))
    return {
        "q": keyword,
        "items": items,
        "sources": status,
        "total": len(items),
        "failed": sum(1 for s in status if s["error"]),
    }


def _save_cover(book: Book, url: str) -> None:
    """下载封面并记到 cover_file；任何失败都静默跳过，不影响登记结果。"""
    from .config import settings
    from .scrapers import http_util

    if not url or not http_util.is_public_http_url(url):
        return
    try:
        data, ctype = http_util.http_get_bytes(url, timeout=8)
    except Exception:  # noqa: BLE001
        return
    if not data or len(data) > 3 * 1024 * 1024:
        return
    lowered = (ctype or "").lower()
    if "png" in lowered:
        ext = ".png"
    elif "webp" in lowered:
        ext = ".webp"
    elif "gif" in lowered:
        ext = ".gif"
    else:
        ext = ".jpg"
    name = f"{int(book.id)}_online{ext}"
    try:
        path = settings.covers_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    except OSError:
        return
    book.cover_file = name


def add_to_library(db: Session, payload: dict) -> dict:
    """把在线搜索结果登记进书库（不抓正文），同一书籍地址幂等。"""
    name = str(payload.get("name") or "").strip()
    if not name:
        raise OnlineSearchError("书名不能为空")
    book_url = str(payload.get("book_url") or "").strip()
    if not book_url:
        raise OnlineSearchError("缺少书籍地址（book_url）")

    source_id = payload.get("source_id")
    try:
        source_pk = int(source_id)
    except (TypeError, ValueError):
        raise OnlineSearchError("缺少书源 ID") from None
    source_row = db.get(BookSource, source_pk)
    if source_row is None:
        raise OnlineSearchError("书源不存在或已被删除")

    marker = ONLINE_PREFIX + book_url[:490]
    existing = db.execute(select(Book).where(Book.source_path == marker)).scalar_one_or_none()
    if existing is not None:
        return {
            "book_id": int(existing.id),
            "created": False,
            "title": existing.title,
            "hint": "该书已在书库中",
        }

    now = _now()
    book = Book(
        title=name[:200],
        author=(str(payload.get("author") or "").strip() or "佚名")[:100],
        # 不用书源的 kind 猜分类：格式五花八门，会污染现有分类树，交给用户自己归
        category="未分类",
        intro=str(payload.get("intro") or "").strip(),
        status="未知",
        tags="",
        cover_file="",
        source_path=marker,
        source_hash="",
        word_count=_parse_word_count(payload.get("word_count")),
        chapter_count=0,
        latest_chapter=str(payload.get("last_chapter") or "").strip()[:200],
        source=source_row.name[:20],
        source_id=book_url[:64],
        read_percent=0,
        read_chapter_index=-1,
        read_at="",
        created_at=now,
        updated_at=now,
    )
    db.add(book)
    db.commit()
    db.refresh(book)

    cover_url = str(payload.get("cover_url") or "").strip()
    if cover_url:
        _save_cover(book, cover_url)
        try:
            db.commit()
        except Exception:  # noqa: BLE001 封面失败不影响书籍登记
            db.rollback()

    return {
        "book_id": int(book.id),
        "created": True,
        "title": book.title,
        "hint": "已加入书库；正文需在原书源阅读",
    }


def is_online_book(book: Book) -> bool:
    """是否为「在线登记」的书（没有本地正文）。"""
    return str(book.source_path or "").startswith(ONLINE_PREFIX)

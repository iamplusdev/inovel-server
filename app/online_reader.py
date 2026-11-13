"""在线书阅读：把书源搜索登记的书的目录/正文接到现有阅读链路上。

设计
----
- 目录：首次打开时执行书源的 ``ruleBookInfo.tocUrl`` / ``ruleToc``，落成普通
  ``Chapter`` 行（``remote_url`` 记录原站地址），之后阅读器与进度同步都按本地章节处理
- 正文：读到哪章抓哪章，清洗后追加进 ``data/contents/{book_id}.bin`` 正文包，
  写回 ``content_offset/content_length``；同一章只抓一次，之后与本地书完全一致
- 失败：目录/正文抓不到时抛 :class:`OnlineReadError`，由路由转成 502，
  绝不在正文包里留下空章节，避免「看起来读过、其实没内容」

这样 Web 阅读器、PC 阅读器、Legado 读接口都不需要单独的「在线书」分支。
"""
from __future__ import annotations

import re
import threading
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import legado_rules
from .content_store import append_chapter_text, delete_pack
from .models import Book, BookSource, Chapter
from .online_search import ONLINE_PREFIX, is_online_book

# 目录页 + 章节页的网络超时：留出余量，保证不超过 PC 客户端 15s 的请求上限
TOC_TIMEOUT = 10.0
CONTENT_TIMEOUT = 12.0
# 换源搜索：书源多、站点慢，收紧单源超时并提高并发，整体控制在 10s 左右
SWITCH_SEARCH_TIMEOUT = 5.0
SWITCH_SEARCH_WORKERS = 6
SWITCH_SEARCH_LIMIT = 6


class OnlineReadError(RuntimeError):
    """在线书目录/正文无法获取（书源缺失、规则不支持、站点不可达等）。"""


_LOCKS: dict[int, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _book_lock(book_id: int) -> threading.RLock:
    with _LOCKS_GUARD:
        lock = _LOCKS.get(book_id)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[book_id] = lock
        return lock


def book_url(book: Book) -> str:
    """在线书登记时保存的原站书籍地址（长地址优先用完整字段）。"""
    full = str(getattr(book, "remote_book_url", "") or "").strip()
    if full:
        return full
    path = str(book.source_path or "")
    if not path.startswith(ONLINE_PREFIX):
        return ""
    return path[len(ONLINE_PREFIX) :]


def resolve_source(db: Session, book: Book) -> BookSource:
    """找到在线书对应的书源。

    新登记的书带 ``remote_source_id``；早期登记的书没有，按书源名回退匹配。
    书源被删除/改名时给出明确提示，而不是静默返回空正文。
    """
    if book.remote_source_id:
        row = db.get(BookSource, int(book.remote_source_id))
        if row is not None:
            return row

    name = str(book.source or "").strip()
    if not name:
        raise OnlineReadError("这本书没有记录来源书源，无法读取；请删除后重新搜索加入")
    from .database import escape_like

    rows = db.execute(
        select(BookSource)
        .where(BookSource.name.like(f"{escape_like(name)}%", escape="\\"))
        .order_by(BookSource.enabled.desc(), BookSource.weight.desc(), BookSource.id.asc())
    ).scalars().all()
    picked = None
    for row in rows:
        raw = row.raw_dict
        if str(raw.get("bookSourceUrl") or "").strip():
            picked = row
            break
    if picked is None:
        raise OnlineReadError("来源书源已被删除或改名，无法读取正文；请重新导入该书源")
    book.remote_source_id = int(picked.id)
    return picked


def _chapter_rows(db: Session, book_id: int) -> list[Chapter]:
    return list(
        db.execute(
            select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.index)
        ).scalars().all()
    )


def _chapter_row(book_id: int, index: int, item: dict) -> Chapter:
    title = str(item.get("title") or "").strip()[:200]
    return Chapter(
        book_id=book_id,
        index=index,
        title=title or f"第 {index + 1} 章",
        content="",
        remote_url=str(item.get("url") or "").strip()[:1000],
    )


def sync_toc(db: Session, book: Book, *, force: bool = False) -> dict:
    """抓取远程目录并落库；已有目录且 ``force=False`` 时直接返回缓存。"""
    if not is_online_book(book):
        raise OnlineReadError("这本书不是在线书")
    with _book_lock(int(book.id)):
        existing = _chapter_rows(db, int(book.id))
        if existing and not force:
            return {"synced": False, "total": len(existing), "cached": True}

        url = book_url(book)
        if not url:
            raise OnlineReadError("这本书缺少原站地址，无法获取目录")

        source = resolve_source(db, book)
        toc_url = str(book.remote_toc_url or "").strip()
        try:
            items = legado_rules.toc_source(
                source.raw_dict,
                url,
                book_name=book.title,
                book_author=book.author,
                toc_url=toc_url,
                timeout=TOC_TIMEOUT,
            )
        except legado_rules.RuleError as exc:
            # 搜索阶段拿到的 tocUrl 有时其实是详情页：解析不到章节时再按书源规则找一次
            if not toc_url or "没有解析到章节" not in str(exc):
                db.rollback()
                raise OnlineReadError(f"获取目录失败：{exc}") from exc
            try:
                items = legado_rules.toc_source(
                    source.raw_dict,
                    url,
                    book_name=book.title,
                    book_author=book.author,
                    timeout=TOC_TIMEOUT,
                )
            except legado_rules.RuleError as retry_exc:
                db.rollback()
                raise OnlineReadError(f"获取目录失败：{retry_exc}") from retry_exc

        by_index = {int(ch.index): ch for ch in existing}
        for index, item in enumerate(items):
            title = str(item.get("title") or "").strip()[:200]
            remote = str(item.get("url") or "").strip()[:1000]
            row = by_index.get(index)
            if row is None:
                db.add(_chapter_row(int(book.id), index, item))
            else:
                row.title = title or row.title
                row.remote_url = remote

        # 远端目录变短时清掉尾部未缓存正文的章节；已抓过的正文保留，避免丢内容
        for row in existing:
            if int(row.index) >= len(items) and not int(row.content_length or 0):
                db.delete(row)
        db.flush()

        total = int(
            db.execute(
                select(func.count()).select_from(Chapter).where(Chapter.book_id == book.id)
            ).scalar_one()
        )
        book.chapter_count = total
        book.latest_chapter = str(items[-1].get("title") or "")[:200]
        db.commit()
        return {"synced": True, "total": total, "cached": False}


def ensure_toc(db: Session, book: Book) -> int:
    """阅读器首次取目录时调用：没有章节就同步一次。"""
    if not is_online_book(book):
        return 0
    rows = int(
        db.execute(
            select(func.count()).select_from(Chapter).where(Chapter.book_id == book.id)
        ).scalar_one()
    )
    if rows:
        return rows
    return int(sync_toc(db, book).get("total") or 0)


def _normalize(value: str) -> str:
    """书名比对：去掉空白、标点与书名号，避免「斗破苍穹（精校版）」差异。"""
    return re.sub(
        r"[\s《》〈〉「」『』【】\[\]()（）·・:：,，.。!！?？、/\\|+—_\-~～]+",
        "",
        str(value or ""),
    ).lower()


def _authors_match(left: str, right: str) -> bool:
    a, b = _normalize(left), _normalize(right)
    if not a or not b:
        return True
    placeholders = {"佚名", "未知", "作者不详"}
    if a in placeholders or b in placeholders:
        return True
    return a == b or a in b or b in a


def list_switch_sources(db: Session, book: Book) -> dict:
    """按书名/作者搜索其他书源，返回可切换的同书候选。"""
    if not is_online_book(book):
        raise OnlineReadError("这本书不是在线书")
    title = str(book.title or "").strip()
    if not title:
        raise OnlineReadError("书名缺失，无法搜索其他书源")

    from .online_search import search as search_online

    result = search_online(
        db,
        title,
        limit=SWITCH_SEARCH_LIMIT,
        timeout=SWITCH_SEARCH_TIMEOUT,
        max_workers=SWITCH_SEARCH_WORKERS,
    )
    target = _normalize(title)
    current_id = int(book.remote_source_id or 0)
    strict: list[dict] = []
    loose: list[dict] = []
    seen: set[tuple[int, str]] = set()

    for item in result.get("items") or []:
        name = _normalize(str(item.get("name") or ""))
        if not name or not _authors_match(str(book.author or ""), str(item.get("author") or "")):
            continue
        for ref in item.get("sources") or []:
            url = str(ref.get("book_url") or "").strip()
            if not url:
                continue
            try:
                source_id = int(ref.get("source_id") or 0)
            except (TypeError, ValueError):
                continue
            key = (source_id, url)
            if key in seen:
                continue
            seen.add(key)
            candidate = {
                "source_id": source_id,
                "source_name": str(ref.get("source_name") or ""),
                "book_url": url[:1000],
                "toc_url": str(ref.get("toc_url") or "")[:1000],
                "last_chapter": str(ref.get("last_chapter") or "")[:200],
                "kind": str(ref.get("kind") or ""),
                "word_count": int(item.get("word_count") or 0),
                "current": source_id == current_id,
            }
            if name == target:
                strict.append(candidate)
            elif target and min(len(name), len(target)) >= 2 and (
                target in name or name in target
            ):
                loose.append(candidate)

    items = strict or loose
    # 当前书源放最后：列表先展示真正能换过去的目标
    items.sort(key=lambda c: (c["current"], c["source_name"]))
    return {
        "current": {"source_id": current_id, "source_name": str(book.source or "")},
        "items": items,
        "total": len(items),
        "failed": int(result.get("failed") or 0),
        "hint": "" if items else "其他书源没有搜到这本书；可稍后重试，或确认书源已启用",
    }


def switch_source(
    db: Session,
    book: Book,
    *,
    source_id: int,
    book_url: str,
    toc_url: str = "",
    word_count: int = 0,
) -> dict:
    """把在线书重绑到另一个书源：先取到新目录再原子替换，失败不改动原书。"""
    if not is_online_book(book):
        raise OnlineReadError("这本书不是在线书")
    try:
        source_pk = int(source_id)
    except (TypeError, ValueError):
        raise OnlineReadError("目标书源无效") from None

    source = db.get(BookSource, source_pk)
    if source is None:
        raise OnlineReadError("目标书源不存在或已被删除")
    if not source.enabled:
        raise OnlineReadError("目标书源已停用")
    raw = source.raw_dict
    if not (raw.get("ruleToc") or {}).get("chapterList"):
        raise OnlineReadError("目标书源缺少目录规则，无法切换")

    url = str(book_url or "").strip()[:1000]
    if not re.match(r"^https?://", url, re.I):
        raise OnlineReadError("目标书源没有返回有效的书籍地址")
    toc = str(toc_url or "").strip()[:1000]

    with _book_lock(int(book.id)):
        # 先抓新目录：拿不到就保持原书源，不破坏当前阅读
        try:
            items = legado_rules.toc_source(
                raw,
                url,
                book_name=book.title,
                book_author=book.author,
                toc_url=toc,
                timeout=TOC_TIMEOUT,
            )
        except legado_rules.RuleError as exc:
            db.rollback()
            raise OnlineReadError(f"切换书源失败：{exc}") from exc

        # 按序号原地更新，保留章节 id（进度/本地位置仍对得上）；正文一律作废重抓
        by_index = {int(ch.index): ch for ch in _chapter_rows(db, int(book.id))}
        for index, item in enumerate(items):
            row = by_index.get(index)
            if row is None:
                db.add(_chapter_row(int(book.id), index, item))
                continue
            row.title = str(item.get("title") or "").strip()[:200] or row.title
            row.remote_url = str(item.get("url") or "").strip()[:1000]
            row.content = ""
            row.content_offset = 0
            row.content_length = 0
            row.content_chars = 0
        for row in by_index.values():
            if int(row.index) >= len(items):
                db.delete(row)
        book.remote_source_id = source_pk
        book.remote_book_url = url
        book.remote_toc_url = toc
        book.source_path = ONLINE_PREFIX + url[:490]
        book.source = str(source.name or "")[:20]
        book.source_id = url[:64]
        book.chapter_count = len(items)
        book.latest_chapter = str(items[-1].get("title") or "")[:200]
        if int(word_count or 0) > 0:
            book.word_count = int(word_count)
        db.commit()

    # 旧正文属于原书源，换源后缓存全部作废，避免章节与正文错配
    delete_pack(int(book.id))
    return {
        "source_id": source_pk,
        "source_name": str(source.name or ""),
        "total": len(items),
    }


def chapter_text(db: Session, chapter: Chapter) -> str:
    """按偏移读正文（与解析接口共用同一套正文包）。"""
    from .content_store import read_chapter_text

    return read_chapter_text(
        int(chapter.book_id),
        int(chapter.content_offset or 0),
        int(chapter.content_length or 0),
        legacy_content=chapter.content,
    )


def ensure_chapter_content(
    db: Session, book: Book, chapter: Chapter, *, timeout: float = CONTENT_TIMEOUT
) -> str:
    """在线章节尚未缓存正文时抓取并写入正文包；已缓存则直接返回。"""
    if not is_online_book(book):
        return ""
    cached = chapter_text(db, chapter)
    if cached:
        return cached
    if not str(chapter.remote_url or "").strip():
        raise OnlineReadError("该章节没有原站地址，请刷新目录后重试")

    with _book_lock(int(book.id)):
        # 等锁期间可能已被其它请求抓取完成，刷新后再判断一次
        db.refresh(chapter)
        cached = chapter_text(db, chapter)
        if cached:
            return cached

        source = resolve_source(db, book)
        try:
            text = legado_rules.content_source(
                source.raw_dict,
                str(chapter.remote_url),
                book_url=book_url(book),
                chapter_title=str(chapter.title or ""),
                timeout=timeout,
            )
        except legado_rules.RuleError as exc:
            db.rollback()
            raise OnlineReadError(f"抓取正文失败：{exc}") from exc

        offset, byte_len, char_len = append_chapter_text(int(book.id), text)
        chapter.content_offset = offset
        chapter.content_length = byte_len
        chapter.content_chars = char_len
        chapter.content = ""
        book.word_count = int(book.word_count or 0) + char_len
        db.commit()
        return text


def online_item(book: Book) -> dict[str, Any]:
    """管理端展示用：在线书的来源信息。"""
    return {
        "online": is_online_book(book),
        "book_url": book_url(book),
        "toc_url": str(book.remote_toc_url or ""),
        "source_id": int(book.remote_source_id or 0),
        "source_name": str(book.source or ""),
        "chapter_count": int(book.chapter_count or 0),
    }

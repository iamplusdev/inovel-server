"""与 Legado（开源阅读）互通的阅读进度同步。

Legado 的进度文件：{WebDAV}/{dir}/bookProgress/{书名}_{作者}.json
字段：name / author / durChapterIndex / durChapterPos / durChapterTime / durChapterTitle
- durChapterIndex 为 0 基章节序号
- durChapterPos 为章内字符位置
- durChapterTime 为毫秒时间戳
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from urllib.parse import quote, unquote

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import webdav_settings
from .models import Book, Chapter
from .webdav import WebDavClient, WebDavError

logger = logging.getLogger("sync")

# Legado 的 normalizeFileName：把非法文件名字符替换为下划线
_BAD_FILENAME = re.compile(r'[\\/:*?"<>|]')


def progress_filename(name: str, author: str) -> str:
    """生成与 Legado 一致的文件名（URL 编码后的 {书名}_{作者}.json）。"""
    safe = _BAD_FILENAME.sub("_", f"{name or ''}_{author or ''}")
    return quote(safe, safe="") + ".json"


def _dir_url(base_url: str, dir_name: str) -> str:
    segs = [quote(p, safe="") for p in (dir_name or "").split("/") if p]
    base = (base_url or "").rstrip("/")
    return f"{base}/{'/'.join(segs)}" if segs else base


def progress_dir_url(settings: dict) -> str:
    return f"{_dir_url(settings['url'], settings['dir'])}/bookProgress/"


def progress_url(settings: dict, filename: str) -> str:
    return f"{progress_dir_url(settings)}{filename}"


def _mkcol_chain(client: WebDavClient, settings: dict) -> None:
    """逐级创建目录（支持 dir 含多级路径），再创建 bookProgress。"""
    current = (settings["url"] or "").rstrip("/")
    for seg in [p for p in (settings["dir"] or "").split("/") if p]:
        current += "/" + quote(seg, safe="")
        client.mkcol(current)
    client.mkcol(f"{current}/bookProgress")


def _client(settings: dict) -> WebDavClient:
    return WebDavClient(
        base_url=settings["url"],
        username=settings["username"],
        password=settings["password"],
        timeout=float(settings["timeout"]),
        verify_tls=bool(settings.get("verify_tls", True)),
    )


def _to_int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _parse_remote(raw: bytes) -> dict | None:
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return {
        "name": str(data.get("name") or ""),
        "author": str(data.get("author") or ""),
        "durChapterIndex": _to_int(data.get("durChapterIndex"), -1),
        "durChapterPos": max(0, _to_int(data.get("durChapterPos"), 0)),
        "durChapterTime": max(0, _to_int(data.get("durChapterTime"), 0)),
        "durChapterTitle": str(data.get("durChapterTitle") or ""),
    }


def _iso_to_ms(value: str) -> int:
    try:
        return int(datetime.fromisoformat(value or "").timestamp() * 1000)
    except (TypeError, ValueError):
        return 0


def _ms_to_iso(ms: int) -> str:
    try:
        return datetime.fromtimestamp(ms / 1000).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return datetime.now().isoformat(timespec="seconds")


def _norm_title(title: str) -> str:
    return re.sub(r"\s+", "", (title or "")).lower()


def resolve_chapter(chapters: list[Chapter], remote: dict) -> Chapter | None:
    """章节匹配：标题优先，序号兜底（Legado 与本地章节顺序可能不同）。"""
    idx = int(remote.get("durChapterIndex", -1))
    title = _norm_title(str(remote.get("durChapterTitle") or ""))
    if title:
        matches = [c for c in chapters if _norm_title(c.title) == title]
        if matches:
            if 0 <= idx < len(chapters):
                return min(matches, key=lambda c: abs(c.index - idx))
            return matches[0]
    if 0 <= idx < len(chapters):
        return chapters[idx]
    return None


def _chapters(db: Session, book_id: int) -> list[Chapter]:
    return (
        db.execute(
            select(Chapter)
            .where(Chapter.book_id == book_id)
            .order_by(Chapter.index, Chapter.id)
        )
        .scalars()
        .all()
    )


def pull_progress(
    db: Session,
    book_id: int,
    *,
    client: WebDavClient | None = None,
    settings: dict | None = None,
) -> dict:
    """打开阅读页时拉取远端进度；远端更新则写入本地并返回目标章节。

    client/settings 可由批量同步传入复用（一次认证、一次配置读取）。
    """
    settings = settings or webdav_settings.load()
    if not webdav_settings.is_configured(settings):
        return {"enabled": False, "updated": False}

    book = db.get(Book, book_id)
    if book is None:
        raise ValueError("书籍不存在")

    filename = progress_filename(book.title, book.author)
    try:
        raw = (client or _client(settings)).get(progress_url(settings, filename))
    except Exception as exc:  # noqa: BLE001
        # 网络/证书/URL 异常统一降级为「本次不更新」，不阻断阅读
        logger.warning("拉取远程进度失败 book=%s: %s", book_id, exc)
        return {
            "enabled": True,
            "found": False,
            "updated": False,
            "filename": filename,
            "error": str(exc),
        }

    if raw is None:
        return {"enabled": True, "found": False, "updated": False, "filename": filename}

    remote = _parse_remote(raw)
    if remote is None:
        return {
            "enabled": True,
            "found": True,
            "updated": False,
            "filename": filename,
            "error": "远端进度格式无法识别",
        }

    chapters = _chapters(db, book_id)
    chapter = resolve_chapter(chapters, remote)
    base = {
        "enabled": True,
        "found": True,
        "filename": filename,
        "remote_chapter_index": remote["durChapterIndex"],
        "remote_chapter_title": remote["durChapterTitle"],
        "remote_time": remote["durChapterTime"],
    }
    if chapter is None:
        return {**base, "updated": False, "error": "无法匹配本地章节"}

    remote_ms = remote["durChapterTime"]
    local_ms = _iso_to_ms(book.read_at)
    if remote_ms and local_ms and remote_ms <= local_ms:
        return {
            **base,
            "updated": False,
            "reason": "本地进度更新",
            "chapter_id": chapter.id,
            "chapter_index": chapter.index,
        }
    if not remote_ms:
        # 没有时间戳时只允许向前，避免无依据回退
        if remote["durChapterIndex"] <= book.read_chapter_index:
            return {
                **base,
                "updated": False,
                "reason": "远端无时间戳且未前进",
                "chapter_id": chapter.id,
                "chapter_index": chapter.index,
            }
        remote_ms = int(time.time() * 1000)

    total = len(chapters)

    def _apply() -> None:
        # apply() 可能在 FTS 自愈回滚后被重放，字段赋值必须放在闭包内
        book.read_chapter_index = int(chapter.index)
        book.read_percent = int(round((chapter.index + 1) * 100 / total)) if total else 0
        book.read_at = _ms_to_iso(remote_ms)

    from .database import commit_with_fts_recovery

    commit_with_fts_recovery(db, _apply)

    return {
        **base,
        "updated": True,
        "chapter_id": chapter.id,
        "chapter_index": chapter.index,
        "chapter_title": chapter.title,
        "chapter_pos": remote["durChapterPos"],
    }


def pull_all(db: Session, limit: int = 300) -> dict:
    """书库打开时批量拉取 WebDAV 进度（两端按需调用，不做服务端定时同步）。

    范围严格限定为「书库（书架）中已存在的书籍」：手机 Legado 的书架通常比
    服务端书库大，WebDAV 里会有大量书库外书籍的进度文件，这些文件一律忽略
    —— 不创建书籍、不写进度、不计入错误，继续阅读/最新阅读也不会出现它们。

    流程：一次 PROPFIND 列出 bookProgress 下的 .json 进度文件 → 按
    {书名}_{作者}.json 精确匹配书库 → 逐本复用 pull_progress 的「最新者胜」。
    返回 remote_files/matched/ignored/ignored_sample 便于排查「为什么某本书
    没同步」（是书库外，还是文件名对不上）。

    WebDAV 不支持列目录时降级为「只扫描已有本地进度的书」，同样只涉及书库内书籍。
    """
    empty = {
        "enabled": False,
        "listed": False,
        "remote_files": 0,
        "matched": 0,
        "ignored": 0,
        "ignored_sample": [],
        "scanned": 0,
        "updated": 0,
        "errors": 0,
        "books": [],
    }
    settings = webdav_settings.load()
    if not webdav_settings.is_configured(settings):
        return empty

    books = db.execute(select(Book)).scalars().all()
    by_filename: dict[str, Book] = {}
    for book in books:
        # progress_filename 做过 URL 编码；目录响应已解码，统一到解码后的名字比较
        by_filename[unquote(progress_filename(book.title, book.author))] = book

    client = _client(settings)
    listed = True
    remote_files = 0
    ignored = 0
    ignored_sample: list[str] = []
    candidates: list[Book] = []
    try:
        names = client.list_files(progress_dir_url(settings))
        remote_files = len(names)
        # 书库外的进度文件：只统计文件名用于诊断，不 GET、不写库、不报错
        unmatched = [n for n in names if n not in by_filename]
        ignored = len(unmatched)
        ignored_sample = unmatched[:10]
        seen: set[int] = set()
        for name in names:
            book = by_filename.get(name)
            if book is None:
                continue
            key = int(book.id)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(book)
    except Exception as exc:  # noqa: BLE001
        logger.warning("列出远程进度目录失败，降级扫描已有进度书籍: %s", exc)
        listed = False
        remote_files = 0
        ignored = 0
        ignored_sample = []
        # 注意 read_chapter_index=0 是合法的「读过第一章」，不能用 `or -1` 兜底
        candidates = [
            b for b in books
            if int(b.read_chapter_index if b.read_chapter_index is not None else -1) >= 0
        ]

    matched = len(candidates)
    if ignored:
        logger.info(
            "批量进度同步：忽略 %d/%d 个书库外的 Legado 进度文件（手机书架大于服务端书库）",
            ignored,
            remote_files,
        )

    scanned = 0
    updated: list[dict] = []
    errors = 0
    for book in candidates[: max(1, min(limit, 1000))]:
        scanned += 1
        try:
            result = pull_progress(db, int(book.id), client=client, settings=settings)
        except Exception as exc:  # noqa: BLE001
            logger.warning("批量同步失败 book=%s: %s", book.id, exc)
            errors += 1
            continue
        if result.get("error"):
            errors += 1
        if result.get("updated"):
            updated.append(
                {
                    "book_id": int(book.id),
                    "chapter_id": result.get("chapter_id"),
                    "chapter_index": result.get("chapter_index"),
                    "remote_time": result.get("remote_time"),
                }
            )
    return {
        "enabled": True,
        "listed": listed,
        "remote_files": remote_files,
        "matched": matched,
        "ignored": ignored,
        "ignored_sample": ignored_sample,
        "scanned": scanned,
        "updated": len(updated),
        "errors": errors,
        "books": updated,
    }


def _build_payload(book: Book, chapter: Chapter, pos: int, time_ms: int) -> dict:
    return {
        "name": book.title,
        "author": book.author,
        "durChapterIndex": int(chapter.index),
        "durChapterPos": max(0, int(pos)),
        "durChapterTime": int(time_ms),
        "durChapterTitle": chapter.title or "",
    }


def push_progress(
    db: Session,
    book_id: int,
    chapter_id: int,
    chapter_pos: int = 0,
    time_ms: int | None = None,
) -> dict:
    """把当前进度写入 Legado 的 bookProgress 文件。

    写入前先读远端：若远端时间明显更新（另一台设备刚读过），跳过本次写入。
    """
    settings = webdav_settings.load()
    if not webdav_settings.is_configured(settings):
        return {"enabled": False, "pushed": False}

    book = db.get(Book, book_id)
    chapter = db.get(Chapter, chapter_id)
    if book is None or chapter is None or chapter.book_id != book_id:
        raise ValueError("书籍或章节不存在")

    filename = progress_filename(book.title, book.author)
    url = progress_url(settings, filename)
    # 客户端时钟偏差过大时以服务端时间为准（±5 分钟内才信任）
    server_ms = int(time.time() * 1000)
    now_ms = (
        int(time_ms)
        if time_ms and abs(int(time_ms) - server_ms) <= 5 * 60 * 1000
        else server_ms
    )

    try:
        client = _client(settings)
        try:
            raw = client.get(url)
            remote = _parse_remote(raw) if raw else None
            if remote and remote["durChapterTime"] > now_ms + 2000:
                return {
                    "enabled": True,
                    "pushed": False,
                    "skipped": "remote_newer",
                    "filename": filename,
                    "remote_time": remote["durChapterTime"],
                }
        except WebDavError:
            # 读不到远端时仍尝试写入（例如文件尚不存在）
            pass

        _mkcol_chain(client, settings)
        payload = _build_payload(book, chapter, chapter_pos, now_ms)
        client.put(url, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        return {
            "enabled": True,
            "pushed": True,
            "filename": filename,
            "remote_time": now_ms,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("推送远程进度失败 book=%s: %s", book_id, exc)
        return {"enabled": True, "pushed": False, "filename": filename, "error": str(exc)}


def push_progress_bg(
    book_id: int,
    chapter_id: int,
    chapter_pos: int = 0,
    time_ms: int | None = None,
) -> None:
    """后台线程推送（供关闭页面/Beacon 使用），不阻塞请求。"""
    from .database import SessionLocal

    db = SessionLocal()
    try:
        push_progress(db, book_id, chapter_id, chapter_pos, time_ms)
    except Exception:  # noqa: BLE001
        logger.exception("后台推送进度失败 book=%s", book_id)
    finally:
        db.close()


def sync_info(db: Session, book_id: int) -> dict:
    """详情页诊断：本地进度、远程文件是否存在、远端进度与章节匹配结果。"""
    settings = webdav_settings.load()
    book = db.get(Book, book_id)
    if book is None:
        raise ValueError("书籍不存在")

    filename = progress_filename(book.title, book.author)
    info: dict = {
        "enabled": bool(settings["enabled"]),
        "configured": webdav_settings.is_configured(settings),
        "filename": filename,
        "url": progress_url(settings, filename) if settings.get("url") else "",
        "local": {
            "read_chapter_index": int(book.read_chapter_index),
            "read_percent": int(book.read_percent),
            "read_at": book.read_at or "",
        },
        "remote_exists": False,
        "remote": None,
        "matched": None,
        "error": "",
    }
    if not info["configured"]:
        info["error"] = "尚未启用 WebDAV（或未填写地址）" if not settings["enabled"] else "未填写 WebDAV 地址"
        return info

    try:
        raw = _client(settings).get(progress_url(settings, filename))
        if raw is None:
            return info
        info["remote_exists"] = True
        remote = _parse_remote(raw)
        info["remote"] = remote
        if remote is None:
            info["error"] = "远端进度格式无法识别"
            return info
        chapters = _chapters(db, book_id)
        chapter = resolve_chapter(chapters, remote)
        if chapter is None:
            info["error"] = "无法匹配本地章节（标题与序号均未命中）"
        else:
            info["matched"] = {
                "chapter_id": chapter.id,
                "index": chapter.index,
                "title": chapter.title,
            }
    except Exception as exc:  # noqa: BLE001
        info["error"] = str(exc)
    return info


def push_now(book_id: int, chapter_id: int | None = None) -> dict:
    """手动推送当前本地进度（按钮触发，同步返回结果）。"""
    from .database import SessionLocal

    db = SessionLocal()
    try:
        book = db.get(Book, book_id)
        if book is None:
            raise ValueError("书籍不存在")
        chapter: Chapter | None = None
        if chapter_id:
            chapter = db.get(Chapter, chapter_id)
            if chapter is None or chapter.book_id != book_id:
                raise ValueError("章节不存在")
        else:
            chapter = (
                db.execute(
                    select(Chapter)
                    .where(
                        Chapter.book_id == book_id,
                        Chapter.index == int(book.read_chapter_index),
                    )
                    .order_by(Chapter.id)
                )
                .scalars()
                .first()
            )
        if chapter is None:
            return {"enabled": True, "pushed": False, "error": "本地还没有阅读进度"}
        last_ms = _iso_to_ms(book.read_at) or int(time.time() * 1000)
        return push_progress(db, book_id, chapter.id, 0, last_ms)
    finally:
        db.close()


def test_connection() -> dict:
    """测试 WebDAV 连接，并尝试创建 legado/bookProgress 目录。"""
    settings = webdav_settings.load()
    if not settings.get("url"):
        raise ValueError("请先填写 WebDAV 地址")
    client = _client(settings)
    root = _dir_url(settings["url"], settings["dir"])
    try:
        if not client.propfind(root):
            client.mkcol(root)
        _mkcol_chain(client, settings)
        return {"ok": True, "message": "连接成功", "root": root}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "root": root}

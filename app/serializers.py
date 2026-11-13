"""Book / Chapter → API 字典（公开与 Legado 共用）。

绝对地址优先按请求 Host / X-Forwarded-Host 推导，避免域名部署后仍指向 127.0.0.1；
管理端另给相对 cover_path，同源访问不依赖域名配置。
"""
from __future__ import annotations

import re
from urllib.parse import quote, urlsplit

from sqlalchemy import select
from sqlalchemy.orm import object_session

from .config import normalize_source, parse_category_label, settings
from .models import Book, Chapter

# 未配置对外地址时的默认值，出现这些 host 说明 PUBLIC_BASE_URL 还是 127.0.0.1
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0", "::1"}
# Host 头只接受常规字符，防「/ @ 空格」之类把基址拼坏
_HOST_RE = re.compile(r"^[A-Za-z0-9.\-:\[\]]{1,255}$")


def _is_loopback_base(url: str) -> bool:
    """基址是否仍指向本机（PUBLIC_BASE_URL 未配置）。"""
    if not url:
        return True
    try:
        return (urlsplit(url).hostname or "") in _LOOPBACK_HOSTS
    except ValueError:
        return False


def _request_base(request) -> str:
    """按请求头拼 scheme://host；host 非法或缺失时返回空串。"""
    try:
        headers = request.headers
        host = (headers.get("x-forwarded-host") or "").split(",")[0].strip()
        if not host:
            host = (headers.get("host") or "").strip()
        if not host or not _HOST_RE.match(host):
            return ""
        proto = (headers.get("x-forwarded-proto") or "").split(",")[0].strip()
        if not proto:
            proto = getattr(getattr(request, "url", None), "scheme", None) or "http"
        return f"{proto}://{host}".rstrip("/")
    except Exception:  # noqa: BLE001
        return ""


def resolve_base_url(request=None) -> str:
    """推导对外基址：默认固定 PUBLIC_BASE_URL，防 Host 头注入。

    仅当请求 Host 命中 TRUSTED_HOSTS 白名单时才采用反代头；未配置白名单时
    一律用 PUBLIC_BASE_URL，避免攻击者伪造 Host 污染返回链接与书源地址。

    例外：PUBLIC_BASE_URL 仍是回环地址（127.0.0.1/localhost，即未配置对外地址）
    时回退用请求 Host —— 否则域名部署下书源、封面、详情地址会指向手机自己，
    表现为 Legado「搜索不到、也读不了」。命中白名单的 Host 优先于该回退。
    """
    fixed = (settings.public_base_url or "").rstrip("/")
    if request is None:
        return fixed
    from_request = _request_base(request)
    if not from_request:
        return fixed
    trusted = {h.lower() for h in settings.trusted_hosts}
    host = from_request.split("://", 1)[-1].lower()
    if trusted and host in trusted:
        return from_request
    if _is_loopback_base(fixed):
        return from_request
    return fixed


def cover_rel(book: Book) -> str:
    """相对封面路径，管理端同源加载用。"""
    if not book.cover_file:
        return ""
    return f"/covers/{book.cover_file}"


def cover_abs(book: Book, base: str | None = None) -> str:
    if not book.cover_file:
        return ""
    root = (base or settings.public_base_url).rstrip("/")
    return f"{root}/covers/{book.cover_file}"


# 列表卡片使用的缩略图宽度（服务端按需生成，磁盘缓存）
THUMB_WIDTH = 320


def cover_thumb_rel(book: Book) -> str:
    if not book.cover_file:
        return ""
    return f"/thumbs/{quote(book.cover_file, safe='')}?w={THUMB_WIDTH}"


def cover_thumb_abs(book: Book, base: str | None = None) -> str:
    rel = cover_thumb_rel(book)
    if not rel:
        return ""
    root = (base or settings.public_base_url).rstrip("/")
    return f"{root}{rel}"


def filter_book_fields(item: dict, fields: str | None) -> dict:
    """按逗号分隔的字段裁剪列表项；id 始终保留，未知字段忽略。"""
    raw = fields.strip() if isinstance(fields, str) else ""
    if not raw:
        return item
    wanted = {part.strip() for part in raw.split(",") if part.strip()}
    if not wanted:
        return item
    wanted.add("id")
    return {key: value for key, value in item.items() if key in wanted}


def book_list_item(book: Book, base: str | None = None) -> dict:
    root = (base or settings.public_base_url).rstrip("/")
    return {
        "id": book.id,
        "name": book.title,
        # title 与 name 同值：体检/重复报告用 title，列表用 name，契约统一
        "title": book.title,
        "author": book.author,
        "category": book.category,
        "tags": book.tags_list,
        "status": book.status,
        "source": getattr(book, "source", "") or "",
        "source_id": getattr(book, "source_id", "") or "",
        # 在线搜索登记进来的书：source_path 统一以 online: 开头，前端据此显示
        # 「在线」角标；正文由服务端按需从原书源抓取，同样可以阅读
        "online": str(getattr(book, "source_path", "") or "").startswith("online:"),
        # 在线书的原站地址（给「去原书源阅读」用）；本地书为空
        "book_url": (
            str(book.source_path)[len("online:") :]
            if str(getattr(book, "source_path", "") or "").startswith("online:")
            else ""
        ),
        "cover_path": cover_rel(book),
        "cover_url": cover_abs(book, root),
        # 列表卡片优先用缩略图，减少原图流量（服务端按需生成）
        "cover_thumb_path": cover_thumb_rel(book),
        "cover_thumb_url": cover_thumb_abs(book, root),
        "intro": (book.intro or "")[:160],
        "word_count": book.word_count,
        "chapter_count": book.chapter_count,
        "latest_chapter": book.latest_chapter,
        "detail_url": f"{root}/api/books/{book.id}",
        "toc_url": f"{root}/api/books/{book.id}/chapters",
        "updated_at": book.updated_at,
        # 阅读进度百分比（0–100），列表卡片展示用
        "read_percent": int(getattr(book, "read_percent", 0) or 0),
        # 最近阅读章节序号（0 基，-1=无记录），「继续阅读」卡片展示用
        "read_chapter_index": (
            int(book.read_chapter_index)
            if getattr(book, "read_chapter_index", None) is not None
            else -1
        ),
        # 最近阅读时间（ISO8601，空串=未读），「继续阅读」排序用
        "read_at": getattr(book, "read_at", "") or "",
    }


def book_detail(book: Book, with_chapters: bool = False, base: str | None = None) -> dict:
    data = book_list_item(book, base)
    data["intro"] = book.intro or ""
    data["source_path"] = book.source_path
    data["created_at"] = book.created_at
    # 阅读续读信息（章节序号 + 最近阅读时间）
    data["read_chapter_index"] = int(getattr(book, "read_chapter_index", -1) if getattr(book, "read_chapter_index", -1) is not None else -1)
    data["read_at"] = getattr(book, "read_at", "") or ""
    if with_chapters:
        data["chapters"] = [chapter_item(ch, base) for ch in book.chapters]
    return data


def chapter_item(ch, base: str | None = None) -> dict:
    """目录条目：仅需 id/book_id/index/title，避免加载正文。"""
    root = (base or settings.public_base_url).rstrip("/")
    return {
        "id": ch.id,
        "index": ch.index,
        "name": ch.title,
        # title 与 name 同值，与 chapters_preview 对齐
        "title": ch.title,
        "content_url": f"{root}/api/books/{ch.book_id}/chapters/{ch.id}",
        "legado_content_url": f"{root}/api/legado/content/{ch.book_id}/{ch.id}",
    }


def chapter_content(ch: Chapter, base: str | None = None) -> dict:
    root = (base or settings.public_base_url).rstrip("/")
    # 正文优先读正文包偏移；迁移前旧数据回退 content 列
    from .content_store import read_chapter_text

    text = read_chapter_text(
        ch.book_id,
        ch.content_offset,
        ch.content_length,
        legacy_content=ch.content,
    )
    return {
        "id": ch.id,
        "book_id": ch.book_id,
        "index": ch.index,
        "title": ch.title,
        "content": text,
        "content_url": f"{root}/api/books/{ch.book_id}/chapters/{ch.id}",
    }


def admin_book_detail(book: Book, base: str | None = None) -> dict:
    data = book_detail(book, with_chapters=False, base=base)
    data["cover_file"] = book.cover_file
    data["source_hash"] = book.source_hash
    data["source"] = getattr(book, "source", "") or ""
    data["source_id"] = getattr(book, "source_id", "") or ""
    # 两级分类：category 合成串 + 拆开的书源/栏目（编辑页下拉用）
    src_name, cat_name = parse_category_label(book.category or "")
    if not src_name:
        src_name = normalize_source(getattr(book, "source", "") or "")
    data["category"] = book.category or ""
    data["category_source"] = src_name  # ""=本地 / 起点 / 番茄
    data["category_name"] = cat_name
    # 只取前 30 章标题做预览，避免 lazy 加载全部章节正文
    session = object_session(book)
    preview: list[dict] = []
    if session is not None:
        rows = session.execute(
            select(Chapter.id, Chapter.index, Chapter.title)
            .where(Chapter.book_id == book.id)
            .order_by(Chapter.index)
            .limit(30)
        ).all()
        preview = [{"id": r.id, "index": r.index, "title": r.title} for r in rows]
    data["chapters_preview"] = preview
    return data

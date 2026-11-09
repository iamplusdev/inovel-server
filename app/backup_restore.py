"""从 Legado 的 WebDAV 备份恢复「书源 + 书架」，并把两个书架拆成三部分。

Legado 的备份形态（都要兼容）：
- 阅读 3.0 App：``backup2026-11-09.zip``，包内 ``bookshelf.json`` /
  ``bookSource.json`` / ``bookGroup.json`` 等；
- reader(web) 版或手工导出：WebDAV 目录里散放的 ``bookshelf.json`` /
  ``bookSource.json``；
- 本地上传的 zip / json（内容与上面相同，只是不走 WebDAV 下载）。

三个部分（相对于「服务端书库 Book」与「Legado 书架 LegadoShelfBook」）：
- ``both``    两边都有，服务端有正文，可直接阅读；
- ``server``  仅服务端有（手机书架里没有）；
- ``legado``  仅 Legado 有（在线书，服务端没有正文，只展示信息）。

本模块只做解析、匹配与落库，不负责 HTTP 路由；规则引擎仍在手机端，
服务端不会去抓取在线书正文。
"""
from __future__ import annotations

import base64
import binascii
import io
import json
import logging
import re
import zipfile
from datetime import datetime
from urllib.parse import quote, urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import book_source_utils, webdav_settings
from .models import Book, BookSource, LegadoShelfBook
from .serializers import book_list_item
from .webdav import WebDavClient, WebDavError

logger = logging.getLogger("shelf")

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_ENTRY_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 96 * 1024 * 1024
MAX_BOOKS = 20000
MAX_SOURCES = 5000

# 备份包内可能出现的文件名（统一小写比较，兼容大小写与包内子目录）
BOOKSHELF_NAMES = {
    "bookshelf.json", "books.json", "book.json", "mybooks.json",
    "bookshelf.txt", "booklist.json",
}
SOURCE_NAMES = {
    "booksource.json", "booksources.json", "book_source.json",
    "sources.json", "source.json", "booksource.txt",
}
GROUP_NAMES = {"bookgroup.json", "bookgroups.json", "groups.json"}
BACKUP_SUFFIXES = (".zip", ".json", ".txt")

_STATE_FILE = "shelf_restore.json"
_BAD_PATH = re.compile(r"(^|/)\.\.?(/|$)")


# ---------------------------------------------------------------- 基础工具

def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _parse_loose(text: str):
    """解析 JSON；兼容多个对象直接拼接、单行一个对象的导出文本。"""
    raw = (text or "").strip().lstrip("\ufeff")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    values: list = []
    pos = 0
    while pos < len(raw):
        while pos < len(raw) and (raw[pos].isspace() or raw[pos] in ",;"):
            pos += 1
        if pos >= len(raw):
            break
        try:
            value, end = decoder.raw_decode(raw, pos)
        except json.JSONDecodeError:
            return None
        values.append(value)
        pos = end
    return values if len(values) > 1 else (values[0] if values else None)


def _as_dict_list(payload) -> list[dict]:
    """把各种包装形态归一为 dict 列表。"""
    if payload is None:
        return []
    if isinstance(payload, str):
        payload = _parse_loose(payload)
    if isinstance(payload, dict):
        for key in ("books", "bookshelf", "bookSources", "sources", "items", "data", "list"):
            nested = payload.get(key)
            if isinstance(nested, (list, dict, str)):
                return _as_dict_list(nested)
        return [payload]
    if isinstance(payload, list):
        out: list[dict] = []
        for item in payload:
            out.extend(_as_dict_list(item))
        return out
    return []


def _text(value, default: str = "") -> str:
    if value is None or isinstance(value, (dict, list, bool)):
        return default
    return str(value).strip()


def _integer(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def norm_text(value: str) -> str:
    """书名/作者匹配用的规范化：去空白、去常见标点、小写。"""
    return re.sub(r"[\s·:：,，.。!！?？\-—_《》〈〉\[\]【】()（）'\"]+", "", (value or "")).lower()


def match_key(name: str, author: str) -> str:
    return f"{norm_text(name)}|{norm_text(author)}"


def shelf_key(name: str, author: str, book_url: str = "", origin: str = "") -> str:
    """书架条目唯一键：优先书源+书籍地址，缺失时退化到 书名_作者。"""
    if origin and book_url:
        return f"{origin}|{book_url}"[:500]
    return f"{match_key(name, author)}"[:500]


def _ms_to_iso(value) -> str:
    ms = _integer(value, 0)
    if ms <= 0:
        return ""
    if ms < 10 ** 12:  # 秒级时间戳
        ms *= 1000
    try:
        return datetime.fromtimestamp(ms / 1000).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return ""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------- 备份解析

def _parse_groups(payload) -> dict[str, str]:
    groups: dict[str, str] = {}
    for row in _as_dict_list(payload):
        gid = _text(row.get("groupId") or row.get("id") or row.get("group"))
        gname = _text(row.get("groupName") or row.get("name") or row.get("title"))
        if gid and gname:
            groups[gid] = gname
    return groups


def normalize_shelf_book(raw: dict, groups: dict[str, str] | None = None) -> dict | None:
    """把 Legado 书架 Book 对象转成模型字段；保留完整原始 JSON。"""
    if not isinstance(raw, dict):
        return None
    name = _text(raw.get("name") or raw.get("bookName") or raw.get("title"))
    if not name:
        return None
    author = _text(raw.get("author"))
    book_url = _text(raw.get("bookUrl") or raw.get("url"))
    toc_url = _text(raw.get("tocUrl"))
    origin = _text(raw.get("origin") or raw.get("bookSourceUrl") or raw.get("sourceUrl"))
    origin_name = _text(raw.get("originName") or raw.get("bookSourceName") or raw.get("sourceName"))
    cover = _text(raw.get("coverUrl") or raw.get("cover"))
    intro = _text(raw.get("intro") or raw.get("description"))
    kind = _text(raw.get("kind") or raw.get("category"))
    latest = _text(
        raw.get("latestChapterTitle") or raw.get("latestChapter") or raw.get("lastChapter")
    )
    group_raw = raw.get("group")
    group_name = ""
    if groups is not None and group_raw is not None and group_raw != "":
        # Legado 的 group 通常是数字分组 ID，要先查 bookGroup.json 映射
        gid = _text(group_raw) if not isinstance(group_raw, (dict, list, bool)) else ""
        group_name = groups.get(gid, "") or groups.get(str(_integer(gid, -1)), "")
    if not group_name and isinstance(group_raw, str):
        group_name = group_raw.strip()
    dur_time = _integer(raw.get("durChapterTime") or raw.get("lastReadTime"), 0)
    return {
        "shelf_key": shelf_key(name, author, book_url, origin),
        "name": name[:200],
        "author": author[:100],
        "book_url": book_url[:1000],
        "toc_url": toc_url[:1000],
        "origin": origin[:500],
        "origin_name": origin_name[:200],
        "cover_url": cover[:1000],
        "intro": intro,
        "kind": kind[:200],
        "latest_chapter": latest[:200],
        "group_name": group_name[:100],
        "word_count": max(0, _integer(raw.get("wordCount"), 0)),
        "dur_chapter_index": _integer(raw.get("durChapterIndex"), -1),
        "dur_chapter_title": _text(raw.get("durChapterTitle"))[:200],
        "dur_chapter_pos": max(0, _integer(raw.get("durChapterPos"), 0)),
        "dur_chapter_time": dur_time,
        "raw_json": json.dumps(raw, ensure_ascii=False),
    }


def parse_backup(data: bytes, filename: str = "") -> dict:
    """解析一个备份文件，返回 {sources, books, groups, files, warnings}。"""
    warnings: list[str] = []
    if not data:
        raise ValueError("备份文件为空")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"备份文件超过 {MAX_FILE_BYTES // 1024 // 1024} MB 上限")

    files: list[dict] = []
    source_payloads: list = []
    book_payloads: list = []
    group_payloads: list = []

    def classify(name: str, raw: bytes) -> None:
        base = name.rsplit("/", 1)[-1]
        low = base.lower()
        if low in GROUP_NAMES:
            group_payloads.append(_parse_loose(_decode(raw)))
        elif low in SOURCE_NAMES or "booksource" in low:
            source_payloads.append(_parse_loose(_decode(raw)))
        elif low in BOOKSHELF_NAMES or "bookshelf" in low:
            book_payloads.append(_parse_loose(_decode(raw)))
        elif low.endswith(".json") and "/books/" in f"/{name.lower()}":
            book_payloads.append(_parse_loose(_decode(raw)))
        else:
            return
        files.append({"name": name, "size": len(raw)})

    if data[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                total = 0
                for info in zf.infolist():
                    if info.is_dir() or not info.filename.lower().endswith(BACKUP_SUFFIXES):
                        continue
                    if info.file_size > MAX_ENTRY_BYTES:
                        warnings.append(f"跳过超大条目 {info.filename}")
                        continue
                    total += info.file_size
                    if total > MAX_TOTAL_BYTES:
                        warnings.append("备份包解压后体积过大，已截断")
                        break
                    try:
                        classify(info.filename, zf.read(info))
                    except (KeyError, RuntimeError, OSError) as exc:
                        warnings.append(f"读取 {info.filename} 失败：{exc}")
        except zipfile.BadZipFile as exc:
            raise ValueError(f"不是有效的 zip 备份：{exc}") from exc
    else:
        low = (filename or "").rsplit("/", 1)[-1].lower()
        text = _decode(data)
        payload = _parse_loose(text)
        if low in SOURCE_NAMES or "booksource" in low:
            source_payloads.append(payload)
        elif low in GROUP_NAMES:
            group_payloads.append(payload)
        elif low in BOOKSHELF_NAMES or "bookshelf" in low:
            book_payloads.append(payload)
        else:
            # 按内容嗅探：书源有 bookSourceUrl，书架条目有 bookUrl/name
            rows = _as_dict_list(payload)
            if rows and any(r.get("bookSourceUrl") for r in rows):
                source_payloads.append(payload)
            elif rows and any(r.get("bookUrl") or r.get("name") for r in rows):
                book_payloads.append(payload)
            else:
                warnings.append(f"无法识别 {filename or '文件'} 的内容，已忽略")
        files.append({"name": filename or "backup.json", "size": len(data)})

    # 书源：复用书源管理的解析 + 规范化，过滤非法条目
    sources: list[dict] = []
    seen_sources: set[str] = set()
    invalid_sources = 0
    for payload in source_payloads:
        try:
            items = book_source_utils.parse_sources(payload)
        except book_source_utils.SourceParseError as exc:
            warnings.append(f"书源解析失败：{exc}")
            continue
        for item in items[:MAX_SOURCES]:
            try:
                src = book_source_utils.normalize_source(item)
            except book_source_utils.SourceParseError:
                invalid_sources += 1
                continue
            url = src["bookSourceUrl"]
            if url in seen_sources:
                continue
            seen_sources.add(url)
            sources.append(src)
    if invalid_sources:
        warnings.append(f"{invalid_sources} 条书源缺少必填字段，已跳过")

    groups: dict[str, str] = {}
    for payload in group_payloads:
        groups.update(_parse_groups(payload))

    books: list[dict] = []
    seen_books: set[str] = set()
    for payload in book_payloads:
        for raw in _as_dict_list(payload)[:MAX_BOOKS]:
            item = normalize_shelf_book(raw, groups)
            if not item or item["shelf_key"] in seen_books:
                continue
            seen_books.add(item["shelf_key"])
            books.append(item)

    if not sources and not books:
        warnings.append("备份里没有找到书架或书源数据（确认文件是否为 Legado 备份）")
    return {
        "sources": sources,
        "books": books,
        "groups": groups,
        "files": files,
        "warnings": warnings,
    }


# ---------------------------------------------------------------- WebDAV 备份文件

def _dir_url(base_url: str, dir_name: str) -> str:
    segs = [quote(p, safe="") for p in (dir_name or "").split("/") if p]
    base = (base_url or "").rstrip("/")
    return f"{base}/{'/'.join(segs)}" if segs else base


def _client(settings: dict) -> WebDavClient:
    return WebDavClient(
        base_url=settings["url"],
        username=settings["username"],
        password=settings["password"],
        timeout=float(settings.get("timeout", 6)),
        verify_tls=bool(settings.get("verify_tls", True)),
    )


def _candidate_roots(settings: dict) -> list[str]:
    base = (settings.get("url") or "").rstrip("/")
    dir_root = _dir_url(base, settings.get("dir") or "")
    roots = [dir_root, base, f"{dir_root}/backup", f"{base}/backup"]
    out: list[str] = []
    for r in roots:
        if r and r not in out:
            out.append(r)
    return out


def list_backups(settings: dict | None = None, client: WebDavClient | None = None) -> dict:
    """列出 WebDAV 上可作为备份的文件（zip / json / txt）。"""
    settings = settings or webdav_settings.load()
    result = {"configured": webdav_settings.is_configured(settings), "items": [], "error": ""}
    if not result["configured"]:
        result["error"] = "请先在「设置 → WebDAV」里配置并启用同步"
        return result
    client = client or _client(settings)
    base_path = urlparse(settings["url"]).path.rstrip("/")
    seen: set[str] = set()
    errors: list[str] = []
    for root in _candidate_roots(settings):
        try:
            entries = client.list_entries(root)
        except WebDavError as exc:
            errors.append(str(exc))
            continue
        rel_dir = urlparse(root).path.rstrip("/")
        if base_path and rel_dir.startswith(base_path):
            rel_dir = rel_dir[len(base_path):]
        rel_dir = rel_dir.strip("/")
        for entry in entries:
            if entry.get("is_dir") or not entry["name"].lower().endswith(BACKUP_SUFFIXES):
                continue
            path = f"{rel_dir}/{entry['name']}" if rel_dir else entry["name"]
            if path in seen:
                continue
            seen.add(path)
            low = entry["name"].lower()
            kind = "zip" if low.endswith(".zip") else "json"
            result["items"].append({**entry, "path": path, "kind": kind})

    # 先按文件名倒序（backup2026-11-09 > backup2026-11-08），再稳定地把 zip 排前面
    result["items"].sort(key=lambda x: x["name"].lower(), reverse=True)
    result["items"].sort(key=lambda x: x["kind"] != "zip")
    result["items"] = result["items"][:200]
    if not result["items"] and errors:
        result["error"] = errors[0]
    return result


def file_url(settings: dict, path: str) -> str:
    """把相对路径安全地拼成 WebDAV URL（禁止 .. 和绝对地址）。"""
    raw = (path or "").replace("\\", "/").strip()
    if not raw or raw.startswith("/") or "://" in raw or _BAD_PATH.search(raw):
        raise ValueError("备份路径不合法")
    parts = [p for p in raw.split("/") if p and p != "."]
    if not parts:
        raise ValueError("备份路径不合法")
    return (settings["url"].rstrip("/") + "/" + "/".join(quote(p, safe="") for p in parts))


def read_backup_file(
    settings: dict, path: str, client: WebDavClient | None = None
) -> tuple[bytes, str]:
    """下载备份；裸文件名会先在配置的 dir 下找，再回退 WebDAV 根目录。"""
    client = client or _client(settings)
    candidates = [path]
    dir_name = (settings.get("dir") or "").strip("/")
    if dir_name and "/" not in path:
        candidates.append(f"{dir_name}/{path}")
    errors: list[str] = []
    for candidate in candidates:
        try:
            url = file_url(settings, candidate)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        try:
            data = client.get(url, max_bytes=MAX_FILE_BYTES)
        except WebDavError as exc:
            errors.append(str(exc))
            continue
        if data is not None:
            return data, candidate.rsplit("/", 1)[-1]
    raise WebDavError(errors[0] if errors else f"WebDAV 上找不到备份文件 {path}")


def load_inputs(
    settings: dict,
    files: list[str] | None = None,
    contents: list[dict] | None = None,
    client: WebDavClient | None = None,
) -> tuple[list[tuple[bytes, str]], list[str]]:
    """汇总 WebDAV 文件与本地上传内容，返回 [(data, name)]。"""
    out: list[tuple[bytes, str]] = []
    warnings: list[str] = []
    for path in (files or [])[:20]:
        try:
            data, name = read_backup_file(settings, str(path), client=client)
        except (WebDavError, ValueError) as exc:
            warnings.append(f"{path}：{exc}")
            continue
        out.append((data, name))
    for item in (contents or [])[:20]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "upload.zip")
        raw = str(item.get("content") or "")
        try:
            data = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError) as exc:
            warnings.append(f"{name}：内容不是合法的 base64（{exc}）")
            continue
        if len(data) > MAX_FILE_BYTES:
            warnings.append(f"{name}：超过大小上限")
            continue
        out.append((data, name))
    if not out:
        raise ValueError(warnings[0] if warnings else "请选择 WebDAV 备份文件或上传本地备份")
    return out, warnings


def parse_inputs(
    settings: dict,
    files: list[str] | None = None,
    contents: list[dict] | None = None,
    client: WebDavClient | None = None,
) -> dict:
    """下载并合并解析多个备份文件（书源/书架按 key 去重，后出现的覆盖前面）。"""
    inputs, warnings = load_inputs(settings, files, contents, client)
    sources: dict[str, dict] = {}
    books: dict[str, dict] = {}
    file_names: list[dict] = []
    for data, name in inputs:
        try:
            parsed = parse_backup(data, name)
        except ValueError as exc:
            warnings.append(f"{name}：{exc}")
            continue
        for src in parsed["sources"]:
            sources[src["bookSourceUrl"]] = src
        for book in parsed["books"]:
            book = dict(book)
            book["backup_name"] = name
            books[book["shelf_key"]] = book
        for f in parsed["files"]:
            file_names.append({"name": f"{name}:{f['name']}", "size": f["size"]})
        warnings.extend(f"{name}：{w}" for w in parsed["warnings"])
    return {
        "sources": list(sources.values()),
        "books": list(books.values()),
        "files": file_names,
        "warnings": warnings,
        "backup_name": ", ".join(name for _, name in inputs[:5]),
    }


# ---------------------------------------------------------------- 三部分拆分

def collect_state(db: Session, *, persist: bool = True) -> dict:
    """计算两个书架的匹配关系，返回 {server_books, shelf_books, matched_ids}。

    server_books / shelf_books 都带 ``origin``（both/server/legado）与
    ``shelf_id``；匹配结果会缓存到 LegadoShelfBook.book_id。
    """
    server_books: list[Book] = list(db.execute(select(Book)).scalars().all())
    shelf_books: list[LegadoShelfBook] = list(
        db.execute(select(LegadoShelfBook)).scalars().all()
    )
    by_key: dict[str, list[Book]] = {}
    by_title: dict[str, list[Book]] = {}
    for book in server_books:
        key = match_key(book.title, book.author)
        by_key.setdefault(key, []).append(book)
        by_title.setdefault(norm_text(book.title), []).append(book)

    matched_ids: set[int] = set()
    changed = False
    for row in shelf_books:
        key = match_key(row.name, row.author)
        candidates = by_key.get(key, [])
        if not candidates:
            # 作者写法不一致（佚名 / 笔名）时，书名唯一命中也算同一本
            title_hits = by_title.get(norm_text(row.name), [])
            if len(title_hits) == 1:
                candidates = title_hits
        book = candidates[0] if candidates else None
        book_id = int(book.id) if book else None
        if row.book_id != book_id:
            row.book_id = book_id
            changed = True
        row._matched_book = book  # type: ignore[attr-defined]
        if book:
            matched_ids.add(int(book.id))
    if changed and persist:
        db.commit()

    for book in server_books:
        book._shelf_origin = "both" if int(book.id) in matched_ids else "server"  # type: ignore[attr-defined]
        book._shelf_rows = [  # type: ignore[attr-defined]
            r for r in shelf_books if r.book_id == int(book.id)
        ]
    return {
        "server_books": server_books,
        "shelf_books": shelf_books,
        "matched_ids": matched_ids,
    }


def _shelf_brief(row: LegadoShelfBook) -> dict:
    return {
        "shelf_id": int(row.id),
        "name": row.name,
        "author": row.author,
        "origin_name": row.origin_name,
        "origin": row.origin,
        "book_url": row.book_url,
        "cover_url": row.cover_url,
        "latest_chapter": row.latest_chapter,
        "dur_chapter_title": row.dur_chapter_title,
        "dur_chapter_index": int(row.dur_chapter_index),
        "dur_chapter_time": int(row.dur_chapter_time),
        "read_at": _ms_to_iso(row.dur_chapter_time),
        "kind": row.kind,
        "intro": row.intro,
        "group_name": row.group_name,
    }


def compare(db: Session, *, limit: int = 200, state: dict | None = None) -> dict:
    """三部分拆分：both / server / legado，带计数与明细（默认截断 200 条）。"""
    state = state or collect_state(db)
    server_books: list[Book] = state["server_books"]
    shelf_books: list[LegadoShelfBook] = state["shelf_books"]
    matched_ids: set[int] = state["matched_ids"]

    both = []
    for book in [b for b in server_books if int(b.id) in matched_ids]:
        rows = getattr(book, "_shelf_rows", [])
        both.append({
            "id": int(book.id),
            "name": book.title,
            "author": book.author,
            "read_at": book.read_at or "",
            "shelf": [_shelf_brief(r) for r in rows[:3]],
        })
    server_only = [
        {"id": int(b.id), "name": b.title, "author": b.author, "read_at": b.read_at or ""}
        for b in server_books if int(b.id) not in matched_ids
    ]
    legado_only = [_shelf_brief(r) for r in shelf_books if r.book_id is None]
    both.sort(key=lambda x: x["name"])
    server_only.sort(key=lambda x: x["name"])
    legado_only.sort(key=lambda x: x["name"])
    cap = max(1, min(limit, 1000))
    return {
        "counts": {
            "both": len(both),
            "server": len(server_only),
            "legado": len(legado_only),
            "server_total": len(server_books),
            "shelf_total": len(shelf_books),
        },
        "both": both[:cap],
        "server": server_only[:cap],
        "legado": legado_only[:cap],
        "truncated": len(both) > cap or len(server_only) > cap or len(legado_only) > cap,
        "limit": cap,
    }


def compare_preview(db: Session, books: list[dict], *, limit: int = 200) -> dict:
    """预览用：不落库，按解析出的书架条目计算「恢复后」的三部分。"""
    server_books: list[Book] = list(db.execute(select(Book)).scalars().all())
    by_key: dict[str, list[Book]] = {}
    by_title: dict[str, list[Book]] = {}
    for book in server_books:
        by_key.setdefault(match_key(book.title, book.author), []).append(book)
        by_title.setdefault(norm_text(book.title), []).append(book)

    matched_ids: set[int] = set()
    both: list[dict] = []
    legado_only: list[dict] = []
    for item in books:
        candidates = by_key.get(match_key(item["name"], item["author"]), [])
        if not candidates:
            hits = by_title.get(norm_text(item["name"]), [])
            if len(hits) == 1:
                candidates = hits
        if candidates:
            book = candidates[0]
            matched_ids.add(int(book.id))
            both.append({
                "id": int(book.id), "name": book.title, "author": book.author,
                "shelf_name": item["name"], "shelf_author": item["author"],
            })
        else:
            legado_only.append({
                "name": item["name"], "author": item["author"],
                "origin_name": item.get("origin_name", ""),
                "latest_chapter": item.get("latest_chapter", ""),
            })
    server_only = [
        {"id": int(b.id), "name": b.title, "author": b.author}
        for b in server_books if int(b.id) not in matched_ids
    ]
    both.sort(key=lambda x: x["name"])
    server_only.sort(key=lambda x: x["name"])
    legado_only.sort(key=lambda x: x["name"])
    cap = max(1, min(limit, 1000))
    return {
        "counts": {
            "both": len(both), "server": len(server_only), "legado": len(legado_only),
            "server_total": len(server_books), "shelf_total": len(books),
        },
        "both": both[:cap], "server": server_only[:cap], "legado": legado_only[:cap],
        "truncated": len(both) > cap or len(server_only) > cap or len(legado_only) > cap,
        "limit": cap,
    }


def online_item(row: LegadoShelfBook, base: str = "") -> dict:
    """把「仅 Legado 有」的书架条目伪装成书库列表项，供前端统一渲染。

    id 取负数，避免与本地 Book.id 冲突；readable=false 让客户端不要进阅读器。
    """
    read_at = _ms_to_iso(row.dur_chapter_time)
    root = (base or "").rstrip("/")
    return {
        "id": -int(row.id),
        "name": row.name,
        "title": row.name,
        "author": row.author or "佚名",
        "category": row.kind or "在线书",
        "tags": [],
        "status": "在线",
        "source": row.origin_name or "Legado",
        "source_id": "",
        "cover_path": "",
        "cover_url": row.cover_url,
        "cover_thumb_path": "",
        "cover_thumb_url": row.cover_url,
        "intro": row.intro[:160],
        "full_intro": row.intro,
        "word_count": int(row.word_count or 0),
        "chapter_count": 0,
        "latest_chapter": row.latest_chapter,
        "detail_url": f"{root}/api/admin/shelf/books/{row.id}" if root else "",
        "toc_url": "",
        "updated_at": read_at or row.updated_at,
        "read_percent": 0,
        "read_chapter_index": int(row.dur_chapter_index),
        "read_at": read_at,
        # —— 书架来源标记 ——
        "origin": "legado",
        "readable": False,
        "shelf_id": int(row.id),
        "source_name": row.origin_name,
        "book_url": row.book_url,
        "source_url": row.origin,
        "dur_chapter_title": row.dur_chapter_title,
    }


def server_item(book: Book, base: str = "", state: dict | None = None) -> dict:
    item = book_list_item(book, base)
    rows = getattr(book, "_shelf_rows", None)
    if rows is None and state is not None:
        rows = [r for r in state["shelf_books"] if r.book_id == int(book.id)]
    rows = rows or []
    item["origin"] = "both" if rows else "server"
    # 在线搜索登记的书没有本地正文，不能进阅读器
    item["readable"] = not item.get("online", False)
    item["shelf_id"] = int(rows[0].id) if rows else None
    item["source_name"] = rows[0].origin_name if rows else ""
    # 有手机书架记录时用书架里的原始地址；在线搜索登记的书沿用书目自带的 book_url
    if rows:
        item["book_url"] = rows[0].book_url
    return item


def merged_books(
    db: Session,
    *,
    q: str = "",
    origin: str = "all",
    sort: str = "updated",
    page: int = 1,
    page_size: int = 20,
    fields: str | None = None,
    base: str = "",
    read: bool = False,
    category: str | None = None,
    source: str | None = None,
    status: str | None = None,
    tag: str | None = None,
    scraped: str | None = None,
    state: dict | None = None,
) -> dict:
    """合并书架分页：本地书 + 仅 Legado 在线书，支持来源筛选与搜索。

    category/source/status/tag/scraped 沿用书库筛选语义；在线书只有
    kind/origin_name，因此按这两列参与 category/source 过滤，其余筛选项
    只在本地书里生效。
    """
    from .serializers import filter_book_fields

    state = state or collect_state(db)
    all_items: list[dict] = [server_item(b, base, state) for b in state["server_books"]]
    all_items.extend(
        online_item(r, base) for r in state["shelf_books"] if r.book_id is None
    )
    counts = {
        "all": len(all_items),
        "both": sum(1 for i in all_items if i["origin"] == "both"),
        "server": sum(1 for i in all_items if i["origin"] == "server"),
        "legado": sum(1 for i in all_items if i["origin"] == "legado"),
    }
    items = all_items
    if origin in ("both", "server", "legado"):
        items = [i for i in items if i["origin"] == origin]
    if read:
        items = [i for i in items if i.get("readable") and i.get("read_at")]
    if category and category != "全部":
        items = [
            i for i in items
            if i.get("category") == category
            or str(i.get("category") or "").endswith(f"-{category}")
        ]
    if source and source != "全部":
        items = [
            i for i in items
            if i.get("source") == source
            or i.get("source_name") == source
            or (source in ("本地", "none", "-") and i["origin"] != "legado")
        ]
    if status:
        items = [i for i in items if i.get("status") == status]
    if tag:
        items = [i for i in items if tag in (i.get("tags") or [])]
    if isinstance(scraped, str) and scraped.strip().lower() == "unscraped":
        items = [i for i in items if i["origin"] != "legado" and not i.get("source_id")]
    kw = (q or "").strip().lower()
    if kw:
        items = [
            i for i in items
            if kw in (i.get("name") or "").lower()
            or kw in (i.get("author") or "").lower()
            or kw in (i.get("intro") or "").lower()
            or kw in (i.get("source_name") or "").lower()
        ]

    def key(item: dict):
        if sort == "title":
            return (0, (item.get("name") or "").lower())
        if sort == "author":
            return (0, (item.get("author") or "").lower(), (item.get("name") or "").lower())
        if sort == "words":
            return (0, -int(item.get("word_count") or 0))
        if sort == "chapters":
            return (0, -int(item.get("chapter_count") or 0))
        if sort == "read":
            return (0, _reverse_time(item.get("read_at") or ""))
        return (0, _reverse_time(item.get("updated_at") or ""), -(item.get("id") or 0))

    items.sort(key=key)
    total = len(items)
    page = max(1, page)
    page_size = min(200, max(1, page_size))
    sliced = items[(page - 1) * page_size : page * page_size]
    if fields:
        # origin/readable/online 是客户端判断「能不能读」的依据，裁剪后必须保留
        sliced = [filter_book_fields(i, f"{fields},origin,readable,online") for i in sliced]
    for item in sliced:
        item.setdefault("origin", "server")
        item.setdefault("readable", True)
        item.setdefault("online", False)
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": sliced,
        "counts": counts,
    }


def _reverse_time(value: str) -> str:
    """把 ISO 时间变成可倒序比较的字符串（空串排最后，用最大字符占位）。"""
    if not value:
        return "\uffff"
    return "".join(chr(255 - ord(c)) if c.isdigit() else c for c in value[:19])


# ---------------------------------------------------------------- 恢复落库

def restore_sources(db: Session, sources: list[dict], *, overwrite: bool = False) -> dict:
    """把备份里的书源写入 BookSource（与书源管理页共用规范化结果）。"""
    created = updated = skipped = 0
    for src in sources:
        url = src["bookSourceUrl"]
        row = db.execute(
            select(BookSource).where(BookSource.source_url == url)
        ).scalars().first()
        if row and not overwrite:
            skipped += 1
            continue
        fields = book_source_utils.extract_fields(src)
        if row:
            for k, v in fields.items():
                setattr(row, k, v)
            row.raw_json = json.dumps(src, ensure_ascii=False)
            row.updated_at = _now()
            updated += 1
        else:
            db.add(BookSource(
                raw_json=json.dumps(src, ensure_ascii=False),
                remark="WebDAV 备份恢复",
                last_checked_at="",
                last_check_ok=False,
                created_at=_now(),
                updated_at=_now(),
                **fields,
            ))
            created += 1
    db.commit()
    return {"total": len(sources), "created": created, "updated": updated, "skipped": skipped}


def restore_shelf(
    db: Session, books: list[dict], *, backup_name: str = "", replace: bool = True
) -> dict:
    """写入 LegadoShelfBook；replace=True 时先清空旧书架（备份是全量快照）。"""
    if replace:
        for row in db.execute(select(LegadoShelfBook)).scalars().all():
            db.delete(row)
        db.flush()
    created = updated = 0
    now = _now()
    existing: dict[str, LegadoShelfBook] = {}
    if not replace:
        existing = {
            r.shelf_key: r for r in db.execute(select(LegadoShelfBook)).scalars().all()
        }
    for item in books:
        row = existing.get(item["shelf_key"])
        if row is None:
            row = LegadoShelfBook(shelf_key=item["shelf_key"], created_at=now)
            db.add(row)
            created += 1
        else:
            updated += 1
        for key, value in item.items():
            if key in ("shelf_key", "raw_json", "backup_name"):
                setattr(row, key, value)
            elif hasattr(row, key) and key not in ("id", "created_at", "book_id"):
                setattr(row, key, value)
        row.raw_json = item.get("raw_json") or "{}"
        row.backup_name = item.get("backup_name") or backup_name
        row.updated_at = now
        row.book_id = None  # 由 collect_state 重新匹配
    db.commit()
    return {"total": len(books), "created": created, "updated": updated, "replace": replace}


def state_path():
    from .config import settings

    parent = settings.database_path.parent
    parent.mkdir(parents=True, exist_ok=True)
    return parent / _STATE_FILE


def load_state() -> dict:
    path = state_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(data: dict) -> None:
    import os

    path = state_path()
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        logger.warning("保存书架恢复状态失败", exc_info=True)


def restore(
    db: Session,
    parsed: dict,
    *,
    restore_books: bool = True,
    restore_book_sources: bool = True,
    overwrite_sources: bool = False,
    replace_shelf: bool = True,
) -> dict:
    """执行一次恢复，返回统计 + 恢复后的三部分对照。"""
    result: dict = {
        "backup_name": parsed.get("backup_name", ""),
        "files": parsed.get("files", []),
        "warnings": parsed.get("warnings", []),
        "parsed": {
            "sources": len(parsed.get("sources", [])),
            "books": len(parsed.get("books", [])),
        },
        "sources": {"total": 0, "created": 0, "updated": 0, "skipped": 0},
        "shelf": {"total": 0, "created": 0, "updated": 0, "replace": replace_shelf},
    }
    if restore_book_sources:
        result["sources"] = restore_sources(
            db, parsed.get("sources", []), overwrite=overwrite_sources
        )
    else:
        result["sources"]["total"] = len(parsed.get("sources", []))
    if restore_books:
        result["shelf"] = restore_shelf(
            db, parsed.get("books", []),
            backup_name=parsed.get("backup_name", ""),
            replace=replace_shelf,
        )
    else:
        result["shelf"]["total"] = len(parsed.get("books", []))
    state = collect_state(db)
    result["compare"] = compare(db, state=state)
    summary = {
        "restored_at": _now(),
        "backup_name": parsed.get("backup_name", ""),
        "sources": result["sources"],
        "shelf": result["shelf"],
        "counts": result["compare"]["counts"],
    }
    save_state(summary)
    result["state"] = summary
    return result

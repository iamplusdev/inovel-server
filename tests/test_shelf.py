"""WebDAV 备份恢复 / 书架三部分拆分 / 合并书库的回归测试。

运行：pytest tests/test_shelf.py 或 python -m unittest tests.test_shelf
"""
from __future__ import annotations

import base64
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


def _source(name: str, url: str) -> dict:
    return {
        "bookSourceName": name,
        "bookSourceUrl": url,
        "searchUrl": "/search?key={{key}}",
        "ruleSearch": {"bookList": "li", "name": "a", "bookUrl": "@href"},
    }


def _book(name: str, author: str, **extra) -> dict:
    return {"name": name, "author": author, "bookUrl": f"https://x/{name}", **extra}


def _zip(shelf=None, sources=None, groups=None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        if shelf is not None:
            zf.writestr("backup/bookshelf.json", json.dumps(shelf, ensure_ascii=False))
        if sources is not None:
            zf.writestr("backup/bookSource.json", json.dumps(sources, ensure_ascii=False))
        if groups is not None:
            zf.writestr("bookGroup.json", json.dumps(groups, ensure_ascii=False))
    return buf.getvalue()


class ShelfRestoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp())
        os.environ["DATABASE_PATH"] = str(self._tmp / "t.db")
        os.environ["NOVELS_DIR"] = str(self._tmp / "novels")
        os.environ["COVERS_DIR"] = str(self._tmp / "covers")

        from app import config as cfg
        from app import database as dbmod
        from app import stats_cache
        from app.auth import reset_auth_cache

        reset_auth_cache()
        stats_cache.clear()
        cfg.settings.database_path = self._tmp / "t.db"
        cfg.settings.novels_dir = self._tmp / "novels"
        cfg.settings.covers_dir = self._tmp / "covers"
        cfg.settings.database_url = f"sqlite:///{cfg.settings.database_path}"
        cfg.settings.ensure_dirs()
        dbmod.reset_database()
        dbmod.init_db()
        self.db = dbmod
        self.cfg = cfg

    def _seed_books(self) -> None:
        from app.models import Book

        s = self.db.SessionLocal()
        s.add_all([
            Book(title="共有书", author="作者甲", category="都市", chapter_count=3),
            Book(title="仅服务端", author="作者乙", category="玄幻", chapter_count=2),
        ])
        s.commit()
        s.close()

    def test_parse_zip_backup_and_normalize(self) -> None:
        """zip 内 bookshelf/bookSource/bookGroup 都能解析，分组映射生效。"""
        from app import backup_restore as br

        data = _zip(
            shelf=[
                _book("共有书", "作者甲", origin="https://src.a", originName="源A",
                      durChapterIndex=2, durChapterTime=1730000000000,
                      latestChapterTitle="第三章", intro="简介", group="1"),
                _book("在线书", "作者丙", origin="https://src.b", originName="源B"),
            ],
            sources=[_source("源A", "https://src.a")],
            groups=[{"groupId": "1", "groupName": "精读"}],
        )
        parsed = br.parse_backup(data, "backup2026-11-09.zip")
        self.assertEqual(len(parsed["books"]), 2)
        self.assertEqual(len(parsed["sources"]), 1)
        self.assertEqual(parsed["warnings"], [])
        first = parsed["books"][0]
        self.assertEqual(first["group_name"], "精读")
        self.assertEqual(first["dur_chapter_index"], 2)
        self.assertEqual(first["origin_name"], "源A")
        self.assertEqual(first["raw_json"] and json.loads(first["raw_json"])["name"], "共有书")

    def test_parse_plain_json_and_multiple_objects(self) -> None:
        """散放的 bookshelf.json / 拼接对象文本也能解析。"""
        from app import backup_restore as br

        shelf = [_book("A", "甲"), _book("B", "乙")]
        self.assertEqual(len(br.parse_backup(json.dumps(shelf).encode(), "bookshelf.json")["books"]), 2)
        sources = json.dumps(_source("S1", "https://s1")) + "\n" + json.dumps(_source("S2", "https://s2"))
        self.assertEqual(len(br.parse_backup(sources.encode(), "bookSource.json")["sources"]), 2)
        # 未知后缀按内容嗅探
        self.assertEqual(len(br.parse_backup(json.dumps(shelf).encode(), "export.txt")["books"]), 2)

    def test_compare_preview_three_groups(self) -> None:
        """预览不落库，且三部分计数正确。"""
        from app import backup_restore as br

        self._seed_books()
        s = self.db.SessionLocal()
        try:
            result = br.compare_preview(s, [
                br.normalize_shelf_book(_book("共有书", "作者甲")),
                br.normalize_shelf_book(_book("仅手机", "作者丙")),
            ])
            self.assertEqual(result["counts"]["both"], 1)
            self.assertEqual(result["counts"]["server"], 1)
            self.assertEqual(result["counts"]["legado"], 1)
            self.assertEqual(result["counts"]["server_total"], 2)
            # 预览不能写库
            from app.models import LegadoShelfBook
            self.assertEqual(len(list(s.execute(__import__("sqlalchemy").select(LegadoShelfBook)).scalars().all())), 0)
        finally:
            s.close()

    def test_restore_endpoint_and_merged_books(self) -> None:
        """端点级：预览→恢复→三部分→合并书库分页与来源筛选。"""
        import time
        from fastapi.testclient import TestClient

        from app import webdav_settings
        from app.auth import require_admin_dep
        from app.main import app as fastapi_app
        from app.models import Book, BookSource, LegadoShelfBook

        self._seed_books()
        data = _zip(
            shelf=[
                _book("共有书", "作者甲", origin="https://src.a", originName="源A",
                      durChapterIndex=1, durChapterTime=int(time.time() * 1000)),
                _book("手机在线书", "作者丙", origin="https://src.b", originName="源B",
                      intro="手机端才有正文", coverUrl="https://x/c.jpg"),
            ],
            sources=[_source("源A", "https://src.a")],
        )
        payload = {
            "contents": [{"name": "backup2026-11-09.zip", "content": base64.b64encode(data).decode()}]
        }
        settings = {
            "enabled": True, "url": "https://dav.example.com/dav",
            "username": "u", "password": "p", "dir": "legado",
            "timeout": 6, "verify_tls": True,
        }
        fastapi_app.dependency_overrides[require_admin_dep] = lambda: "admin"
        try:
            with mock.patch.object(webdav_settings, "load", return_value=settings):
                c = TestClient(fastapi_app)
                prev = c.post("/api/admin/shelf/preview", json=payload)
                self.assertEqual(prev.status_code, 200, prev.text)
                self.assertEqual(prev.json()["compare"]["counts"]["legado"], 1)

                res = c.post("/api/admin/shelf/restore", json={**payload, "overwrite_sources": True})
                self.assertEqual(res.status_code, 200, res.text)
                body = res.json()
                self.assertEqual(body["sources"]["created"], 1)
                self.assertEqual(body["shelf"]["created"], 2)
                self.assertEqual(body["compare"]["counts"], {
                    "both": 1, "server": 1, "legado": 1,
                    "server_total": 2, "shelf_total": 2,
                })

                merged = c.get("/api/admin/books", params={"origin": "all", "page_size": 10})
                self.assertEqual(merged.status_code, 200, merged.text)
                self.assertEqual(merged.json()["total"], 3)
                by_name = {i["name"]: i for i in merged.json()["items"]}
                self.assertEqual(by_name["共有书"]["origin"], "both")
                self.assertEqual(by_name["仅服务端"]["origin"], "server")
                online = by_name["手机在线书"]
                self.assertEqual(online["origin"], "legado")
                self.assertFalse(online["readable"])
                self.assertLess(online["id"], 0)
                self.assertEqual(online["source_name"], "源B")

                only_online = c.get("/api/admin/books", params={"origin": "legado"})
                self.assertEqual(only_online.json()["total"], 1)
                # counts 始终是未筛选全量
                self.assertEqual(only_online.json()["counts"]["all"], 3)

                detail = c.get(f"/api/admin/shelf/books/{online['shelf_id']}")
                self.assertEqual(detail.status_code, 200)
                self.assertEqual(detail.json()["raw"]["name"], "手机在线书")

                cmp_res = c.get("/api/admin/shelf/compare")
                self.assertEqual(cmp_res.json()["counts"]["legado"], 1)
        finally:
            fastapi_app.dependency_overrides.pop(require_admin_dep, None)

        s = self.db.SessionLocal()
        try:
            self.assertEqual(len(list(s.execute(__import__("sqlalchemy").select(LegadoShelfBook)).scalars().all())), 2)
            self.assertEqual(len(list(s.execute(__import__("sqlalchemy").select(BookSource)).scalars().all())), 1)
            self.assertEqual(len(list(s.execute(__import__("sqlalchemy").select(Book)).scalars().all())), 2)
        finally:
            s.close()

    def test_clear_and_replace_shelf(self) -> None:
        """全量替换不会残留旧条目，清空只删 Legado 书架。"""
        from app import backup_restore as br
        from app.models import Book, LegadoShelfBook
        from sqlalchemy import select

        self._seed_books()
        s = self.db.SessionLocal()
        try:
            first = [_book("旧书", "甲")]
            br.restore(s, {"sources": [], "books": [
                {**br.normalize_shelf_book(b), "backup_name": "a.zip"} for b in first
            ], "files": [], "warnings": [], "backup_name": "a.zip"})
            self.assertEqual(len(list(s.execute(select(LegadoShelfBook)).scalars().all())), 1)
            second = [_book("新书A", "乙"), _book("新书B", "丙")]
            br.restore(s, {"sources": [], "books": [
                {**br.normalize_shelf_book(b), "backup_name": "b.zip"} for b in second
            ], "files": [], "warnings": [], "backup_name": "b.zip"})
            rows = list(s.execute(select(LegadoShelfBook)).scalars().all())
            self.assertEqual({r.name for r in rows}, {"新书A", "新书B"})
            # 本地书库没被动过
            self.assertEqual(len(list(s.execute(select(Book)).scalars().all())), 2)
            # clear 走 delete 后本地书仍在
            for r in rows:
                s.delete(r)
            s.commit()
            self.assertEqual(len(list(s.execute(select(Book)).scalars().all())), 2)
        finally:
            s.close()

    def test_webdav_list_entries_and_path_safety(self) -> None:
        """PROPFIND 解析出名称/大小/目录标记；备份路径拒绝 .. 与绝对地址。"""
        from app import backup_restore as br
        from app.webdav import WebDavClient

        xml = (
            '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">'
            "<d:response><d:href>/dav/legado/</d:href>"
            "<d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype>"
            "</d:prop></d:propstat></d:response>"
            "<d:response><d:href>/dav/legado/backup2026-11-09.zip</d:href>"
            "<d:propstat><d:prop><d:resourcetype/><d:getcontentlength>2048</d:getcontentlength>"
            "<d:getlastmodified>Sun, 09 Nov 2026 04:00:00 GMT</d:getlastmodified>"
            "</d:prop></d:propstat></d:response>"
            "<d:response><d:href>/dav/legado/bookProgress/</d:href>"
            "<d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype>"
            "</d:prop></d:propstat></d:response>"
            "</d:multistatus>"
        ).encode()
        client = WebDavClient(base_url="https://dav.example.com/dav")
        with mock.patch.object(client, "_request", return_value=(207, xml)):
            entries = client.list_entries("https://dav.example.com/dav/legado/")
        self.assertEqual(len(entries), 2)  # zip 文件 + bookProgress 子目录（带 is_dir 标记）
        files = [e for e in entries if not e["is_dir"]]
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["name"], "backup2026-11-09.zip")
        self.assertEqual(files[0]["size"], 2048)
        self.assertTrue(any(e["is_dir"] and e["name"] == "bookProgress" for e in entries))

        settings = {"url": "https://dav.example.com/dav", "dir": "legado"}
        self.assertTrue(br.file_url(settings, "legado/backup.zip").endswith("/dav/legado/backup.zip"))
        for bad in ("../secret.zip", "/etc/passwd", "https://evil/x.zip", "a/../../b.zip"):
            with self.assertRaises(ValueError):
                br.file_url(settings, bad)


if __name__ == "__main__":
    unittest.main()

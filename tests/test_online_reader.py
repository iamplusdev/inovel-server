"""在线书阅读回归测试：目录规则、正文清洗、按需缓存。

外部站点一律 mock，测试不依赖网络。
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BOOK_PAGE = """
<html><body>
  <h1>测试小说</h1>
  <a class="toc-link" href="/book/1/toc">查看目录</a>
</body></html>
"""

TOC_PAGE = """
<html><body><ul class="chapters">
  <li><a href="/book/1/10.html">第一章 起点</a></li>
  <li><a href="/book/1/11.html">第二章 远方</a></li>
</ul></body></html>
"""

CHAPTER_PAGE = """
<html><body>
  <div class="content">
    <p>第一段正文。</p>
    <p>第二段<br>换行之后。</p>
    <script>alert("广告")</script>
  </div>
</body></html>
"""

SOURCE = {
    "bookSourceName": "测试阅读源",
    "bookSourceUrl": "https://read.example.com",
    "ruleBookInfo": {"tocUrl": ".toc-link@href"},
    "ruleToc": {
        "chapterList": ".chapters li",
        "chapterName": "a@text",
        "chapterUrl": "a@href",
    },
    "ruleContent": {"content": ".content"},
}

SWITCH_SEARCH_PAGE = """
<html><body>
  <div class="result"><a class="name" href="/book/2">测试小说</a><span class="author">测试作者</span></div>
  <div class="result"><a class="name" href="/book/9">无关的书</a><span class="author">别人</span></div>
</body></html>
"""

SWITCH_TOC_PAGE = """
<html><body><ul class="chapters">
  <li><a href="/book/2/21.html">第一章 新起点</a></li>
  <li><a href="/book/2/22.html">第二章 新远方</a></li>
  <li><a href="/book/2/23.html">第三章 终章</a></li>
</ul></body></html>
"""

SWITCH_CHAPTER_PAGE = """
<html><body><div class="content"><p>备用源正文。</p></div></body></html>
"""

SWITCH_SOURCE = {
    "bookSourceName": "备用阅读源",
    "bookSourceUrl": "https://backup.example.com",
    "searchUrl": "/search?q={{key}}",
    "ruleSearch": {
        "bookList": ".result",
        "name": ".name@text",
        "author": ".author@text",
        "bookUrl": ".name@href",
    },
    "ruleToc": {
        "chapterList": ".chapters li",
        "chapterName": "a@text",
        "chapterUrl": "a@href",
    },
    "ruleContent": {"content": ".content"},
}


class _DbCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp())
        os.environ["DATABASE_PATH"] = str(self._tmp / "t.db")
        os.environ["NOVELS_DIR"] = str(self._tmp / "novels")
        os.environ["COVERS_DIR"] = str(self._tmp / "covers")
        os.environ["CONTENTS_DIR"] = str(self._tmp / "contents")

        from app import config as cfg
        from app import database as dbmod
        from app.auth import reset_auth_cache

        reset_auth_cache()
        cfg.settings.database_path = self._tmp / "t.db"
        cfg.settings.novels_dir = self._tmp / "novels"
        cfg.settings.covers_dir = self._tmp / "covers"
        cfg.settings.contents_dir = self._tmp / "contents"
        cfg.settings.database_url = f"sqlite:///{cfg.settings.database_path}"
        cfg.settings.ensure_dirs()
        dbmod.reset_database()
        dbmod.init_db()
        self.cfg = cfg
        self.dbmod = dbmod

    def tearDown(self) -> None:
        os.environ.pop("CONTENTS_DIR", None)

    def _session(self):
        return self.dbmod.SessionLocal()

    def _add_source(self, db) -> "object":
        return self._add_source_raw(db, SOURCE)

    def _add_source_raw(self, db, raw: dict) -> "object":
        from app.models import BookSource

        row = BookSource(
            source_url=raw["bookSourceUrl"],
            name=raw["bookSourceName"],
            group_name="",
            source_type=0,
            enabled=True,
            enabled_explore=False,
            weight=0,
            remark="",
            raw_json=json.dumps(raw, ensure_ascii=False),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def _add_book(self, db, source) -> "object":
        from app.models import Book

        row = Book(
            title="测试小说",
            author="测试作者",
            category="未分类",
            source=source.name,
            source_path="online:https://read.example.com/book/1",
            remote_source_id=int(source.id),
            remote_book_url="https://read.example.com/book/1",
            chapter_count=0,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row


class RuleTests(unittest.TestCase):
    def test_toc_rule_resolves_book_info_toc_url(self) -> None:
        from app import legado_rules as R

        def fake_fetch(url: str, *, headers=None, timeout=10.0) -> str:
            return BOOK_PAGE if url.endswith("/book/1") else TOC_PAGE

        with patch("app.legado_rules.fetch_text", side_effect=fake_fetch):
            chapters = R.toc_source(SOURCE, "https://read.example.com/book/1")

        self.assertEqual([c["title"] for c in chapters], ["第一章 起点", "第二章 远方"])
        self.assertEqual(chapters[0]["url"], "https://read.example.com/book/1/10.html")

    def test_toc_rule_direct_toc_url_skips_book_page(self) -> None:
        from app import legado_rules as R

        calls: list[str] = []

        def fake_fetch(url: str, *, headers=None, timeout=10.0) -> str:
            calls.append(url)
            return TOC_PAGE

        with patch("app.legado_rules.fetch_text", side_effect=fake_fetch):
            chapters = R.toc_source(
                SOURCE,
                "https://read.example.com/book/1",
                toc_url="https://read.example.com/book/1/toc",
            )

        self.assertEqual(len(chapters), 2)
        self.assertEqual(calls, ["https://read.example.com/book/1/toc"])

    def test_content_rule_cleans_html(self) -> None:
        from app import legado_rules as R

        with patch("app.legado_rules.fetch_text", return_value=CHAPTER_PAGE):
            text = R.content_source(
                SOURCE, "https://read.example.com/book/1/10.html", chapter_title="第一章"
            )

        self.assertIn("第一段正文。", text)
        self.assertIn("第二段", text)
        self.assertIn("换行之后。", text)
        self.assertNotIn("<", text)
        self.assertNotIn("广告", text)


class OnlineReadTests(_DbCase):
    def test_sync_toc_and_cache_chapter_content(self) -> None:
        from app import online_reader
        from app.models import Chapter

        db = self._session()
        try:
            source = self._add_source(db)
            book = self._add_book(db, source)

            def fake_fetch(url: str, *, headers=None, timeout=10.0) -> str:
                if url.endswith("/book/1"):
                    return BOOK_PAGE
                if url.endswith("/toc"):
                    return TOC_PAGE
                return CHAPTER_PAGE

            with patch("app.legado_rules.fetch_text", side_effect=fake_fetch):
                result = online_reader.sync_toc(db, book)
                self.assertTrue(result["synced"])
                self.assertEqual(result["total"], 2)

                rows = (
                    db.query(Chapter)
                    .filter(Chapter.book_id == book.id)
                    .order_by(Chapter.index)
                    .all()
                )
                self.assertEqual(len(rows), 2)
                self.assertTrue(rows[0].remote_url.endswith("/10.html"))

                text = online_reader.ensure_chapter_content(db, book, rows[0])
                self.assertIn("第一段正文。", text)
                self.assertEqual(book.chapter_count, 2)

                # 第二次读取命中正文包，不再发起网络请求
                cached = online_reader.ensure_chapter_content(db, book, rows[0])
                self.assertIn("第二段", cached)
                self.assertGreater(rows[0].content_length, 0)
        finally:
            db.close()

    def test_public_api_reads_online_book(self) -> None:
        from fastapi.testclient import TestClient

        from app.main import app

        db = self._session()
        try:
            source = self._add_source(db)
            book = self._add_book(db, source)
            book_id = int(book.id)
        finally:
            db.close()

        def fake_fetch(url: str, *, headers=None, timeout=10.0) -> str:
            if url.endswith("/book/1"):
                return BOOK_PAGE
            if url.endswith("/toc"):
                return TOC_PAGE
            return CHAPTER_PAGE

        self.cfg.settings.read_api_key = "test-read-key"
        try:
            with patch("app.legado_rules.fetch_text", side_effect=fake_fetch):
                client = TestClient(app)
                headers = {"X-Api-Key": "test-read-key"}

                toc = client.get(f"/api/books/{book_id}/chapters", headers=headers)
                self.assertEqual(toc.status_code, 200)
                payload = toc.json()
                self.assertEqual(payload["total"], 2)
                chapter_id = payload["items"][0]["id"]

                first = client.get(
                    f"/api/books/{book_id}/chapters/{chapter_id}", headers=headers
                )
                self.assertEqual(first.status_code, 200)
                self.assertIn("第一段正文。", first.json()["content"])

                second = client.get(
                    f"/api/books/{book_id}/chapters/{chapter_id}", headers=headers
                )
                self.assertEqual(second.status_code, 200)
                self.assertEqual(second.json()["content"], first.json()["content"])
        finally:
            self.cfg.settings.read_api_key = ""

    def test_sync_toc_is_idempotent_without_force(self) -> None:
        from app import online_reader

        db = self._session()
        try:
            source = self._add_source(db)
            book = self._add_book(db, source)

            def fake_fetch(url: str, *, headers=None, timeout=10.0) -> str:
                return BOOK_PAGE if url.endswith("/book/1") and "toc" not in url else TOC_PAGE

            with patch("app.legado_rules.fetch_text", side_effect=fake_fetch) as mocked:
                online_reader.sync_toc(db, book)
                calls = mocked.call_count
                again = online_reader.sync_toc(db, book)

            self.assertFalse(again["synced"])
            self.assertTrue(again["cached"])
            self.assertEqual(mocked.call_count, calls)
        finally:
            db.close()

    def test_add_to_library_backfills_remote_fields(self) -> None:
        """早期登记的书没有来源字段，再次加入书库时应补齐，否则读不了。"""
        from app import online_search
        from app.models import Book

        db = self._session()
        try:
            source = self._add_source(db)
            old = Book(
                title="测试小说",
                author="测试作者",
                category="未分类",
                source=source.name,
                source_path="online:https://read.example.com/book/1",
            )
            db.add(old)
            db.commit()
            book_id = int(old.id)

            result = online_search.add_to_library(
                db,
                {
                    "source_id": int(source.id),
                    "name": "测试小说",
                    "author": "测试作者",
                    "book_url": "https://read.example.com/book/1",
                    "toc_url": "https://read.example.com/book/1/toc",
                },
            )
            self.assertFalse(result["created"])
            self.assertEqual(result["book_id"], book_id)

            refreshed = db.get(Book, book_id)
            self.assertEqual(refreshed.remote_source_id, int(source.id))
            self.assertEqual(refreshed.remote_book_url, "https://read.example.com/book/1")
            self.assertEqual(refreshed.remote_toc_url, "https://read.example.com/book/1/toc")
        finally:
            db.close()


class SwitchSourceTests(_DbCase):
    """换源：按书名搜其他源、重建目录、作废旧正文缓存。"""

    @staticmethod
    def _fake_fetch(url: str, *, headers=None, timeout=10.0) -> str:
        if "backup.example.com" in url:
            if "/search" in url:
                return SWITCH_SEARCH_PAGE
            if url.endswith("/book/2"):
                return SWITCH_TOC_PAGE
            return SWITCH_CHAPTER_PAGE
        if url.endswith("/book/1"):
            return BOOK_PAGE
        if url.endswith("/toc"):
            return TOC_PAGE
        return CHAPTER_PAGE

    def test_list_switch_sources_matches_title_and_author(self) -> None:
        from app import online_reader

        db = self._session()
        try:
            source = self._add_source(db)
            backup = self._add_source_raw(db, SWITCH_SOURCE)
            book = self._add_book(db, source)

            with patch("app.legado_rules.fetch_text", side_effect=self._fake_fetch):
                result = online_reader.list_switch_sources(db, book)

            self.assertEqual(result["total"], 1)
            item = result["items"][0]
            self.assertEqual(item["source_id"], int(backup.id))
            self.assertEqual(item["source_name"], "备用阅读源")
            self.assertEqual(item["book_url"], "https://backup.example.com/book/2")
            self.assertFalse(item["current"])
        finally:
            db.close()

    def test_switch_source_rebuilds_toc_and_drops_old_content(self) -> None:
        from app import online_reader
        from app.content_store import content_pack_path
        from app.models import Chapter

        db = self._session()
        try:
            source = self._add_source(db)
            backup = self._add_source_raw(db, SWITCH_SOURCE)
            book = self._add_book(db, source)

            with patch("app.legado_rules.fetch_text", side_effect=self._fake_fetch):
                online_reader.sync_toc(db, book)
                old_rows = (
                    db.query(Chapter)
                    .filter(Chapter.book_id == book.id)
                    .order_by(Chapter.index)
                    .all()
                )
                online_reader.ensure_chapter_content(db, book, old_rows[0])
                self.assertTrue(content_pack_path(int(book.id)).is_file())

                result = online_reader.switch_source(
                    db,
                    book,
                    source_id=int(backup.id),
                    book_url="https://backup.example.com/book/2",
                    word_count=999,
                )
                self.assertEqual(result["total"], 3)

                db.refresh(book)
                self.assertEqual(book.remote_source_id, int(backup.id))
                self.assertEqual(book.remote_book_url, "https://backup.example.com/book/2")
                self.assertEqual(book.source, "备用阅读源")
                self.assertEqual(book.chapter_count, 3)
                self.assertEqual(book.latest_chapter, "第三章 终章")
                self.assertEqual(book.word_count, 999)

                rows = (
                    db.query(Chapter)
                    .filter(Chapter.book_id == book.id)
                    .order_by(Chapter.index)
                    .all()
                )
                self.assertEqual([r.title for r in rows], ["第一章 新起点", "第二章 新远方", "第三章 终章"])
                self.assertTrue(all("backup.example.com" in r.remote_url for r in rows))
                self.assertTrue(all(int(r.content_length or 0) == 0 for r in rows))
                # 旧正文包作废，避免旧源正文与新目录错配
                self.assertFalse(content_pack_path(int(book.id)).is_file())

                text = online_reader.ensure_chapter_content(db, book, rows[0])
                self.assertIn("备用源正文。", text)
        finally:
            db.close()

    def test_switch_source_keeps_old_binding_on_failure(self) -> None:
        from app import legado_rules, online_reader
        from app.models import Chapter

        db = self._session()
        try:
            source = self._add_source(db)
            backup = self._add_source_raw(db, SWITCH_SOURCE)
            book = self._add_book(db, source)
            book_id = int(book.id)

            with patch("app.legado_rules.fetch_text", side_effect=self._fake_fetch):
                online_reader.sync_toc(db, book)

            def broken_fetch(url: str, *, headers=None, timeout=10.0) -> str:
                if "backup.example.com" in url:
                    raise legado_rules.RuleError("站点无响应")
                return self._fake_fetch(url, headers=headers, timeout=timeout)

            with patch("app.legado_rules.fetch_text", side_effect=broken_fetch):
                with self.assertRaises(online_reader.OnlineReadError):
                    online_reader.switch_source(
                        db,
                        book,
                        source_id=int(backup.id),
                        book_url="https://backup.example.com/book/2",
                    )

            db.refresh(book)
            self.assertEqual(book.remote_source_id, int(source.id))
            self.assertEqual(book.chapter_count, 2)
            rows = (
                db.query(Chapter)
                .filter(Chapter.book_id == book_id)
                .order_by(Chapter.index)
                .all()
            )
            self.assertEqual(len(rows), 2)
            self.assertTrue(all("/book/1/" in r.remote_url for r in rows))
        finally:
            db.close()

    def test_switch_api_endpoints(self) -> None:
        from fastapi.testclient import TestClient

        from app.auth import require_admin_dep
        from app.main import app

        db = self._session()
        try:
            source = self._add_source(db)
            backup = self._add_source_raw(db, SWITCH_SOURCE)
            book = self._add_book(db, source)
            book_id = int(book.id)
            backup_id = int(backup.id)
        finally:
            db.close()

        app.dependency_overrides[require_admin_dep] = lambda: "tester"
        try:
            with patch("app.legado_rules.fetch_text", side_effect=self._fake_fetch):
                client = TestClient(app)
                listed = client.post(f"/api/admin/online/books/{book_id}/sources", json={})
                self.assertEqual(listed.status_code, 200)
                payload = listed.json()
                self.assertEqual(payload["items"][0]["source_id"], backup_id)

                switched = client.post(
                    f"/api/admin/online/books/{book_id}/switch-source",
                    json={
                        "source_id": backup_id,
                        "book_url": "https://backup.example.com/book/2",
                    },
                )
                self.assertEqual(switched.status_code, 200)
                self.assertEqual(switched.json()["total"], 3)
                self.assertEqual(switched.json()["source_name"], "备用阅读源")
        finally:
            app.dependency_overrides.pop(require_admin_dep, None)


if __name__ == "__main__":
    unittest.main()

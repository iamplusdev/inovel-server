"""在线搜索回归测试：书源规则执行、多源聚合、加入书库幂等。

外部站点一律用 mock 替换，测试不依赖网络。
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HTML_PAGE = """
<html><body><div class="result-list">
  <div class="item">
    <a class="name" href="/book/100">斗罗大陆</a>
    <span class="writer">唐家三少</span>
    <img class="pic" src="/cover/100.jpg">
    <p class="desc">穿越异界，武魂觉醒。</p>
    <span class="last">第1234章 大结局</span>
  </div>
  <div class="item">
    <a class="name" href="/book/101">诡秘之主</a>
    <span class="writer">爱潜水的乌贼</span>
    <p class="desc">蒸汽与机械的时代。</p>
  </div>
</div></body></html>
"""

JSON_PAGE = json.dumps(
    {
        "code": 0,
        "data": {
            "list": [
                {
                    "bookName": "斗罗大陆",
                    "writer": "唐家三少",
                    "url": "/b/100",
                    "cover": "/c/100.jpg",
                    "desc": "另一个源的斗罗",
                },
                {
                    "bookName": "第一序列",
                    "writer": "会说话的肘子",
                    "url": "/b/9",
                    "cover": "",
                    "desc": "废土之上。",
                },
            ]
        },
    },
    ensure_ascii=False,
)

HTML_SOURCE = {
    "bookSourceName": "测试HTML源",
    "bookSourceUrl": "https://html.example.com",
    "searchUrl": "/search?q={{key}}&page={{page}}",
    "ruleSearch": {
        "bookList": ".result-list .item",
        "name": "a.name@text",
        "author": ".writer",
        "bookUrl": "a.name@href",
        "coverUrl": "img.pic@src",
        "intro": ".desc",
        "lastChapter": ".last",
    },
}

JSON_SOURCE = {
    "bookSourceName": "测试JSON源",
    "bookSourceUrl": "https://json.example.com",
    "searchUrl": "/api/s?kw={{key}}&p={{page}}",
    "ruleSearch": {
        "bookList": "$.data.list[*]",
        "name": "$.bookName",
        "author": "$.writer",
        "bookUrl": "$.url",
        "coverUrl": "$.cover",
        "intro": "$.desc",
    },
}


class _DbCase(unittest.TestCase):
    """准备独立的临时数据库与 app 环境。"""

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp())
        os.environ["DATABASE_PATH"] = str(self._tmp / "t.db")
        os.environ["NOVELS_DIR"] = str(self._tmp / "novels")
        os.environ["COVERS_DIR"] = str(self._tmp / "covers")

        from app import config as cfg
        from app import database as dbmod
        from app.auth import reset_auth_cache

        reset_auth_cache()
        cfg.settings.database_path = self._tmp / "t.db"
        cfg.settings.novels_dir = self._tmp / "novels"
        cfg.settings.covers_dir = self._tmp / "covers"
        cfg.settings.database_url = f"sqlite:///{cfg.settings.database_path}"
        cfg.settings.ensure_dirs()
        dbmod.reset_database()
        dbmod.init_db()
        self.cfg = cfg
        self.dbmod = dbmod

    def _session(self):
        return self.dbmod.SessionLocal()

    def _add_source(self, db, raw: dict):
        from app.models import BookSource

        src = BookSource(
            source_url=raw["bookSourceUrl"],
            name=raw["bookSourceName"],
            group_name="",
            source_type=0,
            enabled=True,
            enabled_explore=False,
            weight=0,
            remark="",
            raw_json=json.dumps(raw, ensure_ascii=False),
            last_checked_at="",
            last_check_ok=False,
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
        )
        db.add(src)
        db.commit()
        db.refresh(src)
        return src


class RuleEngineTests(unittest.TestCase):
    def test_html_rules(self) -> None:
        from app import legado_rules as R

        page = R.parse_content(HTML_PAGE, "html")
        nodes = R.select_nodes(".result-list .item", page, kind="html")
        self.assertEqual(len(nodes), 2)
        self.assertEqual(R.read_field("a.name@text", nodes[0], kind="html"), "斗罗大陆")
        self.assertEqual(R.read_field("a.name@href", nodes[0], kind="html"), "/book/100")
        self.assertEqual(R.read_field(".writer", nodes[1], kind="html"), "爱潜水的乌贼")

    def test_json_rules(self) -> None:
        from app import legado_rules as R

        data = R.parse_content(JSON_PAGE, "json")
        nodes = R.select_nodes("$.data.list[*]", data, kind="json")
        self.assertEqual(len(nodes), 2)
        self.assertEqual(R.read_field("$.bookName", nodes[0], kind="json"), "斗罗大陆")
        self.assertEqual(R.read_field("$.desc", nodes[1], kind="json"), "废土之上。")

    def test_regex_suffix_and_chain(self) -> None:
        from app import legado_rules as R

        data = R.parse_content(JSON_PAGE, "json")
        nodes = R.select_nodes("$.data.list[*]", data, kind="json")
        self.assertEqual(R.read_field(r"$.url##/b/(\d+)$", nodes[0], kind="json"), "100")

        page = R.parse_content(HTML_PAGE, "html")
        self.assertEqual(
            R.read_field(".result-list && .item:first-child a.name@text", page, kind="html"),
            "斗罗大陆",
        )
        # || 取首个非空
        self.assertEqual(
            R.read_field(".not-exist || .writer", page.select(".item")[0], kind="html"),
            "唐家三少",
        )

    def test_xpath_rules(self) -> None:
        from app import legado_rules as R

        page = R.parse_content(HTML_PAGE, "html")
        nodes = R.select_nodes('@xpath://div[@class="item"]', page, kind="html")
        self.assertEqual(len(nodes), 2)
        self.assertEqual(
            R.read_field('@xpath:.//a[@class="name"]/text()', nodes[0], kind="html"),
            "斗罗大陆",
        )

    def test_template_and_js_rejected(self) -> None:
        from app import legado_rules as R

        self.assertEqual(
            R.render_template("/s?q={{key}}&p={{page}}", {"key": "abc", "page": 2}),
            "/s?q=abc&p=2",
        )
        with self.assertRaises(R.RuleError):
            R.render_template("@js:java.ajax(url)", {"key": "x"})


class OnlineSearchTests(_DbCase):
    def test_search_aggregates_and_dedupes(self) -> None:
        from app import online_search

        db = self._session()
        try:
            self._add_source(db, HTML_SOURCE)
            self._add_source(db, JSON_SOURCE)

            def fake_fetch(url: str, *, headers=None, timeout=10.0) -> str:
                return JSON_PAGE if "json.example.com" in url else HTML_PAGE

            with patch("app.legado_rules.fetch_text", side_effect=fake_fetch):
                result = online_search.search(db, "斗罗")

            names = [item["name"] for item in result["items"]]
            self.assertIn("斗罗大陆", names)
            self.assertIn("第一序列", names)
            # 两个源都有「斗罗大陆」→ 聚合为一条，sources 长度 2
            douluo = next(i for i in result["items"] if i["name"] == "斗罗大陆")
            self.assertEqual(len(douluo["sources"]), 2)
            self.assertEqual(result["failed"], 0)
        finally:
            db.close()

    def test_source_failure_is_isolated(self) -> None:
        from app import legado_rules
        from app import online_search

        db = self._session()
        try:
            self._add_source(db, JSON_SOURCE)

            with patch(
                "app.legado_rules.fetch_text",
                side_effect=legado_rules.RuleError("站点超时"),
            ):
                result = online_search.search(db, "任意")

            self.assertEqual(result["items"], [])
            self.assertEqual(result["failed"], 1)
            self.assertIn("站点超时", result["sources"][0]["error"])
        finally:
            db.close()

    def test_add_to_library_is_idempotent(self) -> None:
        from app import online_search
        from app.models import Book

        db = self._session()
        try:
            src = self._add_source(db, JSON_SOURCE)
            payload = {
                "source_id": int(src.id),
                "name": "斗罗大陆",
                "author": "唐家三少",
                "book_url": "https://json.example.com/b/100",
                "intro": "穿越异界",
                "last_chapter": "第1234章",
                "word_count": "120万字",
            }
            with patch("app.online_search._save_cover"):
                first = online_search.add_to_library(db, payload)
                second = online_search.add_to_library(db, payload)

            self.assertTrue(first["created"])
            self.assertFalse(second["created"])
            self.assertEqual(first["book_id"], second["book_id"])

            book = db.get(Book, first["book_id"])
            assert book is not None
            self.assertEqual(book.title, "斗罗大陆")
            self.assertEqual(book.author, "唐家三少")
            self.assertEqual(book.word_count, 1_200_000)
            self.assertEqual(book.chapter_count, 0)
            self.assertTrue(online_search.is_online_book(book))
            # 列表序列化要标记为「在线不可读」
            from app.serializers import book_list_item

            item = book_list_item(book)
            self.assertTrue(item["online"])
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()

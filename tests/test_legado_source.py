"""Legado 书源导入端点回归测试。

覆盖曾经的回归：该端点被改成「仅会话鉴权」后，Legado「网络导入」是裸 GET
（不带 Cookie、不带自定义 Header），拿到的是 401 的 {"detail": ...} 对象，
手机端 gson 报 `Expected BEGIN_ARRAY but was BEGIN_OBJECT`，表现为书源导不进来。

兼容 `python -m unittest tests.test_legado_source` 与 `pytest tests/test_legado_source.py`。
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path


class LegadoSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp())
        os.environ["DATABASE_PATH"] = str(self._tmp / "t.db")
        os.environ["NOVELS_DIR"] = str(self._tmp / "novels")
        os.environ["COVERS_DIR"] = str(self._tmp / "covers")

        from app import book_source_settings
        from app import config as cfg
        from app import database as dbmod
        from app.auth import reset_auth_cache

        reset_auth_cache()
        cfg.settings.database_path = self._tmp / "t.db"
        cfg.settings.novels_dir = self._tmp / "novels"
        cfg.settings.covers_dir = self._tmp / "covers"
        cfg.settings.database_url = f"sqlite:///{cfg.settings.database_path}"
        # 复刻线上最常见的坑：没配对外地址（仍是回环），但用户用域名访问
        cfg.settings.public_base_url = "http://127.0.0.1:7311"
        cfg.settings.read_api_key = "test-read-key"
        cfg.settings.trusted_hosts = []
        cfg.settings.ensure_dirs()
        dbmod.reset_database()
        dbmod.init_db()

        self.cfg = cfg
        self.book_source_settings = book_source_settings
        book_source_settings.reset_token()  # 每个用例用干净令牌

    def tearDown(self) -> None:
        # 恢复全局配置，避免影响同进程内其他测试文件
        self.cfg.settings.read_api_key = ""
        self.cfg.settings.public_base_url = "http://127.0.0.1:7311"
        self.cfg.settings.trusted_hosts = []

    def _client(self):
        from fastapi.testclient import TestClient

        from app.main import app

        return TestClient(app, base_url="https://novel.iamplus.cn")

    def test_source_file_requires_token(self) -> None:
        """匿名拿裸 URL 必须 401；带订阅令牌才返回数组（Legado 只认数组）。"""
        token = self.book_source_settings.ensure_token()
        with self._client() as c:
            self.assertEqual(c.get("/legado_book_source.json").status_code, 401)
            r = c.get("/legado_book_source.json", params={"token": token})
            self.assertEqual(r.status_code, 200)
            self.assertIsInstance(r.json(), list)
            self.assertGreaterEqual(len(r.json()), 1)

    def test_source_file_uses_request_host_when_base_is_loopback(self) -> None:
        """PUBLIC_BASE_URL 仍是 127.0.0.1 时，书源要指向用户实际访问的域名。"""
        token = self.book_source_settings.ensure_token()
        with self._client() as c:
            src = c.get("/legado_book_source.json", params={"token": token}).json()[0]
            self.assertEqual(src["bookSourceUrl"], "https://novel.iamplus.cn")
            self.assertEqual(json.loads(src["header"])["X-Api-Key"], "test-read-key")
            self.assertTrue(src["searchUrl"].startswith("/api/legado/search"))

    def test_source_file_accepts_read_api_key(self) -> None:
        """?key= 与 X-Api-Key 等价（自动化/脚本直接拉书源）。"""
        with self._client() as c:
            r = c.get("/legado_book_source.json", params={"key": "test-read-key"})
            self.assertEqual(r.status_code, 200)
            self.assertIsInstance(r.json(), list)

    def test_reset_token_invalidates_old_link(self) -> None:
        """重置令牌后旧导入链接立即失效。"""
        old = self.book_source_settings.ensure_token()
        self.book_source_settings.reset_token()
        with self._client() as c:
            self.assertEqual(
                c.get("/legado_book_source.json", params={"token": old}).status_code, 401
            )


if __name__ == "__main__":
    unittest.main()

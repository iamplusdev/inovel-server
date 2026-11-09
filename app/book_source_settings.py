"""书源订阅设置：手机 Legado「网络导入」链接使用的独立令牌。

不复用 READ_API_KEY —— 那个密钥能读全库正文，放进 URL 风险更高；
订阅令牌只放行书源列表，可在管理页随时重置。
"""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime
from pathlib import Path

from .config import settings

FILE_NAME = "book_sources.json"
DEFAULTS: dict = {"subscribe_token": "", "created_at": ""}


def config_path() -> Path:
    parent = settings.database_path.parent
    parent.mkdir(parents=True, exist_ok=True)
    return parent / FILE_NAME


def load() -> dict:
    data = dict(DEFAULTS)
    path = config_path()
    if not path.is_file():
        return data
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return data
    if isinstance(raw, dict):
        data["subscribe_token"] = str(raw.get("subscribe_token") or "")
        data["created_at"] = str(raw.get("created_at") or "")
    return data


def _save(data: dict) -> None:
    path = config_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # Windows 上部分文件系统不支持 POSIX 权限位


def ensure_token() -> str:
    """首次访问时生成令牌并持久化。"""
    data = load()
    if data["subscribe_token"]:
        return data["subscribe_token"]
    data["subscribe_token"] = secrets.token_urlsafe(24)
    data["created_at"] = datetime.now().isoformat(timespec="seconds")
    _save(data)
    return data["subscribe_token"]


def reset_token() -> str:
    data = load()
    data["subscribe_token"] = secrets.token_urlsafe(24)
    data["created_at"] = datetime.now().isoformat(timespec="seconds")
    _save(data)
    return data["subscribe_token"]

"""极简 WebDAV 客户端（仅标准库）：读写 Legado 的 bookProgress 进度文件。

只实现同步需要的 GET/PUT/MKCOL/PROPFIND，不引入额外依赖。
"""
from __future__ import annotations

import base64
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


class WebDavError(Exception):
    """WebDAV 操作失败（文案可直接展示给用户）。"""


class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
    """只允许同域重定向，避免账号密码被带到其它主机。"""

    def __init__(self, netloc: str) -> None:
        self.netloc = netloc.lower()

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlparse(newurl).netloc.lower() != self.netloc:
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass
class WebDavClient:
    base_url: str
    username: str = ""
    password: str = ""
    timeout: float = 6.0
    verify_tls: bool = True

    def __post_init__(self) -> None:
        parsed = urllib.parse.urlparse(self.base_url)
        self._netloc = parsed.netloc
        ctx = ssl.create_default_context()
        if not self.verify_tls:
            # 允许 NAS 自签名证书（仅用户显式开启时）
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        self._opener = urllib.request.build_opener(
            _SameHostRedirect(self._netloc),
            urllib.request.HTTPSHandler(context=ctx),
        )
        self._auth = ""
        if self.username:
            raw = f"{self.username}:{self.password}".encode("utf-8")
            self._auth = "Basic " + base64.b64encode(raw).decode("ascii")

    def _request(
        self,
        method: str,
        url: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes]:
        hdrs = {"User-Agent": "novel-server/1.0"}
        if self._auth:
            hdrs["Authorization"] = self._auth
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            try:
                body = e.read()
            except Exception:  # noqa: BLE001
                body = b""
            finally:
                # HTTPError 也是文件对象，读完需关闭，避免 ResourceWarning
                try:
                    e.close()
                except Exception:  # noqa: BLE001
                    pass
            return e.code, body
        except urllib.error.URLError as e:
            raise WebDavError(f"连接失败：{e.reason}") from e
        except TimeoutError as e:
            raise WebDavError("连接超时") from e

    def get(self, url: str) -> bytes | None:
        status, body = self._request("GET", url)
        if status == 200:
            return body
        if status == 404:
            return None
        if status in (401, 403):
            raise WebDavError("WebDAV 认证失败，请检查账号/密码")
        raise WebDavError(f"读取失败（HTTP {status}）")

    def put(self, url: str, data: bytes, content_type: str = "application/json") -> None:
        status, _ = self._request(
            "PUT", url, data=data, headers={"Content-Type": content_type}
        )
        if status in (200, 201, 204):
            return
        if status in (401, 403):
            raise WebDavError("WebDAV 认证失败，请检查账号/密码")
        raise WebDavError(f"写入失败（HTTP {status}）")

    def mkcol(self, url: str) -> None:
        """创建目录；已存在（405）或父目录问题（409）按成功处理。"""
        status, _ = self._request("MKCOL", url)
        if status in (200, 201, 204, 405, 409):
            return
        if status in (401, 403):
            raise WebDavError("WebDAV 认证失败，请检查账号/密码")
        raise WebDavError(f"创建目录失败（HTTP {status}）")

    def list_files(self, url: str) -> list[str]:
        """列出目录下直接子文件（Depth: 1），返回解码后的文件名。

        用于批量进度同步：一次 PROPFIND 拿到 Legado 写过的所有 bookProgress 文件，
        避免为书库里每本书都发一次 GET。只取 .json、跳过目录项本身。
        """
        body = (
            b'<?xml version="1.0" encoding="utf-8"?>'
            b'<d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/></d:prop></d:propfind>'
        )
        status, payload = self._request(
            "PROPFIND",
            url,
            data=body,
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
        )
        if status in (401, 403):
            raise WebDavError("WebDAV 认证失败，请检查账号/密码")
        if status == 404:
            return []
        if status not in (200, 207):
            raise WebDavError(f"列出目录失败（HTTP {status}）")
        try:
            import xml.etree.ElementTree as ET

            root = ET.fromstring(payload)
        except Exception as exc:  # noqa: BLE001
            raise WebDavError(f"WebDAV 目录响应无法解析：{exc}") from exc

        names: list[str] = []
        for node in root.iter("{DAV:}href"):
            raw = (node.text or "").strip()
            if not raw:
                continue
            name = urllib.parse.unquote(urllib.parse.urlparse(raw).path.rstrip("/").split("/")[-1])
            if name.lower().endswith(".json"):
                names.append(name)
        return names

    def propfind(self, url: str) -> bool:
        body = (
            b'<?xml version="1.0" encoding="utf-8"?>'
            b'<d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/></d:prop></d:propfind>'
        )
        status, _ = self._request(
            "PROPFIND",
            url,
            data=body,
            headers={"Depth": "0", "Content-Type": "application/xml; charset=utf-8"},
        )
        if status in (200, 207):
            return True
        if status == 404:
            return False
        if status in (401, 403):
            raise WebDavError("WebDAV 认证失败，请检查账号/密码")
        raise WebDavError(f"探测目录失败（HTTP {status}）")

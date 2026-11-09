"""Legado 书源解析 / 规范化 / 结构校验 / 连通性探测。

Legado 的规则（JSONPath / XPath / CSS / JS）由手机 App 解释执行，服务端不实现
规则引擎；这里只判断「导入前结构是否完整、站点是否可达」，不试跑搜索/目录/正文。
"""
from __future__ import annotations

import ipaddress
import json
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

# 四大规则块及 Legado 常用子规则（缺失只告警，因为部分书源依赖默认规则/JS 拼接）
RULE_BLOCKS: dict[str, tuple[str, ...]] = {
    "ruleSearch": ("bookList", "name", "bookUrl"),
    "ruleBookInfo": ("name", "tocUrl"),
    "ruleToc": ("chapterList", "chapterName", "chapterUrl"),
    "ruleContent": ("content",),
}
_TEMPLATE = re.compile(r"\{\{.*?\}\}|<js>|@js:", re.IGNORECASE)
_UA = "Mozilla/5.0 (compatible; iNovelBookSourceValidator/1.0)"


class SourceParseError(ValueError):
    """书源 JSON 无法解析。"""


def _as_bool(value: Any, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "on"):
        return True
    if text in ("false", "0", "no", "off"):
        return False
    return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _decode_loose(text: str) -> Any:
    """解析 JSON；兼容 Legado 分享时常见的「多个对象直接拼接」文本。"""
    raw = (text or "").strip().lstrip("\ufeff")
    if not raw:
        raise SourceParseError("书源内容为空")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    values: list[Any] = []
    pos = 0
    while pos < len(raw):
        while pos < len(raw) and (raw[pos].isspace() or raw[pos] in ",;"):
            pos += 1
        if pos >= len(raw):
            break
        try:
            value, end = decoder.raw_decode(raw, pos)
        except json.JSONDecodeError as exc:
            raise SourceParseError(f"书源 JSON 解析失败（位置 {pos}）：{exc.msg}") from exc
        values.append(value)
        pos = end
    if not values:
        raise SourceParseError("书源内容为空")
    return values if len(values) > 1 else values[0]


def parse_sources(payload: Any) -> list[dict]:
    """把 Legado 导出的内容解析成书源 dict 列表。

    支持：数组 / 单对象 / {bookSources|sources:[...]} / JSON 文本 / 多对象拼接文本。
    """
    if isinstance(payload, str):
        payload = _decode_loose(payload)
    if isinstance(payload, dict):
        for key in ("bookSources", "sources", "items", "data"):
            nested = payload.get(key)
            if isinstance(nested, (list, dict, str)):
                return parse_sources(nested)
        return [payload]
    if isinstance(payload, list):
        out: list[dict] = []
        for item in payload:
            out.extend(parse_sources(item))
        return out
    raise SourceParseError("书源内容必须是 JSON 对象或数组")


def normalize_source(data: dict) -> dict:
    """规范化为 Legado 书源对象：保留所有未知字段，只修正已知字段的类型。"""
    if not isinstance(data, dict):
        raise SourceParseError("书源必须是 JSON 对象")
    src = dict(data)
    url = str(src.get("bookSourceUrl") or "").strip()
    name = str(src.get("bookSourceName") or "").strip()
    if not url or not name:
        raise SourceParseError("书源缺少 bookSourceUrl 或 bookSourceName")
    src["bookSourceUrl"] = url
    src["bookSourceName"] = name
    src["bookSourceGroup"] = str(src.get("bookSourceGroup") or "").strip()
    src["bookSourceType"] = _as_int(src.get("bookSourceType"), 0)
    src["weight"] = _as_int(src.get("weight"), 0)
    src["enabled"] = _as_bool(src.get("enabled"), True)
    src["enabledExplore"] = _as_bool(src.get("enabledExplore"), False)
    header = src.get("header")
    if isinstance(header, dict):
        src["header"] = json.dumps(header, ensure_ascii=False)
    elif header is not None and not isinstance(header, str):
        src["header"] = str(header)
    return src


def extract_fields(src: dict) -> dict:
    """从规范化书源对象中提取列表用字段。"""
    return {
        "source_url": str(src.get("bookSourceUrl") or "").strip(),
        "name": str(src.get("bookSourceName") or "").strip(),
        "group_name": str(src.get("bookSourceGroup") or "").strip()[:100],
        "source_type": _as_int(src.get("bookSourceType"), 0),
        "enabled": _as_bool(src.get("enabled"), True),
        "enabled_explore": _as_bool(src.get("enabledExplore"), False),
        "weight": _as_int(src.get("weight"), 0),
    }


def header_map(value: Any) -> dict[str, str]:
    """Legado header 可能是 JSON 字符串或对象。"""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items()}


def _is_private_host(host: str) -> bool:
    """SSRF 防护：内网/环回/保留地址不发起探测。"""
    if (host or "").lower() in ("localhost", "localhost.localdomain"):
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, OSError):
        return False  # 解析失败交给 urlopen 给出真实错误
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except (ValueError, IndexError):
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return True
    return False


def probe_url(url: str, headers: dict[str, str] | None = None, timeout: float = 5.0) -> dict:
    """探测书源站点是否可达（只 GET 站点根，最多读 64KB，不执行任何规则）。"""
    result: dict = {"ok": False, "status": 0, "ms": 0, "final_url": "", "error": "", "skipped": ""}
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        result["error"] = "仅支持 http/https 地址"
        return result
    if _is_private_host(parsed.hostname or ""):
        result["skipped"] = "内网/环回地址已跳过探测（安全限制）"
        return result
    req = urllib.request.Request(url, headers={"User-Agent": _UA, **(headers or {})})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as resp:
            status = int(getattr(resp, "status", 0) or 0)
            final_url = str(resp.geturl())
            resp.read(65536)
        result.update(ok=200 <= status < 400, status=status, final_url=final_url)
    except urllib.error.HTTPError as exc:
        # 4xx/5xx 说明站点有响应（常见于需要 UA/登录），不算网络不可达
        result.update(status=exc.code, error=f"HTTP {exc.code}（站点有响应）")
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["ms"] = int((time.monotonic() - started) * 1000)
    return result


def fetch_text(url: str, *, timeout: float = 10.0, limit: int = 2 * 1024 * 1024) -> str:
    """从网络地址拉取书源 JSON 文本（供「网络导入」使用）。

    与探测同样的安全限制：仅 http/https、拒绝内网地址、限制响应体大小。
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise SourceParseError("导入地址需为 http/https 开头的完整 URL")
    if _is_private_host(parsed.hostname or ""):
        raise SourceParseError("出于安全限制，不允许从内网/环回地址导入书源")
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json, text/plain, */*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as resp:
            raw = resp.read(limit + 1)
    except Exception as exc:  # noqa: BLE001
        raise SourceParseError(f"拉取书源失败：{type(exc).__name__}: {exc}") from exc
    if len(raw) > limit:
        raise SourceParseError("书源文件超过 2 MB，请改用本地文件导入")
    return raw.decode("utf-8-sig", errors="replace")


def validate_source(
    data: dict,
    *,
    probe: bool = False,
    duplicate_urls: set[str] | None = None,
) -> dict:
    """结构校验 + 可选连通性探测。

    返回 {ok, errors, warnings, checks, probe}；errors 为硬性错误，
    warnings 是「可能能用但建议修」的提示（规则子字段缺失、无模板变量等）。
    """
    src = data if isinstance(data, dict) else {}
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[dict] = []

    def add(label: str, ok: bool, detail: str, *, level: str = "error") -> None:
        checks.append({"label": label, "ok": bool(ok), "level": "ok" if ok else level, "detail": detail})
        if not ok:
            (errors if level == "error" else warnings).append(f"{label}：{detail}")

    url = str(src.get("bookSourceUrl") or "").strip()
    name = str(src.get("bookSourceName") or "").strip()
    parsed = urllib.parse.urlparse(url)
    url_ok = parsed.scheme in ("http", "https") and bool(parsed.netloc)
    add("书源名称", bool(name), name or "缺少 bookSourceName")
    add("书源地址", url_ok, url if url_ok else "bookSourceUrl 需为 http/https 开头的完整地址")

    search = str(src.get("searchUrl") or "").strip()
    explore = src.get("exploreUrl")
    add("搜索地址", bool(search or explore), search or "缺少 searchUrl（也没有发现地址）")
    if search and not _TEMPLATE.search(search):
        warnings.append("搜索地址：searchUrl 中未见 {{key}} 等模板变量，可能无法按关键词搜索")

    for block, required in RULE_BLOCKS.items():
        rule = src.get(block)
        if rule is None or rule == "" or rule == {}:
            add(block, False, f"缺少 {block} 规则块", level="warning")
            continue
        if not isinstance(rule, dict):
            add(block, False, f"{block} 必须是 JSON 对象", level="error")
            continue
        missing = [k for k in required if not str(rule.get(k) or "").strip()]
        add(block, not missing, "字段完整" if not missing else f"缺少子规则 {', '.join(missing)}", level="warning")

    header = src.get("header")
    if header not in (None, "", "{}"):
        if isinstance(header, dict):
            header_ok = True
        elif isinstance(header, str):
            try:
                header_ok = isinstance(json.loads(header), dict)
            except (TypeError, ValueError):
                header_ok = False
        else:
            header_ok = False
        add("请求头 header", header_ok, "合法 JSON 对象" if header_ok else "header 需为 JSON 对象字符串")

    source_type = _as_int(src.get("bookSourceType"), 0)
    add("书源类型", source_type in (0, 1, 2, 3), f"bookSourceType={source_type}（0 文本/1 音频/2 图片/3 文件）", level="warning")
    if duplicate_urls and url in duplicate_urls:
        add("书源唯一性", False, "该 bookSourceUrl 已存在（导入时可勾选覆盖）")

    probe_result: dict | None = None
    if probe and url_ok:
        probe_result = probe_url(url, header_map(header), timeout=5.0)
        if probe_result.get("skipped"):
            add("站点连通性", False, probe_result["skipped"], level="warning")
        elif probe_result.get("ok"):
            add("站点连通性", True, f"HTTP {probe_result['status']}，{probe_result['ms']} ms")
        else:
            add(
                "站点连通性",
                False,
                probe_result.get("error") or f"HTTP {probe_result.get('status')}，{probe_result.get('ms')} ms",
                level="warning",
            )

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
        "probe": probe_result,
    }

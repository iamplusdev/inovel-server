"""Legado 书源规则执行（实用子集），供服务端「在线搜索」使用。

Legado 的规则原本由手机 App 内置的 JS 引擎解释执行。服务端只实现**搜索一本书**
所必需的常用子集，超出范围时抛 :class:`RuleError`，由调用方跳过该字段或该书源，
绝不做静默的错误猜测。

支持范围
--------
- 模板变量：``{{key}}`` ``{{page}}`` ``{{baseUrl}}`` ``{{bookUrl}}`` 等
- 规则来源：``@css:`` / ``@xpath:`` / ``@json:`` / 无前缀
  （无前缀时 HTML 按 CSS、JSON 按 JSONPath，与 Legado 默认行为一致）
- 规则串联：``&&``（上一步结果作为下一步输入）、``||``（取首个非空）
- 后缀修饰：``##正则``（提取，支持 ``$1``）、``###正则##替换``
- CSS 属性后缀：``a@href``、``div@text``、``@html``、``@ownText``、``@textNodes``
- JSONPath 子集：``$.a.b``、``$.a[*]``、``$.a[0]``、``$..a``、``$.a[*].b``

不支持（抛 RuleError）
----------------------
``@js:`` / ``<js>``、JS 模板表达式、``@put`` / ``@get`` 变量、
URL 后带 ``,{"method":"POST"}`` 配置的搜索接口。
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote, urljoin

from .scrapers import http_util

__all__ = [
    "RuleError",
    "render_template",
    "select_nodes",
    "read_field",
    "fetch_text",
    "search_source",
]

# 单字段最大长度：书源规则偶尔命中整页文本，截断避免把巨串塞进响应
MAX_FIELD_CHARS = 2000
# 单次搜索最多返回条数，防止某个书源返回整站目录把我方内存打满
MAX_RESULTS = 30

_JS_MARK = re.compile(r"(?:^@js\s*:|<js>|@js\s*:)", re.IGNORECASE)
_TEMPLATE = re.compile(r"\{\{(.*?)\}\}", re.S)
# CSS 选择器末尾的 @attr（只认常规属性名与 Legado 内置关键字）
_CSS_ATTR = re.compile(
    r"^(?P<sel>.*?)\s*@(?P<attr>textNodes|ownText|text|html|content|href|src|[A-Za-z_][\w\-]*)$",
    re.S,
)
# Legado 允许在 URL 后追加 ,{"method":"POST",...} 形式的请求配置
_URL_OPTS = re.compile(r",\s*\{.*\}\s*$", re.S)


class RuleError(ValueError):
    """规则无法执行（含不支持的 JS 规则、站点不可达、选择器语法错误）。"""


# ---------------------------------------------------------------- 模板变量


def render_template(text: str, variables: dict[str, Any], *, strict: bool = False) -> str:
    """替换 ``{{...}}`` 模板变量。

    ``strict=True`` 时遇到未知变量直接报错（用于必须正确的 URL 拼接）；
    默认把未知变量替换成空串，避免模板原文被拼进请求地址。
    """
    if not text:
        return ""
    if _JS_MARK.search(text):
        raise RuleError("规则含 @js / <js> 表达式，服务端不支持")
    if "<js>" in text:
        raise RuleError("规则含 <js> 表达式，服务端不支持")

    def repl(match: re.Match[str]) -> str:
        expr = match.group(1).strip()
        if expr in variables:
            value = variables[expr]
            return "" if value is None else str(value)
        if strict:
            raise RuleError(f"未知模板变量：{{{{{expr}}}}}")
        return ""

    return _TEMPLATE.sub(repl, text)


# ---------------------------------------------------------------- JSONPath 子集


def _parse_json_path(expr: str) -> list[tuple[str, Any]] | None:
    """把 ``$.a.b[*]`` 解析成 token 列表；不是 JSONPath 时返回 None。"""
    expr = (expr or "").strip()
    if not expr.startswith("$"):
        return None
    tokens: list[tuple[str, Any]] = []
    i, n = 1, len(expr)
    while i < n:
        if expr.startswith("..", i):
            i += 2
            j = i
            while j < n and (expr[j].isalnum() or expr[j] in "_-"):
                j += 1
            if j == i:
                return None
            tokens.append(("desc", expr[i:j]))
            i = j
        elif expr[i] == ".":
            i += 1
            j = i
            while j < n and expr[j] not in ".[":
                j += 1
            if j == i:
                return None
            tokens.append(("key", expr[i:j]))
            i = j
        elif expr[i] == "[":
            j = expr.find("]", i)
            if j < 0:
                return None
            inner = expr[i + 1 : j].strip()
            if inner == "*":
                tokens.append(("all", None))
            elif inner[:1] in ("'", '"') and inner[-1:] == inner[:1]:
                tokens.append(("key", inner[1:-1]))
            else:
                try:
                    tokens.append(("index", int(inner)))
                except ValueError:
                    tokens.append(("key", inner))
            i = j + 1
        else:
            return None
    return tokens


def _descend(node: Any, key: str, out: list[Any]) -> None:
    """递归下降找同名 key（保持文档顺序）。"""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                out.append(v)
            if isinstance(v, (dict, list)):
                _descend(v, key, out)
    elif isinstance(node, list):
        for item in node:
            if isinstance(item, (dict, list)):
                _descend(item, key, out)


def json_query(data: Any, expr: str) -> list[Any]:
    """执行 JSONPath 子集，返回命中的节点列表。"""
    tokens = _parse_json_path(expr)
    if tokens is None:
        return []
    nodes: list[Any] = [data]
    for kind, arg in tokens:
        out: list[Any] = []
        for node in nodes:
            if kind == "key":
                if isinstance(node, dict) and arg in node:
                    out.append(node[arg])
            elif kind == "index":
                if isinstance(node, list) and -len(node) <= arg < len(node):
                    out.append(node[arg])
            elif kind == "all":
                if isinstance(node, list):
                    out.extend(node)
                elif isinstance(node, dict):
                    out.extend(node.values())
            elif kind == "desc":
                _descend(node, arg, out)
        nodes = out
        if not nodes:
            return []
    return nodes


# ---------------------------------------------------------------- 节点取值


def _node_text(node: Any, attr: str | None = None) -> str:
    """把节点转成字符串；attr 用于 ``@text`` / ``@href`` 这类后缀。

    HTML 节点有两种形态：CSS 选择走 bs4 Tag（有 get_text），XPath 走 lxml
    Element（有 itertext / text_content），这里统一兼容，避免取值时报属性缺失。
    """
    if node is None:
        return ""
    if isinstance(node, str):
        return node.strip()
    if isinstance(node, bool):
        return "true" if node else "false"
    if isinstance(node, (int, float)):
        return str(node)
    if isinstance(node, (dict, list)):
        return json.dumps(node, ensure_ascii=False)

    is_bs4 = hasattr(node, "get_text")
    if attr in (None, "", "text"):
        if is_bs4:
            return node.get_text().strip()
        return "".join(node.itertext()).strip()
    if attr == "ownText":
        if is_bs4:
            return "".join(c for c in node.contents if isinstance(c, str)).strip()
        return (node.text or "").strip()
    if attr == "textNodes":
        if is_bs4:
            return "".join(str(s) for s in node.find_all(string=True)).strip()
        return "".join(node.itertext()).strip()
    if attr == "html":
        if is_bs4:
            return node.decode_contents().strip()
        from lxml import etree

        return "".join(etree.tostring(c, encoding="unicode") for c in node).strip()
    value = node.get(attr)
    return str(value).strip() if value is not None else ""


def _as_text(node: Any) -> str:
    return _node_text(node)


def _headers(source: dict) -> dict[str, str]:
    raw = source.get("header")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if v is not None}


# ---------------------------------------------------------------- 规则求值


def _split_suffix(rule: str) -> tuple[str, str | None, tuple[str, str] | None]:
    """拆出 ``##正则`` / ``###正则##替换`` 后缀。"""
    if "###" in rule:
        core, _, tail = rule.partition("###")
        pattern, _, repl = tail.partition("##")
        return core.strip(), None, (pattern, repl)
    if "##" in rule:
        core, _, pattern = rule.partition("##")
        return core.strip(), pattern, None
    return rule.strip(), None, None


def _apply_mods(value: Any, extract: str | None, replace: tuple[str, str] | None) -> str:
    text = _as_text(value)
    if replace:
        pattern, repl = replace
        try:
            return re.sub(pattern, repl, text)
        except re.error:
            return text
    if extract:
        try:
            match = re.search(extract, text)
        except re.error:
            return text
        if not match:
            return ""
        return match.group(1) if match.groups() else match.group(0)
    return text


def _css_select(node: Any, selector: str) -> list[Any]:
    """CSS 选择；支持末尾 ``@attr``。lxml 节点会先转成 bs4 再选。"""
    selector = (selector or "").strip()
    if not selector:
        return []
    attr: str | None = None
    match = _CSS_ATTR.match(selector)
    if match and "[" not in match.group("sel")[-1:]:
        selector = match.group("sel").strip() or "*"
        attr = match.group("attr")
    if not hasattr(node, "select"):
        # && 链里可能出现「先 XPath 再 CSS」，把 lxml 节点转回 bs4
        if callable(getattr(node, "xpath", None)):
            from lxml import etree

            node = parse_content(etree.tostring(node, encoding="unicode"), "html")
        elif isinstance(node, str):
            node = parse_content(node, "html")
        if not hasattr(node, "select"):
            return []
    try:
        tags = node.select(selector)
    except Exception as exc:  # noqa: BLE001  soupsieve 语法错误种类较多
        raise RuleError(f"CSS 选择器无效：{selector}（{exc}）") from exc
    if attr:
        return [_node_text(tag, attr) for tag in tags]
    return list(tags)


def _lxml_root(node: Any):
    """把节点转成 lxml 根，供 XPath 查询。

    注意不能对 lxml Element 调 str()：那会得到 ``<Element div at 0x...>`` 而不是
    HTML，导致后续 XPath 全部落空；bs4 Tag 才需要先序列化成 HTML 再解析。
    """
    from lxml import html as lxml_html

    if isinstance(node, str):
        return lxml_html.fromstring(node)
    if callable(getattr(node, "xpath", None)):
        # 已经是 lxml 节点，直接复用，保持 .// 相对语义。
        # 必须用 callable 判断：bs4 的 Tag/BeautifulSoup 有 __getattr__，
        # 访问任何未知属性（含 xpath/tag）都返回 None，hasattr 会误判。
        return node
    try:
        return lxml_html.fragment_fromstring(str(node), create_parent="div")
    except Exception:  # noqa: BLE001
        return lxml_html.fromstring(str(node))


def _xpath_select(node: Any, expr: str) -> list[Any]:
    expr = (expr or "").strip()
    if not expr:
        return []
    try:
        root = _lxml_root(node)
        result = root.xpath(expr)
    except Exception as exc:  # noqa: BLE001
        raise RuleError(f"XPath 无效：{expr}（{exc}）") from exc
    if not isinstance(result, list):
        return [result]
    out: list[Any] = []
    for item in result:
        # lxml 的 _ElementUnicodeResult 是 str 子类，统一转 str 便于后续处理
        out.append(str(item) if isinstance(item, str) else item)
    return out


def _json_select(node: Any, expr: str) -> list[Any]:
    return json_query(node, (expr or "").strip())


def _select_single(
    rule: str,
    node: Any,
    *,
    kind: str,
    variables: dict[str, Any],
    base_url: str,
) -> list[Any]:
    rule = (rule or "").strip()
    if not rule or node is None:
        return []
    if _JS_MARK.search(rule) or "<js>" in rule:
        raise RuleError("规则含 @js / <js> 表达式，服务端不支持")

    core, extract, replace = _split_suffix(rule)
    if not core:
        return []
    core = render_template(core, variables)

    if core.startswith("@css:"):
        nodes = _css_select(_html_node(node), core[5:].strip())
    elif core.startswith("@xpath:"):
        nodes = _xpath_select(_html_node(node), core[7:].strip())
    elif core.startswith("@json:"):
        nodes = _json_select(node, core[6:].strip())
    elif core.startswith("@") and ":" not in core:
        # 直接用 @text / @href 取当前节点的值
        attr = core[1:].strip()
        if attr:
            nodes = [_node_text(node, attr)]
        else:
            nodes = []
    elif kind == "json":
        tokens = _parse_json_path(core)
        nodes = [] if tokens is None else json_query(node, core)
    else:
        nodes = _css_select(_html_node(node), core)

    if extract or replace:
        nodes = [_apply_mods(n, extract, replace) for n in nodes]
    return [n for n in nodes if n is not None and n != ""]


def _html_node(node: Any) -> Any:
    """把 ``@css:`` 作用在字符串节点上时，先包成一个 HTML 片段。"""
    if isinstance(node, str):
        try:
            return parse_content(node, "html")
        except RuleError:
            return node
    return node


def select_nodes(
    rule: str,
    content: Any,
    *,
    kind: str = "html",
    variables: dict[str, Any] | None = None,
    base_url: str = "",
) -> list[Any]:
    """按规则选出节点列表。``kind`` 取 ``html`` 或 ``json``。"""
    variables = variables or {}
    rule = (rule or "").strip()
    if not rule or content is None:
        return []
    # || 备选：取第一个有结果的规则
    if "||" in rule:
        for part in rule.split("||"):
            got = select_nodes(part, content, kind=kind, variables=variables, base_url=base_url)
            if got:
                return got
        return []
    # && 串联：上一步的结果作为下一步的输入
    if "&&" in rule:
        nodes: list[Any] = [content]
        for part in rule.split("&&"):
            nxt: list[Any] = []
            for node in nodes:
                nxt.extend(
                    _select_single(part, node, kind=kind, variables=variables, base_url=base_url)
                )
            nodes = nxt
            if not nodes:
                return []
        return nodes
    return _select_single(rule, content, kind=kind, variables=variables, base_url=base_url)


def read_field(
    rule: str,
    node: Any,
    *,
    kind: str = "html",
    variables: dict[str, Any] | None = None,
    base_url: str = "",
) -> str:
    """读取单个字段（取首个非空值，截断到 MAX_FIELD_CHARS）。"""
    if not (rule or "").strip():
        return ""
    nodes = select_nodes(rule, node, kind=kind, variables=variables, base_url=base_url)
    for item in nodes:
        text = _as_text(item).strip()
        if text:
            return text[:MAX_FIELD_CHARS]
    return ""


# ---------------------------------------------------------------- 抓取与解析


def parse_content(text: str, kind: str) -> Any:
    """把响应文本解析成可查询的节点（HTML 树 / JSON 对象）。"""
    if kind == "json":
        try:
            return json.loads(text)
        except (TypeError, ValueError) as exc:
            raise RuleError(f"响应不是合法 JSON：{exc}") from exc
    from bs4 import BeautifulSoup

    try:
        return BeautifulSoup(text, "lxml")
    except Exception:  # noqa: BLE001  lxml 缺失或解析失败时退回标准库
        return BeautifulSoup(text, "html.parser")


def detect_kind(text: str) -> str:
    """按首字符判断响应是 JSON 还是 HTML。"""
    head = (text or "").lstrip()[:1]
    if head in ("{", "["):
        try:
            json.loads(text)
            return "json"
        except (TypeError, ValueError):
            return "html"
    return "html"


def fetch_text(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> str:
    """抓取书源页面；拒绝内网地址（SSRF 防护），失败统一抛 RuleError。"""
    if not http_util.is_public_http_url(url):
        raise RuleError("书源地址不是公网 http/https，出于安全限制已拒绝请求")
    try:
        return http_util.http_get(url, headers=headers, timeout=max(1, int(timeout)))
    except Exception as exc:  # noqa: BLE001  http_util 抛 HttpError/URLError 等
        raise RuleError(f"请求失败：{type(exc).__name__}: {exc}") from exc


def _split_url_options(raw: str) -> tuple[str, dict]:
    """拆出 Legado 的 ``,{"method":"POST"}`` 请求配置（仅识别，不执行）。"""
    raw = (raw or "").strip()
    if not _URL_OPTS.search(raw):
        return raw, {}
    idx = raw.rfind(",")
    if idx < 0:
        return raw, {}
    try:
        opts = json.loads(raw[idx + 1 :].strip())
    except (TypeError, ValueError):
        return raw, {}
    if not isinstance(opts, dict):
        return raw, {}
    return raw[:idx].strip(), opts


def _absolute(base: str, value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if value.startswith(("http://", "https://")):
        return value
    return urljoin(base.rstrip("/") + "/", value.lstrip("/"))


def search_source(
    source: dict,
    keyword: str,
    *,
    page: int = 1,
    timeout: float = 10.0,
    limit: int = MAX_RESULTS,
) -> list[dict]:
    """用单个书源搜索，返回规范化后的书籍列表。

    规则缺失或站点不可达时抛 :class:`RuleError`，由调用方决定是否忽略该源。
    """
    keyword = (keyword or "").strip()
    if not keyword:
        return []
    base = str(source.get("bookSourceUrl") or "").strip()
    raw_search = str(source.get("searchUrl") or "").strip()
    if not raw_search:
        raise RuleError("书源未配置 searchUrl")

    search_url, opts = _split_url_options(raw_search)
    method = str(opts.get("method") or "GET").upper()
    if method != "GET":
        raise RuleError(f"暂不支持 {method} 类型的搜索接口，请改用 GET 书源")

    variables = {
        # {{key}} 在 Legado 里是已 URL 编码的，这里保持一致
        "key": quote(keyword, safe=""),
        "keyword": keyword,
        "page": page,
        "baseUrl": base.rstrip("/"),
        "base": base.rstrip("/"),
    }
    path = render_template(search_url, variables, strict=False)
    if not path:
        raise RuleError("searchUrl 渲染后为空，请检查 {{key}} 等模板变量")
    url = _absolute(base, path)
    headers = _headers(source)

    text = fetch_text(url, headers=headers, timeout=timeout)
    kind = detect_kind(text)
    content = parse_content(text, kind)

    rule = source.get("ruleSearch") or {}
    if not isinstance(rule, dict):
        raise RuleError("ruleSearch 规则必须是对象")
    list_rule = str(rule.get("bookList") or "").strip()
    if not list_rule:
        raise RuleError("书源缺少 ruleSearch.bookList")

    nodes = select_nodes(list_rule, content, kind=kind, variables=variables, base_url=base)
    if not nodes:
        return []

    items: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for node in nodes[: max(1, limit)]:
        name = read_field(str(rule.get("name") or ""), node, kind=kind, variables=variables, base_url=base)
        if not name:
            continue
        author = read_field(str(rule.get("author") or ""), node, kind=kind, variables=variables, base_url=base)
        book_url = _absolute(base, read_field(str(rule.get("bookUrl") or ""), node, kind=kind, variables=variables, base_url=base))
        key = (name, author)
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "name": name,
                "author": author,
                "book_url": book_url,
                "toc_url": _absolute(
                    base,
                    read_field(str(rule.get("tocUrl") or ""), node, kind=kind, variables=variables, base_url=base),
                ),
                "cover_url": _absolute(
                    base,
                    read_field(str(rule.get("coverUrl") or ""), node, kind=kind, variables=variables, base_url=base),
                ),
                "intro": read_field(str(rule.get("intro") or ""), node, kind=kind, variables=variables, base_url=base),
                "kind": read_field(str(rule.get("kind") or ""), node, kind=kind, variables=variables, base_url=base),
                "last_chapter": read_field(
                    str(rule.get("lastChapter") or ""), node, kind=kind, variables=variables, base_url=base
                ),
                "word_count": read_field(
                    str(rule.get("wordCount") or ""), node, kind=kind, variables=variables, base_url=base
                ),
            }
        )
    return items

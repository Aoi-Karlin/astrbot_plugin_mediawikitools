"""通用 MediaWiki Action API 客户端，不依赖 AstrBot，方便独立单元测试。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import aiohttp

try:
    from .text_utils import html_to_text, wikitext_to_plain
except ImportError:  # 独立运行/单测时作为普通模块导入
    from text_utils import html_to_text, wikitext_to_plain


class MediaWikiAPIError(Exception):
    """MediaWiki API 返回错误，或请求本身失败时抛出。"""


@dataclass
class SearchResult:
    pageid: int
    title: str
    snippet: str


@dataclass
class PageInfo:
    pageid: int
    title: str
    length: int
    touched: str
    fullurl: str


@dataclass
class ExtractResult:
    text: str
    source: str  # "extract" | "html" | "wikitext"


class MediaWikiClient:
    """封装单个 MediaWiki 站点 api.php 的常用 Action API 调用。"""

    def __init__(
        self,
        api_url: str,
        timeout: float = 10.0,
        user_agent: str = "AstrBotMediaWikiTools/1.0",
    ):
        self._api_url = api_url
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._headers = {"User-Agent": user_agent}
        self._session: aiohttp.ClientSession | None = None

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self._timeout, headers=self._headers
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def _request(self, params: dict[str, Any]) -> dict[str, Any]:
        params = {**params, "format": "json"}
        session = self._get_session()
        try:
            async with session.get(self._api_url, params=params) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except aiohttp.ClientError as e:
            raise MediaWikiAPIError(f"请求 MediaWiki API 失败: {e}") from e
        except Exception as e:  # noqa: BLE001 - 统一转换为可读错误
            raise MediaWikiAPIError(f"解析 MediaWiki API 响应失败: {e}") from e

        if isinstance(data, dict) and "error" in data:
            err = data["error"]
            info = err.get("info") if isinstance(err, dict) else str(err)
            raise MediaWikiAPIError(f"MediaWiki API 返回错误: {info}")
        return data

    async def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        """按关键词搜索页面，返回标题与摘要片段列表。"""
        data = await self._request(
            {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": max(1, min(limit, 50)),
                "srprop": "snippet",
            }
        )
        results = data.get("query", {}).get("search", [])
        return [
            SearchResult(
                pageid=item.get("pageid", 0),
                title=item.get("title", ""),
                snippet=_strip_search_snippet(item.get("snippet", "")),
            )
            for item in results
        ]

    async def get_wikitext(self, title: str) -> str:
        """获取页面的 wikitext 原文。"""
        data = await self._request(
            {
                "action": "query",
                "prop": "revisions",
                "titles": title,
                "rvprop": "content",
                "rvslots": "main",
                "redirects": 1,
            }
        )
        page = _first_page(data)
        if page is None or "missing" in page:
            raise MediaWikiAPIError(f"页面不存在: {title}")
        revisions = page.get("revisions") or []
        if not revisions:
            raise MediaWikiAPIError(f"页面没有可用的修订内容: {title}")
        slots = revisions[0].get("slots", {})
        main_slot = slots.get("main", {})
        return main_slot.get("*", "") or main_slot.get("content", "")

    async def get_html(self, title: str) -> str:
        """获取页面解析后的 HTML。"""
        data = await self._request(
            {
                "action": "parse",
                "page": title,
                "prop": "text",
                "redirects": 1,
            }
        )
        parse = data.get("parse")
        if not parse:
            raise MediaWikiAPIError(f"页面不存在或解析失败: {title}")
        text = parse.get("text", {})
        # parse.text 在 v2 结构里是字符串, 旧版为 {"*": "..."}
        if isinstance(text, dict):
            return text.get("*", "")
        return text or ""

    async def get_pageinfo(self, title: str) -> PageInfo:
        """获取页面基本信息：pageid、标题、长度、最后修改时间、完整链接。"""
        data = await self._request(
            {
                "action": "query",
                "prop": "info",
                "titles": title,
                "inprop": "url",
                "redirects": 1,
            }
        )
        page = _first_page(data)
        if page is None or "missing" in page:
            raise MediaWikiAPIError(f"页面不存在: {title}")
        return PageInfo(
            pageid=page.get("pageid", 0),
            title=page.get("title", title),
            length=page.get("length", 0),
            touched=page.get("touched", ""),
            fullurl=page.get("fullurl", ""),
        )

    async def get_extract(self, title: str, intro_only: bool = False) -> ExtractResult:
        """获取页面纯文本摘要，带三级 fallback，解决 infobox 开头页面 extract 为空的问题。

        1. TextExtracts (prop=extracts explaintext) —— 最快最干净
        2. 若为空: 解析 HTML 并剥标签取正文
        3. 若仍拿不到: 用 wikitext 原文做粗略清洗兜底
        """
        params = {
            "action": "query",
            "prop": "extracts",
            "titles": title,
            "explaintext": 1,
            "redirects": 1,
        }
        if intro_only:
            params["exintro"] = 1

        try:
            data = await self._request(params)
        except MediaWikiAPIError:
            # 请求本身失败（如 TextExtracts 扩展未安装），继续尝试 HTML fallback
            data = None

        if data is not None:
            page = _first_page(data)
            if page is not None and "missing" in page:
                # 页面确实不存在，HTML/wikitext fallback 也不会有结果，直接报错
                raise MediaWikiAPIError(f"页面不存在: {title}")
            if page is not None:
                extract = (page.get("extract") or "").strip()
                if extract:
                    return ExtractResult(text=extract, source="extract")

        try:
            html = await self.get_html(title)
            text = html_to_text(html).strip()
            if text:
                return ExtractResult(text=text, source="html")
        except MediaWikiAPIError:
            pass

        wikitext = await self.get_wikitext(title)
        text = wikitext_to_plain(wikitext).strip()
        if not text:
            raise MediaWikiAPIError(f"无法从任何来源提取页面正文: {title}")
        return ExtractResult(text=text, source="wikitext")


def _first_page(data: dict[str, Any]) -> dict[str, Any] | None:
    pages = data.get("query", {}).get("pages")
    if not pages:
        return None
    if isinstance(pages, dict):
        return next(iter(pages.values()), None)
    if isinstance(pages, list):
        return pages[0] if pages else None
    return None


def _strip_search_snippet(snippet: str) -> str:
    """去掉搜索摘要里的 <span class="searchmatch"> 等高亮标签。"""
    return re.sub(r"<[^>]+>", "", snippet)

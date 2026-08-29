"""AstrBot 插件: MediaWiki API Tool

将任意白名单内的 MediaWiki 站点 Action API 封装为 LLM Tool 和聊天指令，
供 Agent / 用户获取百科页面的搜索结果、纯文本正文、wikitext、HTML 与基本信息。
"""

from __future__ import annotations

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .mediawiki_client import MediaWikiAPIError, MediaWikiClient
from .text_utils import truncate


@register(
    "mediawikitools",
    "astrbot_plugin_mediawikitools",
    "将白名单内的 MediaWiki 站点 API 封装为 LLM Tool，解决 extract 为空的问题",
    "1.0.0",
)
class MediaWikiToolsPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._sites_config: dict[str, str] = dict(self.config.get("sites", {}) or {})
        self._default_site: str = self.config.get("default_site", "") or ""
        self._timeout: float = float(self.config.get("request_timeout", 10) or 10)
        self._user_agent: str = self.config.get(
            "user_agent", "AstrBotMediaWikiTools/1.0"
        )
        self._max_extract_length: int = int(
            self.config.get("max_extract_length", 1500) or 1500
        )
        self._clients: dict[str, MediaWikiClient] = {}

        if self._default_site and self._default_site not in self._sites_config:
            logger.error(
                "MediaWikiTools: default_site '%s' 不在 sites 白名单中，"
                "调用时必须显式指定 site 参数" % self._default_site
            )
            self._default_site = ""

        if not self._sites_config:
            logger.error(
                "MediaWikiTools: 未配置任何 sites，插件功能将不可用，请在配置中添加白名单站点"
            )

    def _resolve_site_name(self, site: str | None) -> str:
        """解析用户传入的站点简称，未指定时回退默认站点，非法名称抛出可读错误。"""
        name = (site or self._default_site or "").strip()
        if not name:
            raise MediaWikiAPIError(
                "未指定 site 且未配置 default_site，请指定站点简称。"
                f"可用站点: {', '.join(self._sites_config) or '(无)'}"
            )
        if name not in self._sites_config:
            raise MediaWikiAPIError(
                f"站点 '{name}' 不在白名单中。可用站点: {', '.join(self._sites_config) or '(无)'}"
            )
        return name

    def _get_client(self, site: str | None) -> MediaWikiClient:
        name = self._resolve_site_name(site)
        client = self._clients.get(name)
        if client is None:
            api_url = self._sites_config[name]
            client = MediaWikiClient(
                api_url, timeout=self._timeout, user_agent=self._user_agent
            )
            self._clients[name] = client
        return client

    async def terminate(self) -> None:
        """插件卸载/停用时关闭所有站点的 HTTP 会话。"""
        for client in self._clients.values():
            try:
                await client.close()
            except Exception as e:  # noqa: BLE001
                logger.error(f"MediaWikiTools: 关闭 client 失败: {e}")

    # ------------------------------------------------------------------
    # LLM Tools
    # ------------------------------------------------------------------

    @filter.llm_tool(name="wiki_list_sites")
    async def wiki_list_sites(self, event: AstrMessageEvent) -> str:
        """列出当前允许查询的 MediaWiki 站点简称列表（白名单）。

        Returns:
            站点简称列表的文本描述。
        """
        if not self._sites_config:
            return "当前没有配置任何可用的 MediaWiki 站点。"
        lines = [
            f"- {name}" + ("（默认）" if name == self._default_site else "")
            for name in self._sites_config
        ]
        return "可用站点:\n" + "\n".join(lines)

    @filter.llm_tool(name="wiki_search")
    async def wiki_search(
        self, event: AstrMessageEvent, query: str, site: str = "", limit: int = 5
    ) -> str:
        """在 MediaWiki 站点中按关键词搜索相关页面标题。

        Args:
            query(string): 搜索关键词。
            site(string): 站点简称，留空使用默认站点，可通过 wiki_list_sites 查看可用站点。
            limit(number): 返回结果数量上限，默认 5。
        """
        try:
            client = self._get_client(site or None)
            results = await client.search(query, limit=limit)
        except MediaWikiAPIError as e:
            return f"搜索失败: {e}"
        except Exception as e:  # noqa: BLE001
            logger.error(f"MediaWikiTools wiki_search 异常: {e}")
            return f"搜索时发生未知错误: {e}"

        if not results:
            return f"没有找到与 '{query}' 相关的页面。"
        lines = [f"- {r.title}: {r.snippet}" for r in results]
        return "\n".join(lines)

    @filter.llm_tool(name="wiki_get_extract")
    async def wiki_get_extract(
        self,
        event: AstrMessageEvent,
        title: str,
        site: str = "",
        intro_only: bool = False,
    ) -> str:
        """获取 MediaWiki 页面的纯文本正文，自动处理信息框开头页面 extract 为空的问题。

        Args:
            title(string): 页面标题。
            site(string): 站点简称，留空使用默认站点。
            intro_only(boolean): 是否只返回导言部分，默认 False（返回全文）。
        """
        try:
            client = self._get_client(site or None)
            result = await client.get_extract(title, intro_only=intro_only)
        except MediaWikiAPIError as e:
            return f"获取正文失败: {e}"
        except Exception as e:  # noqa: BLE001
            logger.error(f"MediaWikiTools wiki_get_extract 异常: {e}")
            return f"获取正文时发生未知错误: {e}"

        text = truncate(result.text, self._max_extract_length)
        return text

    @filter.llm_tool(name="wiki_get_wikitext")
    async def wiki_get_wikitext(
        self, event: AstrMessageEvent, title: str, site: str = ""
    ) -> str:
        """获取 MediaWiki 页面的 wikitext 原始文本（包含模板、链接标记等未渲染内容）。

        Args:
            title(string): 页面标题。
            site(string): 站点简称，留空使用默认站点。
        """
        try:
            client = self._get_client(site or None)
            wikitext = await client.get_wikitext(title)
        except MediaWikiAPIError as e:
            return f"获取 wikitext 失败: {e}"
        except Exception as e:  # noqa: BLE001
            logger.error(f"MediaWikiTools wiki_get_wikitext 异常: {e}")
            return f"获取 wikitext 时发生未知错误: {e}"

        return truncate(wikitext, self._max_extract_length)

    @filter.llm_tool(name="wiki_get_html")
    async def wiki_get_html(
        self, event: AstrMessageEvent, title: str, site: str = ""
    ) -> str:
        """获取 MediaWiki 页面解析后的 HTML 内容。

        Args:
            title(string): 页面标题。
            site(string): 站点简称，留空使用默认站点。
        """
        try:
            client = self._get_client(site or None)
            html = await client.get_html(title)
        except MediaWikiAPIError as e:
            return f"获取 HTML 失败: {e}"
        except Exception as e:  # noqa: BLE001
            logger.error(f"MediaWikiTools wiki_get_html 异常: {e}")
            return f"获取 HTML 时发生未知错误: {e}"

        return truncate(html, self._max_extract_length)

    @filter.llm_tool(name="wiki_get_pageinfo")
    async def wiki_get_pageinfo(
        self, event: AstrMessageEvent, title: str, site: str = ""
    ) -> str:
        """获取 MediaWiki 页面的基本信息：pageid、标题、长度、最后修改时间、链接。

        Args:
            title(string): 页面标题。
            site(string): 站点简称，留空使用默认站点。
        """
        try:
            client = self._get_client(site or None)
            info = await client.get_pageinfo(title)
        except MediaWikiAPIError as e:
            return f"获取页面信息失败: {e}"
        except Exception as e:  # noqa: BLE001
            logger.error(f"MediaWikiTools wiki_get_pageinfo 异常: {e}")
            return f"获取页面信息时发生未知错误: {e}"

        return (
            f"标题: {info.title}\n"
            f"pageid: {info.pageid}\n"
            f"长度: {info.length} 字节\n"
            f"最后修改时间: {info.touched}\n"
            f"链接: {info.fullurl}"
        )

    # ------------------------------------------------------------------
    # 聊天指令（人工测试用，不经过 LLM）
    # ------------------------------------------------------------------

    @filter.command_group("wiki")
    def wiki_group(self):
        """MediaWiki 查询指令组。"""

    @wiki_group.command("sites")
    async def cmd_sites(self, event: AstrMessageEvent):
        """列出白名单站点。"""
        yield event.plain_result(await self.wiki_list_sites(event))

    @wiki_group.command("search")
    async def cmd_search(self, event: AstrMessageEvent, query: str, site: str = ""):
        """搜索页面: /wiki search <关键词> [站点]"""
        query = query.strip()
        if not query:
            yield event.plain_result("请提供搜索关键词，例如: /wiki search 猫")
            return
        yield event.plain_result(await self.wiki_search(event, query, site=site))

    @wiki_group.command("get")
    async def cmd_get(self, event: AstrMessageEvent, title: str, site: str = ""):
        """获取页面正文: /wiki get <标题> [站点]"""
        title = title.strip()
        if not title:
            yield event.plain_result("请提供页面标题，例如: /wiki get 猫")
            return
        yield event.plain_result(await self.wiki_get_extract(event, title, site=site))

    @wiki_group.command("wikitext")
    async def cmd_wikitext(self, event: AstrMessageEvent, title: str, site: str = ""):
        """获取页面 wikitext: /wiki wikitext <标题> [站点]"""
        title = title.strip()
        if not title:
            yield event.plain_result("请提供页面标题，例如: /wiki wikitext 猫")
            return
        yield event.plain_result(await self.wiki_get_wikitext(event, title, site=site))

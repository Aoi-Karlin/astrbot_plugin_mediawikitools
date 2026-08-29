"""HTML / wikitext 转纯文本的清洗工具，不依赖 AstrBot，方便独立单元测试。"""

from __future__ import annotations

import re
from html.parser import HTMLParser

# 解析 HTML 时需要整体跳过的标签（含内部所有内容）
_SKIP_TAGS = {"style", "script", "table", "sup"}
# 会被当作分隔/换行的块级标签
_BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}


class _WikiHTMLExtractor(HTMLParser):
    """从 MediaWiki parse 出的 HTML 中提取正文纯文本。

    跳过 infobox / 导航框等 table，跳过 style/script，保留段落换行。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self._chunks.append(data)

    def get_text(self) -> str:
        raw = "".join(self._chunks)
        # 合并多余空白/空行
        lines = [line.strip() for line in raw.splitlines()]
        lines = [line for line in lines if line]
        return "\n".join(lines)


def html_to_text(html: str) -> str:
    """将 MediaWiki parse API 返回的 HTML 转为可读纯文本，跳过信息框/样式/脚本。"""
    parser = _WikiHTMLExtractor()
    parser.feed(html)
    return parser.get_text()


_TEMPLATE_RE = re.compile(r"\{\{[^{}]*\}\}")
_REF_RE = re.compile(r"<ref[^>]*>.*?</ref>|<ref[^>]*/>", re.DOTALL | re.IGNORECASE)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_LINK_RE = re.compile(r"\[\[([^\[\]|]*\|)?([^\[\]]+)\]\]")
_BOLD_ITALIC_RE = re.compile(r"'{2,5}")
_EXTLINK_RE = re.compile(r"\[https?://[^\s\]]+\s+([^\]]+)\]")


def wikitext_to_plain(wikitext: str) -> str:
    """粗略清洗 wikitext 原文为纯文本，作为 extract/HTML 都失败时的最后兜底。"""
    text = wikitext
    # 模板可能嵌套，多轮清除直到不再变化
    for _ in range(5):
        new_text = _TEMPLATE_RE.sub("", text)
        if new_text == text:
            break
        text = new_text
    text = _COMMENT_RE.sub("", text)
    text = _REF_RE.sub("", text)
    text = _EXTLINK_RE.sub(r"\1", text)
    text = _LINK_RE.sub(r"\2", text)
    text = _HTML_TAG_RE.sub("", text)
    text = _BOLD_ITALIC_RE.sub("", text)

    lines = [line.strip() for line in text.splitlines()]
    lines = [
        line
        for line in lines
        if line and not line.startswith("|") and not line.startswith("!")
    ]
    return "\n".join(lines)


def truncate(text: str, max_len: int) -> str:
    """按字符数截断文本，超长时追加省略提示。"""
    if max_len <= 0 or len(text) <= max_len:
        return text
    return text[:max_len].rstrip() + f"\n...(内容已截断，完整长度 {len(text)} 字符)"

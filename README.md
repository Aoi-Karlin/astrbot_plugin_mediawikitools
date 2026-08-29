# AstrBot MediaWiki Tools

一个用于 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 的 MediaWiki API 工具插件。

本插件将 MediaWiki Action API 封装为 AstrBot 的 **LLM Tool**，使支持 Tool Calling 的模型可以主动搜索和读取 MediaWiki 网站内容。同时提供 `/wiki` 聊天指令，方便人工测试。

> 插件名称：`astrbot_plugin_mediawikitools`  
> 当前版本：`1.0.0`

## ✨ 功能

- 🔎 搜索 MediaWiki 页面
- 📄 获取页面纯文本正文
- 📝 获取页面原始 Wikitext
- 🌐 获取页面解析后的 HTML
- ℹ️ 获取页面基本信息
- 🤖 提供给 LLM Agent 自动调用
- 💬 提供 `/wiki` 指令进行人工查询
- 🛡️ 使用站点白名单限制可访问的 MediaWiki API
- 🔄 页面正文提取支持多级 fallback
- ✂️ 自动限制返回文本长度，避免过度占用上下文
- 🌐 支持同时配置多个 MediaWiki 站点

---

## 📦 安装

将插件目录放入 AstrBot 的插件目录，例如：

```text
AstrBot/
└── data/
    └── plugins/
        └── astrbot_plugin_mediawikitools/
            ├── __init__.py
            ├── main.py
            ├── mediawiki_client.py
            ├── text_utils.py
            └── _conf_schema.json
```

然后在 AstrBot 中加载/重载插件即可。

### 依赖

插件使用：

- Python 3
- AstrBot
- `aiohttp`

`aiohttp` 通常已经作为 AstrBot 的依赖存在。如果运行时提示缺少该模块，可以手动安装：

```bash
pip install aiohttp
```

---

## ⚙️ 配置

插件通过 `_conf_schema.json` 提供配置项。

默认配置：

```json
{
  "sites": {
    "zhwiki": "https://zh.wikipedia.org/w/api.php",
    "moegirl": "https://mzh.moegirl.org.cn/api.php"
  },
  "default_site": "zhwiki",
  "request_timeout": 10,
  "user_agent": "AstrBotMediaWikiTools/1.0",
  "max_extract_length": 1500
}
```

### `sites`

MediaWiki 站点白名单。

键是供 Tool 和指令使用的站点简称，值是对应站点的 `api.php` 地址。

例如：

```json
{
  "sites": {
    "zhwiki": "https://zh.wikipedia.org/w/api.php",
    "moegirl": "https://mzh.moegirl.org.cn/api.php",
    "example": "https://example.org/w/api.php"
  }
}
```

**只有这里配置的站点才能被插件访问。**

这也是插件有意采用的安全措施，可以避免让 LLM 根据用户输入直接访问任意 URL。

### `default_site`

未指定 `site` 时使用的默认站点。

例如：

```json
"default_site": "zhwiki"
```

此时：

```text
/wiki search 洛天依
```

等价于在 `zhwiki` 站点执行搜索。

如果配置的 `default_site` 不存在于 `sites` 中，插件会自动禁用默认站点，此后必须显式指定站点。

### `request_timeout`

MediaWiki API 请求超时时间，单位为秒。

默认：

```json
"request_timeout": 10
```

如果网络环境较慢，可以适当增加。

### `user_agent`

访问 MediaWiki API 时使用的 User-Agent。

默认：

```text
AstrBotMediaWikiTools/1.0
```

如果部署环境允许，建议设置为包含联系方式的 User-Agent，例如：

```text
AstrBotMediaWikiTools/1.0 (admin@example.com)
```

### `max_extract_length`

返回给用户或 LLM 的内容最大字符数。

默认：

```json
"max_extract_length": 1500
```

超过限制后会自动截断，例如：

```text
……
...(内容已截断，完整长度 5273 字符)
```

这样可以避免一次读取超长 Wiki 页面导致聊天刷屏或占用大量上下文。

---

# 🤖 LLM Tools

插件注册了以下 LLM Tools。

## `wiki_list_sites`

列出当前配置的 MediaWiki 站点。

返回示例：

```text
可用站点:
- zhwiki（默认）
- moegirl
```

---

## `wiki_search`

根据关键词搜索页面。

参数：

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---:|---|
| `query` | string | 必填 | 搜索关键词 |
| `site` | string | `""` | 站点简称，留空使用默认站点 |
| `limit` | number | `5` | 返回结果数量，最大 50 |

例如 LLM 可以调用：

```text
wiki_search(
    query="洛天依",
    site="zhwiki",
    limit=5
)
```

返回：

```text
- 洛天依: 洛天依是上海禾念信息科技有限公司...
- VOCALOID: VOCALOID是一种...
```

---

## `wiki_get_extract`

获取页面的纯文本正文。

参数：

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---:|---|
| `title` | string | 必填 | 页面标题 |
| `site` | string | `""` | 站点简称 |
| `intro_only` | boolean | `false` | 是否只获取导言 |

例如：

```text
wiki_get_extract(
    title="洛天依",
    site="zhwiki"
)
```

### 多级正文提取

这是本插件比较重要的一项设计。

MediaWiki 的 `extracts` API 在某些页面上可能返回空内容，尤其是页面开头主要由 Infobox 等模板构成的情况。

因此插件采用三级 fallback：

```text
TextExtracts
     │
     │ 为空/不可用
     ▼
解析后的 HTML
     │
     │ 仍无法获取
     ▼
原始 Wikitext
```

具体流程：

1. 优先使用 `prop=extracts&explaintext=1`
2. 如果失败，获取解析后的 HTML 并清理标签
3. 如果仍然失败，获取原始 Wikitext 并进行粗略清洗

因此，即使目标 Wiki 没有安装 TextExtracts 扩展，也有机会正常获取页面内容。

---

## `wiki_get_wikitext`

获取页面原始 Wikitext。

例如：

```text
wiki_get_wikitext(
    title="洛天依",
    site="zhwiki"
)
```

返回的内容可能包含：

```text
{{Infobox ...
| name = 洛天依
}}

'''洛天依'''是...
[[VOCALOID]]
```

也就是说，这个 Tool 获取的是**未渲染的原始 Wiki 源码**。

---

## `wiki_get_html`

获取 MediaWiki 解析后的 HTML。

例如：

```text
wiki_get_html(
    title="洛天依",
    site="zhwiki"
)
```

适用于需要查看页面 Wiki 渲染结果的场景。

---

## `wiki_get_pageinfo`

获取页面基本信息。

包括：

- 页面标题
- `pageid`
- 页面长度
- 最后修改时间
- 页面完整 URL

返回示例：

```text
标题: 洛天依
pageid: 123456
长度: 12345 字节
最后修改时间: 2026-08-29T10:00:00Z
链接: https://zh.wikipedia.org/wiki/洛天依
```

---

# 💬 聊天指令

除了 LLM Tool 之外，还可以直接使用 `/wiki` 指令。

## 查看站点

```text
/wiki sites
```

---

## 搜索页面

```text
/wiki search <关键词> [站点]
```

例如：

```text
/wiki search 猫
```

指定站点：

```text
/wiki search 猫 zhwiki
```

---

## 获取页面正文

```text
/wiki get <页面标题> [站点]
```

例如：

```text
/wiki get 猫
```

或者：

```text
/wiki get 洛天依 zhwiki
```

---

## 获取 Wikitext

```text
/wiki wikitext <页面标题> [站点]
```

例如：

```text
/wiki wikitext 洛天依 zhwiki
```

---

# 🛡️ 安全设计

插件不会直接允许用户或 LLM 指定任意 API URL。

例如，即使用户尝试：

```text
site="https://example.com/api.php"
```

也不会直接访问该地址。

插件首先会检查：

```text
site
 ↓
sites 白名单
 ↓
匹配成功？
 ├─ 是 → 访问对应 API
 └─ 否 → 返回错误
```

这样可以避免 LLM Tool 被利用成任意 URL 请求工具。

因此，如果希望增加一个 Wiki 站点，应当先在 `sites` 中添加：

```json
{
  "sites": {
    "mywiki": "https://example.com/w/api.php"
  }
}
```

然后使用：

```text
site="mywiki"
```

---

# 🧹 文本清理

插件包含独立的 `text_utils.py`，用于将 MediaWiki 内容转换成适合人类/LLM 阅读的纯文本。

HTML 清理时会跳过：

- `<table>`
- `<style>`
- `<script>`
- `<sup>`

同时保留段落、标题、列表等基本换行结构。

Wikitext fallback 则会尝试移除：

- 模板 `{{...}}`
- `<ref>`
- HTML 标签
- Wiki 链接
- 外部链接标记
- 粗体/斜体标记
- HTML 注释

需要注意的是，Wikitext 本身是一种复杂的标记语言，因此 fallback 清理属于**粗略转换**，不能替代真正的 MediaWiki Parser。

---

# 🏗️ 项目结构

```text
astrbot_plugin_mediawikitools/
├── __init__.py
├── main.py
├── mediawiki_client.py
├── text_utils.py
├── _conf_schema.json
└── pytest.ini
```

### `main.py`

AstrBot 插件入口。

负责：

- 注册插件
- 注册 LLM Tools
- 注册 `/wiki` 指令
- 管理 MediaWiki Client
- 处理配置
- 错误处理

### `mediawiki_client.py`

独立的 MediaWiki Action API Client。

负责：

- API 请求
- 页面搜索
- Wikitext 获取
- HTML 获取
- 页面信息获取
- Extract 获取
- API 错误处理

该模块尽量不依赖 AstrBot，方便单独进行测试。

### `text_utils.py`

HTML/Wikitext → 纯文本的工具函数。

### `_conf_schema.json`

AstrBot 插件配置 Schema。

---

# 🔧 工作流程

以 LLM 查询一个 Wiki 页面为例：

```text
用户
 │
 │ “介绍一下洛天依”
 ▼
LLM
 │
 │ wiki_search
 ▼
MediaWiki API
 │
 │ 搜索结果
 ▼
LLM
 │
 │ wiki_get_extract
 ▼
MediaWiki API
 │
 │ 页面正文
 ▼
LLM
 │
 ▼
生成回答
```

如果 `extracts` 无法正常工作：

```text
wiki_get_extract
       │
       ▼
 TextExtracts
       │
    失败/为空
       ▼
 Parsed HTML
       │
    失败/为空
       ▼
   Wikitext
       │
       ▼
  文本清理
```

---

# 🧪 测试

项目中的 `mediawiki_client.py` 和 `text_utils.py` 尽可能保持独立，因此文本处理部分可以独立进行单元测试。

如果项目中配置了测试环境，可以使用：

```bash
pytest
```

---

# ⚠️ 注意事项

### 1. 需要 MediaWiki API

目标网站必须提供 MediaWiki Action API，并且配置的 URL 应指向对应的：

```text
api.php
```

例如：

```text
https://zh.wikipedia.org/w/api.php
```

### 2. 不保证所有 Wiki 都具有相同 API 能力

不同 MediaWiki 站点可能安装了不同的扩展，因此：

- `extracts` 可能不可用
- 某些页面可能无法正常解析
- API 权限可能有所不同
- 页面内容格式可能存在差异

本插件已经针对 `extracts` 不可用的情况提供 fallback，但不能保证所有特殊 Wiki 页面都能完美转换。

### 3. 页面内容会被截断

为了保护 LLM 上下文和避免刷屏，正文、Wikitext 和 HTML 都受到 `max_extract_length` 限制。

如果需要更长内容，可以调大配置值：

```json
"max_extract_length": 5000
```

不过不建议无限增大。

---

# 📄 License

GNU Affero license 3.0

---

# 🙏 致谢

- [AstrBot](https://github.com/AstrBotDevs/AstrBot)
- [MediaWiki](https://www.mediawiki.org/)
- MediaWiki Action API
- `aiohttp`

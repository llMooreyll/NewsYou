# NewsYou · AI 分类改造计划

> 状态：设计已定案，待实施。本文档是本次改造的唯一设计基线，代码改动以此为准。
> 基线：数据目录为 `data/collected/` 的 2026-10-13 那一期；最后更新 2026-10-14。
>
> 本次相对上一版的变化：加入奇客 Solidot 源、Telegram 频道由 7 个扩到 18 个、
> `nature` 由两值扩为三值（新增「不定期长文」）、频道级性质改为配置可编辑。
> 并因此取消了 `seq` 标号（见 §7）。
>
> 18 个频道的性质归属与其余 4 项待定事项已全部确认，决策记录见 §12。
>
> 2026-10-14 对基线代码做了一轮只读复审，结果补进 §14：6 项必须在批次 1 之前解决
> 的问题（§14.1）、8 项会改变行为但原计划未写明的后果（§14.2）、事实订正（§14.3）。
> **§14.1 未定案前不要开工。**

---

## 1. 目标与非目标

**目标**

1. 每条信息在采集阶段就有一个稳定标号（`id`），供 AI 引用与页面锚点使用。
2. AI 读完全部信息后输出分类，页面按分类展示，每条带来源引用，可点击跳转到原条目。
3. 新闻、分享/讨论、不定期长文三类信息分开处理，不混进同一个分类。
4. 信源性质（新闻 / 分享 / 长文）暴露在 `config.toml` 里，改分类流程不需要改代码。

**非目标**

- 不改阶段 1 的抓取解析逻辑（除新增源与盖章字段）。
- 不改阶段 2a（Telegram 去广告/去重/合并）的行为。
- 不做跨天的类目对比（类目名由模型自由命名，跨天不可比，已接受）。
- 不重构渲染器的既有面板样式。

---

## 2. 信源变更

### 2.1 新增源：奇客 Solidot（新闻）

已验证的事实（2026-10-14 实测）：

| 项 | 结果 |
|---|---|
| RSS 地址 | `https://www.solidot.org/index.rss` |
| HTTP | 200，`application/xml;charset=utf-8`，23.6 KB |
| 格式 | RSS 2.0，一次 20 条 |
| 摘要 | **自带**，200–450 字（`description`），现代引擎解析正常 |
| 日期 | `pubDate` 带 `+0800`，可直接用 |
| 正文提取 | trafilatura 可提取（564 字），但**带页面噪声**：「solidot 新版网站常见问题，请点击这里查看。」「本文已被查看 813 次」 |

结论：走 `[[feeds]]` 通用 RSS 路径，**不写采集代码**；`fulltext = "none"`
（RSS 摘要已足够分类与展示，正文页噪声会污染「阅读全文」，且省 20 次请求）。

```toml
[[feeds]]
id       = "solidot"
name     = "奇客 Solidot"
url      = "https://www.solidot.org/index.rss"
fulltext = "none"
limit    = 20
```

性质为新闻（不在 `sources.nature.discussion` / `longform` 里即为 news，无需配置）。
面板位置建议加进 `sources.order` 的 `geekpark` 之后。

### 2.2 Telegram 频道：7 → 18

13 个新频道**全部实测可访问**（`t.me/s/<channel>` 返回 200 且有帖子）。

去重结果：你给的新增列表里 **`tnews365` 与现有列表重复**，只保留一份。
因此净增 11 个频道：3 个新闻 + 6 个分享 + 2 个长文。

最终 18 个频道：

| # | 频道 | 显示名 | nature | 来源 |
|---|---|---|---|---|
| 1 | `MaiTungNews` | 買動新聞 | news | 原有 |
| 2 | `tnews365` | 竹新社 | news | 原有（你重复列出） |
| 3 | `zaihuapd` | 再花盘点 | news | 原有 |
| 4 | `xhqcankao` | 新华前瞻参考 | news | 原有 |
| 5 | `GodlyNews1` | — | news | 原有 |
| 6 | `NewlearnerChannel` | Newlearner の自留地 | news | 原有 |
| 7 | `DNSPODT` | — | news | 原有 |
| 8 | `douView` | douView × 银幕 | news | 新增 |
| 9 | `Vwyxrd` | 追尾游戏热点 | news | 新增 |
| 10 | `CNSmydream` | 来一点医学科学前沿 | news | 新增 |
| 11 | `reorx_share` | Reorx's Forge | discussion | 新增 |
| 12 | `notonlyshare` | 啰哩啰嗦分享频道 | discussion | 新增 |
| 13 | `bigwalnut` | 地心引力 | discussion | 新增 |
| 14 | `codedump_notes` | codedump 的电报频道 | discussion | 新增 |
| 15 | `hacker_news_zh` | Hacker News 摘要 | discussion | 新增 |
| 16 | `gledos_microblogging` | gledos 的微型博客 | discussion | 新增 |
| 17 | `wxbyg` | 微信搬运工 | **longform** | 新增 |
| 18 | `zhihu_bazaar` | 知乎大巴扎 | **longform** | 新增 |

关于 `hacker_news_zh`：它是 HN 故事的中文摘要搬运频道，与内置 `hackernews` 源
（直连 HN Firebase API）内容不重叠，两者都保留、各自在独立面板里。因此它们不构成
去重对象，也不需要任何合并逻辑。

全部 18 个频道的性质归属已确认，无待定项（决策记录见 §12）。

### 2.3 量级影响（推算，非实测）

| 项 | 现在 | 18 频道后（推算） |
|---|---|---|
| 抓取的帖子数 | 515（7 频道） | 约 1300 |
| HTTP 请求数 | 28 | **72** |
| 当天保留条目 | 84 | 约 216 |
| `telegram_input.json`（2a 输入） | 111 KiB | 约 390 KiB |
| `digest_input.json`（2b 输入） | 309 KiB | 约 500 KiB |
| `sources.limits.telegram` | 400 | **仍够用，但接近上限** |

四个直接后果：

1. **抓取耗时显著增加**（72 次请求，每次带延迟）。`telegram.pages` **保持 4 页**
   （已确认不降）：宁可贵一点，也不要漏掉当天的早期帖子。
2. **2b 输入涨到约 500 KiB**，模型上下文压力变大。若出现截断或质量下降，
   对策是给 2b 输入做条目上限（例如每个源最多 N 条）。
3. **Telegram 面板的频道筛选 rail 从 7 项变 18 项**。rail 本身已是可滚动容器
   （`scroller()`），不会破版，但需要人工看一眼实际观感。
4. **图片下载会触顶**。`collect.images.image_cap = 200`（`telegram.py:481` 按频道顺序
   `if total >= cap: break`）——当前 84 条对应 45 张，216 条约需 400+ 张，后段频道会
   没有图。图片也计入「零外部请求」验证与 HTML 体积。见 §14.2 #8。

---

## 3. nature：三分类

### 3.1 三个值

| 值 | 含义 | 参与分类 | 渲染 |
|---|---|---|---|
| `news` | 新闻（默认） | 2c | 独立 tab + 进分类面板的「分类」段 |
| `discussion` | 分享 / 讨论 / 用户分享 | 2d | 独立 tab + 进分类面板的「讨论」段 |
| `longform` | 不定期更新的长文 / 合集 | **不参与** | 只保留独立 tab |

`longform` 是本轮新增的第三类。它不需要任何渲染层改动：这类源本来就有独立 tab，
改造后只是不进分类面板。

### 3.2 源级分配

| nature | 源 |
|---|---|
| `news` | `sspai` `latepost` `geekpark` `weibo` `zhihu` `solidot` |
| `discussion` | `linuxdo`（除站外速览） `hackernews` |
| `longform` | `ruanyifeng` `anywayfm` |

内置默认写在 `tools/collectors/registry.py`，与 `SOURCE_LABELS` 并排：

```python
SOURCE_NATURE = {
    "linuxdo": "discussion",
    "hackernews": "discussion",
    "ruanyifeng": "longform",
    "anywayfm": "longform",
}
# 不在表里的源一律 news（sspai / latepost / geekpark / weibo / zhihu / 配置的 feed）
```

`config.defaults()` 从 `SOURCE_NATURE` 播种，保证内置默认与配置不漂移。

### 3.3 单元级覆盖

下级单元（Telegram 频道、linux.do 分节）可以覆盖源级判断：

| 单元 | 覆盖为 | 依据 |
|---|---|---|
| `telegram:reorx_share` 等 6 个频道 | `discussion` | §2.2 |
| `telegram:wxbyg`、`telegram:zhihu_bazaar` | `longform` | §2.2 |
| `linuxdo:站外速览` | `news` | 该节是外链新闻转述，其余 5 节是社区讨论 |

解析顺序：**源级默认 → 单元级覆盖**，一个纯函数：

```python
def nature_of(source, entry, cfg) -> str:
    unit = (entry.get("extra") or {}).get(registry.UNIT_FIELD.get(source, ""))
    return (cfg.unit_nature.get(f"{source}:{unit}")
            or cfg.source_nature.get(source)
            or "news")
```

`UNIT_FIELD = {"telegram": "channel", "linuxdo": "section_name"}` 留在 `registry.py`
（这是数据位置，不是用户偏好）。

---

## 4. 配置形态

### 4.1 新增配置

```toml
[sources.nature]
# 分享/讨论性质的源。不在 discussion / longform 里的源一律按新闻处理。
discussion = ["linuxdo", "hackernews"]
# 不定期更新的长文源：不参与 AI 分类，各自保留独立面板。
longform   = ["ruanyifeng", "anywayfm"]

# 下级单元的性质覆盖。键是「源名:单元名」，单元是 Telegram 频道名或
# linux.do 的晚报分节名。未列出的单元跟随其源。
[sources.units]
"telegram:reorx_share"        = "discussion"
"telegram:notonlyshare"       = "discussion"
"telegram:bigwalnut"          = "discussion"
"telegram:codedump_notes"     = "discussion"
"telegram:hacker_news_zh"     = "discussion"
"telegram:gledos_microblogging" = "discussion"
"telegram:wxbyg"              = "longform"
"telegram:zhihu_bazaar"       = "longform"
"linuxdo:站外速览"             = "news"

[telegram]
channels = ["MaiTungNews", "tnews365", "zaihuapd", "xhqcankao", "GodlyNews1",
            "NewlearnerChannel", "DNSPODT", "douView", "Vwyxrd", "CNSmydream",
            "reorx_share", "notonlyshare", "bigwalnut", "codedump_notes",
            "hacker_news_zh", "gledos_microblogging", "wxbyg", "zhihu_bazaar"]
```

### 4.2 为什么用「一张扁平表」而不是嵌套表

`tools/config.py` 的开放表校验（`OPEN_TABLES`）只检查一层类型，**不会递归**。
嵌套形态 `[sources.units.telegram]` 需要新写一个校验函数才能保持「拼错就报错」
的现有承诺。扁平表 `源名:单元名 = "性质"` 的值全是字符串，类型天然一致，
加进 `OPEN_TABLES` 即可。

唯一代价：键含冒号，`--show-config` 渲染时必须加引号。改动 2 行：

```python
def _toml_key(key: str) -> str:
    return key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else _toml_str(key)
```

`_flatten` / `_unflatten` 按 `.` 分割，键里的 `:` 不受影响，无需改动。

### 4.3 写错会怎样

| 错误 | 何时发现 | 处理 |
|---|---|---|
| `sources.nature.*` 里写了未知源名 | 配置校验期（合并后校验，白名单 = `registry.SOURCES` ∪ `[[feeds]]` 的 id） | 报错退出（码 2） |
| `sources.units` 的键格式不对（无冒号） | 配置校验期 | 报错退出 |
| `sources.units` 被写成嵌套表 `[sources.units.telegram]` | **当前不会报错**：开放表样本为空 → 整表不校验，且键名对不上 `nature_of` 的查找 | 见 §14.1 #1 |
| `sources.units` 里的源名未知 | 配置校验期 | 报错退出 |
| `sources.units` 里的频道名不在 `telegram.channels` | 配置校验期 | 报错退出 |
| `sources.units` 里的分节名拼错 | **配置期无法校验**（节名来自页面） | 盖章时若某源列出的单元名一个都没匹配上，打印警告 |
| 性质值不是三个合法值之一 | 配置校验期 | 报错退出 |

---

## 5. 流水线

```
1 采集
  18 个 Telegram 频道 + 11 个源（含新增 solidot）
  每条盖 id + nature（源级 → 单元级覆盖）
        │
        ▼  data/collected/*.json（原始，永不改写）
2a Telegram 去广告 / 去重 / 合并
        │
        ▼  processed items
        ├──────────────────┐
        ▼                  ▼
2c 新闻分类             2d 讨论分类        ← 并行，同一模板 / 同一校验 / 同一应用
   nature=news             nature=discussion
        └────────┬─────────┘
                 ▼  写回 item["category"]，产出 processed/categories.json
                 │
                 ▼  data/processed/*.json（含 id / nature / category）
2b 头条（输入带 category，themes 已删）
        │
        ▼  data/ai/digest_decisions.json
3 渲染
        ▼  site/digest.html
```

**顺序上的三条硬约束**

1. **2c/2d 必须在 2a 之后**：2a 会删广告、合并消息，在它之前分类会给页面上
   根本不存在的条目打类目。
2. **2b 必须在 2c/2d 之后**：2b 输入每条要带 `category`，模型才能判断
   「AI 类今天 18 条里 6 条讲同一件事」。
3. **`longform` 全程不参与 2c/2d/2b 的分类判断**，但它仍然出现在 2b 输入里
   （头条是全天视角，长文源也可能上头条）。

**并行实现**：`ai_model.generate` 是同步的（内部 `subprocess.run`），用
`ThreadPoolExecutor(max_workers=2)` 包两次调用即可，不新增抽象。
注意两点：`subprocess.run` 释放 GIL，线程是真并行，总耗时 ≈ `max(两者)`；
两次调用都传 `verbose=False`，否则 stderr 日志会交错。

---

## 6. 各阶段输入输出

### 6.1 现状（改造前）

| 阶段 | 输入 | 体积 | 输出 |
|---|---|---|---|
| 2a | `telegram_input.json` | 111 KiB / **84** 条 | `telegram_decisions.json` 13.7 KiB |
| 2b | `digest_input.json` | 309 KiB / 242 条 | `digest_decisions.json` 10.3 KiB |

84 条是 2a 的输入（去重前）；去重后 61 条，即 `data/processed/telegram.json`。
§2.3 的「当天保留条目 84」与这里一致（§14.3）。

### 6.2 改造后

| 阶段 | 输入 | 体积（推算） | 输出 |
|---|---|---|---|
| 2a | `telegram_input.json` | 约 390 KiB / 216 条 | 不变 |
| 2c | `classify_input.json` | 约 64 KiB / 约 220 条 | `classify_decisions.json` |
| 2d | `classify_forum_input.json` | 约 47 KiB / 约 150 条 | `classify_forum_decisions.json` |
| 2b | `digest_input.json` | 约 500 KiB / 约 390 条 | `digest_decisions.json`（无 themes） |

分类输入每条只给 `id, nature, source, source_name, title, summary(≤400), url,
channel?, section_name?` —— **不含 `body_text`**，这是它比 2b 便宜十倍的原因。

### 6.3 2c / 2d 输入（同构）

```json
{
  "schema": "newsletter.classify-input/1",
  "generated_at": "2026-10-14T01:30:00+08:00",
  "day": "2026-10-13",
  "nature": "news",
  "item_count": 220,
  "instructions": "<prompts/classify.md 的内容>",
  "response_schema": {
    "schema": "newsletter.classify-decisions/1",
    "day": "string",
    "model": "string",
    "nature": "news|discussion",
    "categories": [
      {"name": "string，中文，≤6 字", "summary": "string，一句话导语，≤60 字",
       "item_ids": ["string"]}
    ]
  },
  "items": [
    {"id": "sspai:1a2b3c4d5e6f7a8b", "nature": "news", "source": "sspai",
     "source_name": "少数派", "title": "…", "summary": "…", "url": "…"}
  ]
}
```

### 6.4 2c / 2d 输出（同构）

```json
{
  "schema": "newsletter.classify-decisions/1",
  "day": "2026-10-13",
  "model": "pi:deepseek-v4.1-flash",
  "nature": "news",
  "categories": [
    {"name": "AI", "summary": "模型能力与监管同日推进",
     "item_ids": ["sspai:1a2b…", "latepost:9f3c…"]},
    {"name": "硬件", "summary": "苹果与国产旗舰集中发布", "item_ids": ["…"]}
  ]
}
```

**没有 slug、没有 rank、没有 topic。** 类目锚点由脚本按条数降序编
`cat-1..N`，中文类目名只用于显示——这是「每天只有数量上的差别」成立的前提
（中文名转写成锚点会每天漂）。

### 6.5 2b 输出（删除 themes）

```json
{
  "schema": "newsletter.digest-decisions/1",
  "day": "2026-10-13",
  "model": "pi:deepseek-v4.1-flash",
  "headline": "一句话描述今天",
  "highlights": [
    {"item_ids": ["…"], "title": "…", "summary": "…",
     "topics": ["ai"], "importance": 92, "why": "…"}
  ],
  "stats_note": "一句话"
}
```

`themes` 字段**删除**，由 `categories` 取代。理由：`themes` 是「跨条目的模式」，
在自由命名的分类方案下与「某个类目有多少条」说的是同一件事。删掉之后三部分
各有唯一职责：

| 部分 | 覆盖范围 | 产出文字 | 回答的问题 |
|---|---|---|---|
| 今日头条 | 5–10 个事件 | 有（摘要 + why） | 今天什么最重要 |
| 分类（news） | 新闻侧全量 | 只有类目名 + 导语 | 全部新闻怎么归类 |
| 讨论（discussion） | 讨论侧全量 | 只有类目名 + 导语 | 社区在聊什么 |

### 6.6 校验层规则（`validate_classify_decisions`）

沿用现有「模型只给建议、脚本负责执行」的范式，任何非法项**拒绝并计数**，
不猜测执行：

| 情况 | 处理 |
|---|---|
| id 不存在于输入 | 拒绝该 id |
| 引用条目的 `nature` 与文件不符 | 拒绝（防止新闻条目出现在讨论侧） |
| 同一条被多个类目收下 | 只保留首次出现 |
| 类目名为空或超长 | 截断 / 跳过该条 |
| `summary` 超长 | 截断到 60 字 |
| 类目数 > 12 | 按条数保留前 12，其余合并进「其他」 |
| 类目条数 < 2 | 并入「其他」 |
| 未被任何类目收下的条目 | 脚本兜底进「其他」 |
| **覆盖率** | 打印 `classified 218/220`，并在页面上显示 |

day 闸门复用 `ai_apply.load_decisions`：决策文件的 `day` 必须等于本次运行的 day，
否则不应用。**这条必须有**——URL 类条目（HN、博客、feed）跨天 id 不变，
昨天的分类会被套到今天的数据上且零报错。

---

## 7. 标号（id）

### 7.1 只有 `id`，没有 `seq`

上一版计划里的 `seq`（人类可见序号）**取消**。原因：引用 chip 只显示源名
（见 §8.2），`seq` 的唯一用途随之消失。连带取消：渲染器 `display_no()` 函数、
「面板数字 == chip 数字」一致性测试、微博/知乎 rank 与 seq 的冲突处理。

### 7.2 id 的生成与落盘

| 项 | 说明 |
|---|---|
| 生成 | 复用现有 `ai_stage.item_id()`：Telegram 用 `telegram:<channel>:<message_id>`；其余用 `source:sha1(canonical_url)[:16]` |
| 落盘点 | `collect.py` 在 **`_postprocess()` 之后**、写 JSON 之前盖章 |
| 为什么在 `_postprocess` 之后 | 它内部跑 `base.dedupe()` 和 Telegram 图片下载；在之前盖章，被去重掉的重复项会白占一个号 |
| 单一实现 | `item_id()` 开头加 `if entry.get("id"): return entry["id"]`，保持一处推导逻辑，阶段 1 与阶段 2 不会漂移 |
| 撞号处理 | `dedupe()` 按 `dedupe_key`（title+summary）去重，而 id 按 canonical URL 生成，所以两条标题不同、URL 相同的条目会活下来并**同号**。盖章时同源撞号加 `-2` 后缀并打印警告 |

### 7.3 数据契约变更

`data/collected/*.json` 与 `data/processed/*.json` 的每条 item 新增：

| 字段 | 谁写 | 值 |
|---|---|---|
| `id` | `collect.py` | 稳定标号 |
| `nature` | `collect.py` | `news` / `discussion` / `longform` |
| `category` | `ai_apply.py`（2c/2d 之后） | 类目名；`longform` 条目为 `null` |

---

## 8. 引用跳转

### 8.1 锚点

6 个渲染函数各给每条加 `id="it-<id>"`：

| 函数 | 行号（当前） | 说明 |
|---|---|---|
| `article_rows` | `render_digest.py:898` | 少数派近期文章 / 晚点 / 极客公园 / 阮一峰 / Anyway |
| `_tg_card` | `render_digest.py:715` | Telegram 卡片 |
| `render_hn_board` | `render_digest.py:828` | Hacker News |
| `render_weibo_board` | `render_digest.py:854` | 微博热搜 |
| `render_zhihu_board` | `render_digest.py:874` | 知乎热榜 |
| `render_linuxdo_panel` | `render_digest.py:949` | linux.do 各节 |

### 8.2 chip 形态

```html
<a class="cite" href="#it-telegram:zaihuapd:44363"
   data-jump="telegram:zaihuapd:44363">@zaihuapd ↗</a>
```

- 显示文本：Telegram 显示 `@<channel>`，其余显示源名。**不带序号**（已定）。
- `href` 让无 JS 时也能用（浏览器会跳到锚点）。
- 代价：同一个源的多条引用看起来一样，识别靠点击后的高亮。已接受。

### 8.3 跳转时序（顺序不能反）

```js
e.preventDefault();                                  // 见 §14.1 #5：保留 href 就必须阻止默认跳转
var t = doc.getElementById('it-' + a.dataset.jump);  // 不能拼 CSS 选择器：id 含冒号
show(t.closest('.panel').id.slice(2));               // 同步：unhide → layoutMasonry
t.classList.add('no-rv');                            // 关掉 rv 的 translateY(6px)
spyLock = Date.now() + 900;                          // 否则 rail 被中途 section 抢走
t.scrollIntoView({ block:'center', behavior: reduce ? 'auto' : 'smooth' });
flashAfterScroll(t);                                 // scrollend 优先，定时器兜底
```

必须先 `show()`：面板是 `hidden` 的，隐藏元素没有几何信息，`scrollIntoView` 无效。
`href` 只是为了无 JS 时可用，点击路径必须 `preventDefault()`：`show()` 用
`replaceState` 独占 hash（`digest.js:105`），chip 再把 hash 改成 `#it-...` 会让
刷新掉到第一个 tab（`digest.js:467`）。见 §14.1 #5。

### 8.4 六个会干扰的既有机制

| # | 机制 | 后果 | 处理 |
|---|---|---|---|
| 1 | `.hd` 是 `position:sticky`，高度在运行期量进 `--head-h`（css:146） | 目标被顶到 header 底下 | 用 `block:'center'` 绕开；但比视口高的卡片仍会被顶到 header 下，需同时给 `it-` 目标加 `scroll-margin-top`（§14.3） |
| 2 | `@keyframes rv` 在 unhide 时重启动画，`animation-delay` 最多 348ms（css:242） | 目标此刻 `opacity:0`，跳过去看到空白 | 加 `.no-rv`；**必须写 `.panel.on .rv.no-rv{animation:none}`**（`.no-rv` 单类打不过 `.panel.on .rv`） |
| 3 | `show()` 内 `win.scrollTo(0, scrollMemo[id])`（js:104） | 先滚到记忆位置 | 我们的滚动在 `show()` 之后调用，后者赢 |
| 4 | scroll-spy 的 `spyLock`（js:186） | rail 被中途 section 抢走 | 跳转时也设 `spyLock` |
| 5 | `layoutMasonry` 把 `.tg` 搬进 `.mas-col`（js:228） | DOM 顺序 ≠ 视觉顺序 | 先 `show()`（内部已调 `layoutMasonry`）再测量 |
| 6 | 全站 `reduce` 标志（js:12） | 新动画不跟随会破坏无障碍约定 | 跳转与高亮都跟随 `reduce` |

### 8.5 Telegram 的跳转

统一滚动 + 高亮（已定），不弹浮层。注意瀑布流没有线性顺序、卡片被 `.clamp` 截断
（css:526），所以跳到的是被截断的卡片，看全文要再点一次。

### 8.6 深链

现状只支持到 tab（`location.hash` 当 tab key）。要支持 `#p-sspai/it-xxx`
需在 boot 里加两段解析（约 5 行），且**必须在 `show()` 之后解析第二段**——
`show()` 会用 `replaceState` 覆写 hash。

---

## 9. 渲染

### 9.1 「今日汇总」tab 的三块

```
今日汇总 tab
├ rail：今日头条 / 分类 / 讨论
│   └ 分类展开：AI(18) 硬件(12) 国际政治(9) …
├ 今日头条   01 · 头条 + 要点（含跨源 refs）
├ 分类       AI 18 → 每条：标题外链 + 引用 chip + 类目导语
└ 讨论       AI 7 → 同上
```

`longform` 条目（阮一峰、Anyway，以及 Telegram 面板里的 wxbyg/zhihu_bazaar）
**不出现在这三块里**，仍按原样保留在各自的渲染位置：阮一峰/Anyway 在自己的独立 tab，
wxbyg/zhihu_bazaar 在 Telegram 瀑布流内（可用频道筛选 rail 单独查看）。

### 9.2 分类面板结构

每个类目一个 `<section id="cat-N">`，左侧 rail + scroll-spy
（复用 `rail(links, "spy", "类目")`，linux.do 面板就是这么做的）。
类目内不复制正文，只放「标题外链 + 引用 chip」，所以 HTML 体积几乎不变
（现有 3.4 MB 主要是 base64 图片）。

### 9.3 落盘

分类结果写独立文件 `data/processed/categories.json`，不塞进 `digest.json`。
这样分类失败不连带丢掉头条。代价：`ai_apply.remove_leftovers()`
（`ai_apply.py:169`）要多认一种面板文件。

---

## 10. 改动清单

| # | 文件 | 改动 | 估量 |
|---|---|---|---|
| 1 | `tools/ai_stage.py:131` | `item_id` 优先取 `entry["id"]` | 2 行 |
| 2 | `tools/collect.py:224` | `_postprocess` 后盖章 `id`（含撞号检测）+ `nature` | ~20 行 |
| 3 | `tools/collectors/registry.py:33` | `SOURCE_NATURE` / `UNIT_FIELD` | 8 行 |
| 4 | `tools/config.py` | `defaults()` 加 `sources.nature`、`sources.units`；`OPEN_TABLES` 加 `sources.units`；`_toml_key()`；单元名校验 | ~30 行 |
| 5 | `config/config.toml` | `[[feeds]]` 加 solidot；`[telegram]` channels 扩到 18；新增 `[sources.nature]` / `[sources.units]`；`sources.order` 加 solidot | ~25 行 |
| 6 | `tools/ai_stage.py` | 新增 `build_classify_input` / `validate_classify_decisions` / `apply_classify_decisions`；删 themes 相关 7 处 | ~120 行 |
| 7 | `tools/ai_apply.py` | 2c/2d 线程并行接线；写 `categories.json`；`remove_leftovers` 加识别；删 themes 统计 2 处 | ~50 行 |
| 8 | `prompts/classify.md`（新）、`prompts/digest_highlights.md` | 新提示词；后者删 themes 段 | — |
| 9 | `tools/render_digest.py` | 三块面板（头条/分类/讨论）；6 个函数加锚点；删 themes 5 处 | ~100 行 |
| 10 | `tools/digest_assets/digest.js` / `.css` | `data-jump` handler（含 `no-rv`、`spyLock`、`reduce`）+ `.flash` + 深链解析 | ~40 行 |
| 11 | `tools/demo_decisions.py` | 加假分类决策；删 themes（L142-159、L183、L200） | ~40 行 |
| 12 | `tests/`、`README.md` | 锚点一致性 / 覆盖率 / 性质不串组 / 单元覆盖 测试；README 加阶段 2c 与新增源 | ~100 行 |
| 13 | `config/config.toml` `[sources.labels]` | 加 `solidot = "奇客 Solidot"`，否则 tab 标题回落成裸 id（§14.2 #7） | 1 行 |
| 14 | `tools/render_digest.py:1132` | 今日汇总面板的生成条件改为「`cards` 或 categories 任一存在」，并让 `render_digest_panel` 容忍空 digest（§14.1 #3） | ~10 行 |
| 15 | `tools/ai_apply.py:294` | 逐源 processed 文件与 `build_digest_input` 的写入挪到 2c/2d 之后（§14.2 #9） | ~15 行 |
| 16 | `tools/ai_stage.py` | `apply_classify_decisions` 输出**已解析的 refs**，并定稿 `categories.json` schema（§14.1 #4，含在 #6 内） | — |
| 17 | `tools/run_ab.py:86` | `ai_apply` 调用加 `--ai-backend none`，避免模型调用由 2 次变 6 次（§14.2 #11） | 1 行 |
| 18 | `README.md`、`prompts/digest_highlights.md`、`tools/digest_assets/digest.css` | README:317/449/512/675 改为 11 源 + 2c/2d；删提示词的 themes 段；删 `.dg-th/.th*` 死 CSS（§14.2 #14） | ~40 行 |
| 19 | `config/config.toml`、`tools/collectors/telegram.py:33`、`tests/test_config.py:41` | 二选一：把 18 频道与单元覆盖搬进 `defaults()` 并修好恒真的守卫测试，或改掉「config.toml 是 no-op」的注释与测试（§14.1 #6） | 决策项 |

---

## 11. 实施顺序

| 批 | 内容 | 验收 |
|---|---|---|
| 1 | 清单 #1–#5：`id` 落盘、`nature` 盖章、配置项、新增源与频道 | `collected/*.json` 每条有 `id`/`nature`；同源无撞号；18 频道全部抓到；`make config` 可见；现有 63 项测试不回归 |
| 2 | 清单 #6–#8：2c/2d 纯函数 + 并行接线 + 提示词 | `make demo` 产出 `categories.json`；覆盖率报告正确；单侧失败不影响另一侧 |
| 3 | 清单 #9–#10：三块面板 + 锚点 + 跳转 JS | 分类 chip 能跨 tab 跳转并高亮；产物仍零外部请求（CDP 验网络请求数 = 0） |
| 4 | 清单 #11–#12：文档 + 测试 | README 加阶段 2c；锚点一致性测试通过 |

批次 1 开工前先落定 §14.1 的 6 项，并把 §14.2 的后果写进对应批次的验收。
另：`id` / `nature` 由阶段 1 盖章，所以批次 1 之后每次验证都必须先跑 `make collect`——
只跑 `make ai` 时所有源会静默回落成 `news`（§14.2 #10）。

§10 新增行的归属：

| 行 | 批 |
|---|---|
| #13（solidot 标签） | 1 |
| #15（processed 写入时机）、#16（categories refs） | 2 |
| #14（汇总面板生成条件） | 3 |
| #17（run_ab 不调模型）、#18（README / 提示词 / 死 CSS） | 4 |
| #19（config.toml no-op 二选一） | 开工前决策 |

---

## 12. 决策记录

上一版的 5 个待确认项已全部定案：

| # | 事项 | 决定 | 影响 |
|---|---|---|---|
| 1 | `NewlearnerChannel` 归哪一类 | **news**（按默认，不例外） | 无代码影响；7 个原有频道全部保持 news |
| 2 | `hacker_news_zh` 与内置 `hackernews` 是否重叠 | **不重叠，两个都留** | 两个源各自独立面板；无去重逻辑；`hacker_news_zh` 走 `[sources.units]` 归 discussion |
| 3 | `wxbyg` / `zhihu_bazaar` 是否从 Telegram 面板拆出 | **不拆** | 它们是 Telegram 频道，条目仍在瀑布流里与其余 16 个频道混排；`longform` 标记只影响「是否参与 AI 分类」，不影响面板归属 |
| 4 | `douView` / `Vwyxrd` / `CNSmydream` 归 news 是否准确 | **归 news，正确** | 无代码影响 |
| 5 | `telegram.pages` 是否从 4 降到 3 | **保持 4** | 约 72 次请求；接受抓取耗时，换取不漏当天的早期帖子 |

### 关于第 3 项的一个说明

`longform` 与面板归属是两件正交的事：

| | 是否参与 AI 分类 | 渲染位置 |
|---|---|---|
| `ruanyifeng` / `anywayfm` | 否 | 各自的独立 tab |
| `wxbyg` / `zhihu_bazaar` | 否 | **Telegram 瀑布流内**（与其余 16 个频道混排） |

所以「不参与分类」这个目标对这两类源都已达成，无需新增渲染分支。Telegram 面板里
可以用既有的频道筛选 rail 单独看 `wxbyg` 或 `zhihu_bazaar`。

---

## 13. 未验证与取舍

### 13.1 未验证（需要真实运行才能确认）

- 模型实际输出的类目数与「上限 12 / 最少 2 条」的冲突频率。
- 2b 输入涨到约 500 KiB 后模型是否还能稳定输出（可能截断或质量下降）。
- `scrollend` 事件在目标浏览器上的支持（`flash` 时机需要它）。
- `NewlearnerChannel` 这类标题就是标签行（`#News #APPLE`）的条目，模型只能靠
  summary 判断类目，实际质量需观察。
- 18 频道抓取的实际耗时与失败率。
- 奇客 RSS 的 `summary` 是否每条都有（实测 20 条都有，但需观察）。
- 两个 `pi` 进程并发（`-p --no-session`）是否在 auth/cache 上有共享状态。静态不可判定，
  建议第一次并行时把两侧 stderr 分别落盘。
- `image_cap = 200` 触顶后图片在频道间的分布变化（见 §14.2 #8）。

### 13.2 取舍

| 取舍 | 原因 |
|---|---|
| 类目跨天不可比 | 模型自由命名，不建词表。已确认只要数量差别。 |
| chip 不带序号 | 已定。代价是同源多条引用看起来一样。 |
| `topics`（8 个固定值）与 `category` 并存 | `topics` 仍用于 2a merge 和头条标注，动它会牵连 2a。两套标签的语义需在 README 写清。 |
| 节级覆盖放在 `registry.py` 而非配置 | `UNIT_FIELD` 是数据位置（不是偏好）；但 `sources.units` 表让分节性质也可配置，所以实际例外也在配置里。 |
| `longform` 不做任何渲染层改动 | 这个标记只决定「是否进 AI 分类」，不决定面板归属：阮一峰/Anyway 本就有独立 tab，`wxbyg`/`zhihu_bazaar` 仍在 Telegram 瀑布流内，两者都已达成目标。 |
| `telegram.pages` 保持 4 | 18 频道 × 4 页 ≈ 72 次请求。接受抓取耗时，换取不漏当天的早期帖子。 |
| 奇客 `fulltext = "none"` | RSS 摘要足够；正文页带噪声（「新版网站常见问题」「本文已被查看 N 次」）。 |

---

## 14. 复审补充（2026-10-14）

对基线代码做了一轮只读复审。§1–§13 的技术引用（§8.1 的 6 个函数行号、digest.js /
digest.css 的机制与行号、63 项测试、§2.3 与 §6.1 的基线数字）已逐条核对，基本准确
（订正见 §14.3，核对结论见 §14.4）。下面是原计划遗漏的问题。

### 14.1 必须先解决（6 项）

#### 1 `sources.units` 的空默认值让「拼错就报错」失效

`tools/config.py:217-221` 的开放表校验用表里的第一个值当类型样本：

```python
if where in OPEN_TABLES:
    sample = next(iter(schema.values()), None)
    if sample is not None and not _type_ok(value, sample):
```

`defaults()` 里 `sources.units = {}` 时样本为 `None`，`_type_ok` 直接返回 `True`，
**整张表完全不校验**。用户写成嵌套表 `[sources.units.telegram]` 更糟：`_flatten`
得到 `sources.units.telegram.reorx_share`，而 `nature_of` 查的是
`telegram:reorx_share` → 查不到 → 静默回落到源级默认。这正是 `config.py` 开头承诺
要避免的「静默 no-op」。

处理：`defaults()` 至少种一条；或对空表显式保留字符串类型检查。另加一条
「`sources.units` 只允许一层、键必须含冒号」的检查。

#### 2 §4.3 承诺的 4 类配置期报错在 `_validate` 里做不到

`config.py:200` 的 `_validate(raw, schema)` 只拿文件与 schema，没有跨表上下文；
`[[feeds]]` 的 id 要到 `collect.py:158` 的 `registry.configure_feeds()` 才注册。
所以「`sources.nature.*` 未知源名」「`sources.units` 的源名 / 频道名」必须在**合并后**
新增一趟校验。白名单也不是 §4.3 原先写的「`sources.limits` 的键」——`sources.limits`
本身是开放表，用户可以不写——应为 `registry.SOURCES` ∪ feed ids。

#### 3 今日汇总面板只在 `digest.cards` 非空时存在，分类没有落脚点

`render_digest.py:1132`（另 `:1263`）：

```python
if digest and digest.get("cards"):
    add("digest", "今日汇总", ...)
```

§9.3 的收益（「分类失败不连带丢掉头条」）只覆盖一个方向；反过来 **2b 失败时
`categories.json` 有内容却无处渲染**。并且 `AI=none` 会让分类 / 讨论彻底不可见，
与 README:317（「`AI=none` 时产出仍然完整」）和 README:512 的现有表述冲突。

处理：`cards` 或 categories 任一存在即生成面板，`render_digest_panel` 容忍空 digest。

#### 4 `categories.json` 的 schema 没有定义

§6.4 给的是**模型决策**格式（只有 `item_ids`），§9.2 却要求渲染「标题外链 + 引用
chip」，这需要 id → (source, source_name, channel, title, url) 的解析。
`ai_stage.py:585` 起的 `apply_digest_decisions` 已为 highlights 做了这件事，
`apply_classify_decisions` 必须照做并把**已解析的 refs** 写进 `categories.json`，
否则渲染器要自己回读全部 `data/processed/*.json`。先定下来再写渲染。

#### 5 chip 用 `<a href="#it-...">` 必须 `preventDefault()`

§8.3 的时序片段没有阻止默认锚点跳转。未阻止时点击会把 `#it-...` 写进
`location.hash`，而 `digest.js:467` 的 boot 把 hash 当 tab key：

```js
var initial = panelOf(first) ? first : (tabs[0] && tabs[0].dataset.tab);
```

点过 chip 后刷新会掉到第一个 tab；原生瞬时跳转还会与 `scrollIntoView` 打架。
`show()` 已经用 `replaceState` 独占 hash（`digest.js:105`），chip 也必须有
`preventDefault()`（或像现有 rail 那样干脆不用 `<a href>`）。

#### 6 「config.toml 是 no-op」这个不变量会被打破，而它的守卫测试是空的

`config/config.toml` 开头写着「Every value here is also a built-in default, so
deleting any line changes nothing」，`tests/test_config.py:41` 就是为守住它写的。
但 `config.py:327-330` 把 `path=None` 解析成 `DEFAULT_PATH` 并**真的加载**：

```python
file_path = Path(path) if path is not None else DEFAULT_PATH
if file_path.exists():
```

所以 `builtin` 恒等于 `shipped`，断言永远成立（恒真）。静态可证：`defaults()` 里
唯一不在文件里的键是 `feeds`，而它被显式 skip。

后果不是测试失败（所以「63 项测试不回归」可以达成），而是：计划把 18 频道、solidot、
`sources.units` 只写进 `config.toml` 后，删掉该文件就静默退回 7 频道 + 无 solidot +
无单元覆盖，且没有任何东西会报警。

处理（二选一，需定案）：

- (a) 把 18 频道搬进 `telegram.DEFAULT_CHANNELS`、单元覆盖搬进 `registry` 常量，
  并把守卫测试改成真的比较 `defaults()`；
- (b) 承认 config.toml 不再是 no-op，同时改注释和测试。

### 14.2 会改变行为、原计划未写明（8 项）

| # | 问题 | 证据 | 处理 |
|---|---|---|---|
| 7 | solidot 的 tab 标题会是裸 id `solidot` | `render_digest.py:74` `labels.get(name) or builtin_label or name`；solidot 不在 `SOURCE_META` 里 | 清单 #13 补 `[sources.labels] solidot`；否则 tab 标题（`solidot`）与条目上的 `source_name`（`奇客 Solidot`）不一致 |
| 8 | `image_cap = 200` 在 18 频道下会真的触顶 | `telegram.py:481` `if total >= cap: break`，按频道顺序取图；当前 84 条 → 45 张，216 条约需 400+ 张 | 上调 cap，或接受后段频道在瀑布流里无图；图片也计入「零外部请求」验证与 HTML 体积 |
| 9 | 逐源 processed 文件的写入时机必须后移 | `ai_apply.py:294-317` 现在在 2a 之后立刻写 `<source>.json`，然后才 build digest input | 条目要带 `category`，源文件与 `build_digest_input` 都要挪到 2c/2d 之后；`written` 集合与 `remove_leftovers` 的记账同样依赖这个顺序 |
| 10 | `nature` 依赖「先 collect」 | 现有 `data/collected/` 里没有 `id` / `nature` | 只跑 `make ai` 会让所有源回落成 `news`，linux.do / HN 静默进新闻侧；批次 1 验收写明「必须先 `make collect`」 |
| 11 | A/B 路径的模型调用次数翻倍 | `run_ab.py:86` 调 `ai_apply` 时没传 `--ai-backend none`，默认 `ai.backend = "pi"` | 每引擎由 2 次（2a/2b）变 4 次（2a/2c/2d/2b），一次 A/B 由 4 次变 8 次 |
| 12 | 并行调用的失败会静默 | §5 要求两侧都传 `verbose=False` 以免 stderr 交错 | join 之后必须补一行汇总（`classified 0/220: <error>`），并写进 `report` 与 `processed/index.json` 的 `stage2`（现在只有 telegram / digest） |
| 13 | 同一面板里出现两种链接语义 | `render_digest.py:537` 的 `refs()` 用外链 `ext(url, ...)`；新的分类 / 讨论行用内部 `class="cite"` chip | 要么把头条 refs 也改成 chip，要么明确写「头条保持外链」 |
| 14 | README / 提示词 / 死 CSS 的连带更新没进清单 | README:317、449、512、675；`prompts/digest_highlights.md:80,98`；删 themes 后 `.dg-th/.th/.th-n/.th-s/.th-r` 成为死 CSS | 见清单 #18 |

### 14.3 事实订正

| 位置 | 原文 | 实际 |
|---|---|---|
| §6.1 2a 行 | `telegram_input.json` 111 KiB / 61 条 | 该文件是 **84** 条（111 KiB 对得上）；61 是 2a **之后** `data/processed/telegram.json` 的条数，§2.3 的 84 才对 |
| §6.1 输出列 | `telegram_decisions.json` 16 KiB、`digest_decisions.json` 12 KiB | 13.7 KiB、10.3 KiB |
| §4.3 白名单 | 「`sources.limits` 的键 + `[[feeds]]` 的 id」 | 应为 `registry.SOURCES` ∪ feed ids（见 §14.1 #2） |
| §8.4 #1 | 「用 `block:'center'` 绕开」 | 对普通高度行成立；比视口高的卡片仍会被顶到 header 下。`digest.css:303` 已有 `.sec{scroll-margin-top:...}`，但只在 `start` / `nearest` 生效 → 建议 `block:'nearest'` + 给 `it-` 目标加 `scroll-margin-top` |

另：根目录文件是 `PLAN.md`（大写），不是 `plan.md`。

### 14.4 已核对为准确的部分

- §8.1 六个函数行号全对：`_tg_card` 715、`render_hn_board` 828、`render_weibo_board` 854、
  `render_zhihu_board` 874、`article_rows` 898、`render_linuxdo_panel` 949。
- digest.js：`reduce` 12、`scrollMemo` / `show()` 82、`win.scrollTo` 104、`replaceState` 105、
  `spyLock` 162/187、`layoutMasonry` 213/228、boot hash 467。digest.css：`--head-h` 42、
  `.hd{sticky}` 146、`.panel.on .rv` 242、`@keyframes rv` 244、`.clamp` 526。
- §8.4 #2 的优先级判断正确：`.panel.on .rv` 是 (0,3,0)，`.no-rv` 单类打不过，必须写
  `.panel.on .rv.no-rv{animation:none}`（(0,4,0)）。348ms 上限也对（`min(--i,16)*18ms+60ms`）。
- 63 项测试 = 29 + 34；`make test` = `unittest discover -s tests -v`。
- §4.2 全对：`_flatten` / `_unflatten` 按 `.` 分割，`telegram:reorx_share` 能正确穿过
  `Config.get("sources.units")` 与 `as_dict()`；`_toml_key` 确实必须加——`config.py:174`
  现在直接 `f"{key} = ..."`，冒号键会产出非法 TOML，而 `tests/test_config.py:124`
  会抓到这个。
- §5 的三条顺序硬约束正确；`ai_model.generate` 确实是同步 `subprocess.run`，有 `verbose`
  参数，并且会把 `day` 盖进决策文件，所以 `load_decisions` 的 day 闸门对 classify 同样有效。
- Telegram rail 确实是 `scroller()`（`render_digest.py:492` 的 `rail()` 内部包了 scroller），
  18 项不会破版。
- 基线数字：515 = 77+75+75+80+59+69+80；84 = 85−1 去重；28 = 7×4；digest.html 3.4 MB。
- 信源实测（2026-10-14）：solidot RSS 用浏览器 UA 返回 200 / `application/xml` / 23.1 KB /
  20 条，`summary` 384 字（`feedparser` 解析正常）；11 个净增频道 `t.me/s/<channel>`
  全部 200 且有帖子。

---

## 15. 相关文件

```
PLAN.md                          本文档
config/config.toml               全部配置（新增 nature / units / solidot feed）
prompts/classify.md              2c/2d 提示词（新增）
prompts/digest_highlights.md     2b 提示词（删 themes 段）
tools/collect.py                 阶段 1 编排器（盖章 id / nature）
tools/collectors/registry.py     源注册表（新增 SOURCE_NATURE / UNIT_FIELD）
tools/ai_stage.py                阶段 2 纯函数（新增分类三函数，删 themes）
tools/ai_apply.py                阶段 2 CLI（并行接线 2c/2d）
tools/render_digest.py           阶段 3 渲染器（三块面板 + 锚点）
tools/digest_assets/digest.js    跳转与高亮
tools/demo_decisions.py          无模型演示（新增假分类决策）
tools/digest_assets/digest.css   跳转高亮 + 死 CSS 清理（清单 #18）
tools/run_ab.py                  A/B 驱动（清单 #17）
tests/test_config.py             配置层测试（守卫测试需按 §14.1 #6 修正）
tests/test_pipeline_fixes.py     流水线测试
README.md                        阶段 2c / 信源数量更新（清单 #18）
```

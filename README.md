# NewsYou

个人 Newsletter 聚合系统：把中英文多信源的一天内容抓下来，可选地交给模型去重、筛选，最后渲染成**一个自包含的 HTML 文件**。

- **10 个信源**：少数派（派早报逐条拆开）、Telegram（18 个频道）、linux.do、晚点、极客公园、奇客 Solidot、阮一峰、Anyway.Now、Hacker News、知乎热榜

> 微博热搜默认关闭（`sources.disabled = ["weibo"]`）：榜单噪声大、内容很少留到第二天。**采集器与榜单渲染器都保留**，删掉配置里那个名字即可恢复。
- **单文件产出**：Telegram 图片内联 base64，短讯可离线阅读；展开长文时正文配图按原 URL 加载

### 图片策略

| 位置 | 处理 | 原因 |
|---|---|---|
| Telegram 卡片图 | 下载后内联 base64 | 数量可控（`images_per_post` / `image_cap`），且是消息主体 |
| 长文正文配图 | **保留远程 URL** | 实测每篇 6–11 张、平均 248 KiB，全部内联约 +65 MB |

所以「零外部请求」现在只对短讯成立：**离线或把文件发给别人时，展开长文才会缺图**。正文图片经白名单净化，只允许 `http(s)` 源（`data:` 与相对路径会被丢弃）。
- **模型可选**：默认调用，`AI=none` 完全不调；模型只输出「决策」，脚本校验后应用
- **一切可配置**：`config/config.toml`，删掉整个文件也能跑

---

# 第一部分 · 怎么用

## 1. 安装

```bash
git clone <repo> && cd NewsYou
make setup          # 建 .venv 并装依赖（唯一需要网络的步骤）
make check          # 验证依赖与两个引擎
```

依赖装在项目内的 `.venv/`，**不动系统 Python**。要求 Python **3.11+**（配置层用标准库 `tomllib`）。

## 2. 跑一次

日常用这个：

```bash
make daily          # 抓取 → AI → 渲染 → 归档快照
```

约 2–4 分钟（Telegram 图片下载和模型调用占大头）。产出 `site/digest.html`（最新一期日报）和 `archive/<day>/`（当天快照）。

只跑不归档：

```bash
make all            # 抓取 → AI → 渲染
```

不想调模型（AI 面板消失，其余照常）：

```bash
make all AI=none
```

## 3. 命令一览

| 命令 | 作用 |
|---|---|
| `make setup` | 建 `.venv` 并安装 `requirements.txt` |
| `make check` | 验证依赖与两个引擎是否可用 |
| `make test` | 跑离线回归测试（不联网） |
| `make config` | 打印**生效配置**，并标注每个值来自哪一层 |
| `make collect` | 只抓取 → `data/collected/` |
| `make ai` | 只做 AI 四个子阶段 → `data/processed/` |
| `make render` | 只渲染 → `site/digest.html` |
| `make all` | collect + ai + render |
| `make daily` | **日常用这个**：all + 归档当天快照 |
| `make demo` | 用启发式假决策跑通全流程，**不调模型** |
| `make archive` | 归档当前产物（不重跑流水线） |
| `make ab` | 两个引擎各跑一遍并出对比报告 |
| `make clean` | 清掉生成的数据（保留 `site/digest.html` 与 `archive/`） |

### 常用覆盖参数

Makefile 变量只在**显式设置时**才传给脚本，否则由 `config.toml` 决定：

```bash
make all AI=none                       # 不调模型
make all AI_MODEL=deepseek-flash       # 换模型
make all FULLTEXT=all                  # 本次抓全部全文
make all CONFIG=config/other.toml      # 换配置文件
make collect                           # 也可直接调脚本，见下
```

### 直接调脚本

```bash
./.venv/bin/python tools/collect.py --all
./.venv/bin/python tools/collect.py sspai --limit 5
./.venv/bin/python tools/collect.py --list-sources
./.venv/bin/python tools/collect.py --show-config
./.venv/bin/python tools/ai_apply.py --report
./.venv/bin/python tools/render_digest.py
./.venv/bin/python tools/archive.py --day 2026-10-13
```

## 4. 输出在哪

```
site/digest.html          最新一期日报（单文件，可直接分享）
data/collected/*.json     原始抓取结果（永不改写）
data/ai/*_input.json      发给模型的输入
data/ai/*_decisions.json  模型返回的决策
data/processed/*.json     应用决策后的数据
archive/<day>/            当天完整快照，含 digest.html 的副本（gitignore）
```

`data/`、`archive/`、`reports/`、`.venv/` 都在 `.gitignore` 里；只有 `site/digest.html` 进版本控制。

---

## 5. 配置项详解

全部配置在 **`config/config.toml`**。优先级：

```
命令行参数  >  NEWSLETTER_* 环境变量  >  config.toml  >  代码内置默认值
```

**每一行都有内置默认值**，所以删掉任何一行（甚至整个文件）行为都不变。反过来，**写错会报错**——未知键、类型错误都会让程序以退出码 2 停下，而不是静默忽略。

随时查看生效值：

```bash
make config
```

输出是合法 TOML，每行末尾标注来源（`# from file` / `# from env` / `# from cli` / `# from default`）。

### `[ai]` — 模型调用

| 键 | 默认 | 含义 |
|---|---|---|
| `backend` | `"pi"` | 用哪个 CLI。`pi` \| `codex` \| `none`。**`none` 是唯一关闭 AI 的方式** |
| `model` | `"deepseek-v4.1-flash"` | 传给后端的模型名。留空字符串则让后端自选 |
| `timeout` | `900` | 单次调用超时（秒） |
| `reuse` | `false` | 是否复用已有的 `*_decisions.json`。**默认 `false`：每次跑都会调模型**。设为 `true` 可省一次调用，但仅限同一天重跑——决策文件只对生成它的那一天有效，跨天的文件会被自动重新生成（见下） |
| `args` | `[]` | 追加到命令行的额外参数（逃生舱），排在提示词之前 |

关于 `backend` 与 `model` 两个字段的边界：它们能**标识**要跑什么，但**命令长什么样是每个后端自己的知识**。pi 和 codex 在可执行文件、子命令、隔离参数上全都不同：

```
pi     pi -p --no-session --no-tools --no-context-files --model <m> "<prompt>"
codex  codex exec --skip-git-repo-check --sandbox read-only --model <m> "<prompt>"
```

所以每个后端在 `tools/ai_model.py` 里有一条声明式 profile（可执行文件 + 基础参数 + 模型参数）。**加后端 = 加一条记录**。`codex` 已作为示例存在，但标记为实验性：二进制不存在时会拒绝运行并告警。

### `[collect]` — 抓取

| 键 | 默认 | 含义 |
|---|---|---|
| `engine` | `"modern"` | 抽取引擎。`modern`（trafilatura + feedparser，保留链接和小节标题）\| `legacy`（纯标准库正则） |
| `fulltext` | `"today"` | 全文策略，见下 |
| `images` | `true` | 是否下载 Telegram 图片（会以 base64 内联进 HTML） |
| `images_per_post` | `2` | 每条 Telegram 消息最多存几张图 |
| `image_cap` | `200` | 单次运行的图片总数上限 |
| `limit` | `0` | 全局条数上限。**`0` 表示不覆盖**，用 `sources.limits` 里的每源设置 |
| `workers` | `4` | 并发抓取数。一次运行几乎全在等别人的服务器，线程能显著缩短耗时（实测 206s → 62s）；上限是礼貌而非机器。`1` 恢复严格串行 |

**`fulltext` 的取值**（决定「要不要抓正文」）：

| 值 | 含义 |
|---|---|
| `today` | 只抓**当天发布**的条目的正文（默认，最省流量） |
| `latest` | 抓最近 N 条，不管日期 |
| `all` | 全部抓正文 |
| `none` | 从不抓正文，只留标题+链接 |
| `<N>` | 最近 N 条 |

### `[telegram]` — 频道

| 键 | 默认 | 含义 |
|---|---|---|
| `channels` | 18 个频道 | 要跟的公开频道。**加频道 = 加一个字符串** |
| — | — | 面板与引用 chip 显示频道的中文名（取自 t.me 页面，零额外请求）；取不到时回退 `@频道id` |
| `pages` | `4` | 每个频道翻几页（每页约 20 条） |
| `day` | `"auto"` | 取哪天的消息。`auto` \| `today` \| `yesterday` \| `YYYY-MM-DD` |

> `auto` 会选**所有频道中最新有动态的那一天**，避免各频道对「今天」的理解不一致。
> 注意 `today` 在凌晨会返回 0 条，日常用 `auto` 更安全。

### `[hackernews]`

| 键 | 默认 | 含义 |
|---|---|---|
| `feed` | `"topstories"` | `topstories` \| `beststories` \| `newstories` \| `askstories` \| `showstories` |

### `[sources]` — 信源集合

| 键 | 默认 | 含义 |
|---|---|---|
| `order` | 11 个源 | **面板顺序**。纯粹是排序：没列到的源仍会运行，只是排在后面 |
| `enabled` | `[]` | **白名单**。非空时只抓这些源。空 = 全部 |
| `disabled` | `["weibo"]` | **按名关闭**。与 `enabled` 分开，关一个源不必列出其余全部 |
| `[sources.limits]` | 各源不同 | 每个源抓多少条 |
| `[sources.labels]` | 各源中文名 | 面板显示名 |
| `[sources.nature]` | 见下 | 哪些源是讨论/长文。**没列到的一律是新闻**；列表是**替换**语义 |
| `[sources.units]` | 见下 | 单个频道或分节的性质覆盖，键为 `源:单元` |

> **关源**用 `enabled`（从 `order` 里删掉**不会**关掉它，只会改变排序）；**改名**用 `labels`。

`[sources.nature]` 决定一个源属于哪一类，也就决定它进哪一半流程：

```toml
[sources.nature]
discussion = ["linuxdo", "hackernews"]      # 分享/讨论 → 2d
longform   = ["sspai", "latepost", "geekpark", "ruanyifeng", "anywayfm"]
```

⚠️ 每个列表**替换**内置默认而不是追加，所以列出一个源就意味着列出全部；漏掉一个源会让它静默回到新闻侧。

| 值 | 含义 | 参与分类 | 页面位置 |
|---|---|---|---|
| `news` | 新闻（默认） | 2c | 自己的面板 + 汇总页的「分类」段 |
| `discussion` | 分享 / 讨论 | 2d | 自己的面板 + 汇总页的「讨论」段 |
| `longform` | 不定期更新的长文 | **不参与** | 只保留自己的面板 |

`[sources.units]` 处理一个源内部不一致的情况（Telegram 的 18 个频道分属三种性质，linux.do 的「站外速览」是转载新闻）：

```toml
[sources.units]
"sspai:派早报"          = "news"        # 少数派是长文源，只有派早报进分类
"telegram:reorx_share" = "discussion"
"telegram:wxbyg"       = "longform"
"linuxdo:站外速览"      = "news"
```

少数派的 `派早报` 不是它自己的字段，而是采集器拆块时写上的标记。

没列到的单元跟随其源，所以只需要写例外。配置写错会在启动时报错退出，不会静默回落。

### `[output]` / `[archive]` / `[paths]`

| 键 | 默认 | 含义 |
|---|---|---|
| `output.digest` | `"site/digest.html"` | 单文件摘要写到哪 |
| `archive.root` | `"archive"` | 每日快照的根目录 |
| `paths.collected` | `"data/collected"` | 原始抓取结果 |
| `paths.ai` | `"data/ai"` | 模型输入与决策 |
| `paths.processed` | `"data/processed"` | 应用决策后的数据 |

### `[[feeds]]` — 通用 RSS 源（**加源不写代码**）

普通 RSS/Atom 只要 4 行配置就会成为一个独立面板：

```toml
[[feeds]]
id       = "example-blog"
name     = "示例博客"
url      = "https://example.com/feed.xml"
fulltext = "today"
limit    = 20
```

| 键 | 必填 | 含义 |
|---|---|---|
| `id` | ✅ | 唯一标识，也会成为 `data/collected/<id>.json` |
| `url` | ✅ | feed 地址 |
| `name` | | 面板显示名，默认用 `id` |
| `fulltext` | | 覆盖 `collect.fulltext` |
| `limit` | | 覆盖默认条数（20） |
| `kind` / `date_source` / `source` | | 数据模型细节，一般不用改 |

> **需要特殊处理的站点仍写模块。** 少数派要在 feed 不足时调 API 兜底、阮一峰要爬归档页、晚点根本不是 RSS（是 POST 分页接口）——**这些是逻辑，不是配置**。硬塞进配置文件只会让两边都更难懂。

### 环境变量

标量配置可以用 `NEWSLETTER_` 前缀设置（列表和表只能写在文件里）：

```bash
NEWSLETTER_AI_BACKEND=none make all
NEWSLETTER_COLLECT_ENGINE=legacy make collect
NEWSLETTER_AI_TIMEOUT=120 make ai
```

---

## 6. 常见场景

**加一个 Telegram 频道** → 编辑 `config/config.toml`：

```toml
[telegram]
channels = ["MaiTungNews", "...", "新频道名"]
```

**加一个 RSS 源** → 追加一段 `[[feeds]]`（见上），零代码。

**临时抓全部全文** → `make collect FULLTEXT=all`

**关掉 AI 省钱** → `make all AI=none`

**换模型** → `make all AI_MODEL=gpt-5.6-sol`

**同一天重跑不想再花一次模型调用** → 在 `config.toml` 里设 `[ai] reuse = true`。注意它只对**同一天**有效：决策文件里记录了 `day`，跨天的文件会被自动重新生成。

**每天跑一次** → `make daily`：抓取 + AI + 渲染 + 归档快照。

**只跑不归档** → `make all`（不写 `archive/`）。

**补归档** → `make archive` 把**当前**产物快照一份；同日已有快照则生成 `-v2`，不覆盖。`make archive FORCE=1` 才替换：

```bash
make archive                 # 快照当前 data/ 与 site/digest.html
make archive FORCE=1         # 替换当天已有快照
make archive DAY=2026-10-12  # 指定快照名
```

**对比两个引擎的差异** → `make ab`，产出 `reports/engine-comparison.md`。

**看模型到底做了什么** → `data/ai/*_decisions.json` 是模型原始输出；`--report` 打印应用结果：

```bash
./.venv/bin/python tools/ai_apply.py --report
```

---

# 第二部分 · 系统架构

## 1. 总览

三个阶段，每个阶段**只读上一阶段的产物、只写自己的目录**：

```
                    ┌──────────────────────────────────────────┐
                    │  阶段 1 · 采集                            │
                    │  tools/collect.py + collectors/          │
                    │                                          │
  11 个信源  ──────▶ │  HTTP 抓取 → 归一化 → 时间戳 → 去重        │
                    │                                          │
                    └──────────────┬───────────────────────────┘
                                   │  data/collected/*.json   （原始，永不改写）
                                   ▼
                    ┌──────────────────────────────────────────┐
                    │  阶段 2 · AI（可选）                       │
                    │  tools/ai_apply.py + ai_stage.py          │
                    │                                          │
                    │  2a Telegram 去重/合并/删广告              │
                    │  2c/2d 分类（新闻侧 / 讨论侧，并行）         │
                    │  2b 全量重要性提取（不删条目）              │
                    │                                          │
                    │  模型只输出「决策」→ 脚本校验 → 应用         │
                    └──────────────┬───────────────────────────┘
                                   │  data/processed/*.json
                                   ▼
                    ┌──────────────────────────────────────────┐
                    │  阶段 3 · 渲染                            │
                    │  tools/render_digest.py                   │
                    │                                          │
                    │  内联图片 → 单文件 HTML（零外部请求）        │
                    └──────────────┬───────────────────────────┘
                                   ▼
                          site/digest.html
```

### 贯穿全局的三条原则

1. **原始数据永不改写。** `data/collected/` 是事实层，任何阶段都不动它。出错时可以从任意一层重跑。
2. **模型只给建议，脚本负责执行。** 模型输出的是 JSON「决策」，每一条都经过校验（id 是否存在、引用是否合法、值是否越界），非法的被拒绝并报告，而不是被猜测着执行。
3. **没有模型也能跑通全流程。** `AI=none` 时阶段 2 退化为直通，产出仍然完整，只是没有 AI 面板。

---

## 2. 阶段 1 · 采集层

### 目录结构

```
tools/collect.py              编排器：解析参数、调各源、写 JSON（~320 行）
tools/collectors/
  ├── base.py                 共享底座（~430 行）
  ├── extract.py              两个引擎的实现
  ├── feed.py                 RSS 源的共用骨架
  ├── registry.py             唯一的注册表
  ├── rss.py                  通用 RSS 源（配置驱动）
  └── <10 个源>.py            每源一个文件
```

### `base.py` — 共享底座

所有信源共用的能力，是「加一个源只需要写它自己那部分」的原因：

| 能力 | 说明 |
|---|---|
| `fetch()` | HTTP，带重试、超时、UA、TLS 处理 |
| `item()` | **统一数据契约**：每个条目都是同一个 dict 结构 |
| `stamp_now()` | 打抓取时间戳 |
| `mark_today()` | 标记是否为当天发布 |
| `source_status()` | 新鲜度：`updated_today` / `staleness_days` / `latest_published_at` |
| `fulltext_targets()` | 全文策略的判定（`today`/`latest`/`all`/`none`/`<N>`） |
| `dedupe()` / `content_hash()` | 确定性去重 |
| `CST` / `media_root()` | 时区与图片根目录 |

**统一数据契约**（`base.item()` 的产物）——这是整个系统的核心：

```python
{
  "source": "sspai",              # 机器标识
  "source_name": "少数派",         # 显示名（可被 config 覆盖）
  "kind": "article",
  "title": "...",
  "url": "...",                   # 原始链接，永不丢
  "published_at": "2026-10-13T17:00+08:00",
  "date_source": "rss",           # 时间戳怎么来的（见下）
  "summary": "...",
  "body_text": "...",             # 正文纯文本
  "body_html": "...",             # 正文富文本（modern 引擎，保留链接）
  "sections": [...],              # 分节正文（少数派等）
  "extra": {"rank": 3, ...},      # 源特有字段
  "content_hash": "...",          # 去重用
}
```

> `date_source` 是**诚实性字段**：微博热搜榜本身没有发布时间，所以它记录的是抓取时间并标注 `capture_time`；阮一峰的归档页只有月份，标注为近似日期。系统不编造时间。

### 两个可互换的引擎（`extract.py`）

| | `modern`（默认） | `legacy` |
|---|---|---|
| 依赖 | trafilatura + feedparser | **纯标准库** |
| 正文抽取 | 通用算法，**保留内链和小节标题** | 每站正则标记 |
| feed 解析 | feedparser | 手写正则 |
| 适用 | 日常使用 | 无依赖环境、作为干净基线 |

`modern` 不可用时会**自动回退** `legacy` 并告警；但如果你**显式**指定 `modern`，则直接报错退出（不静默降级）。

`make ab` 会用两个引擎各跑一遍，`tools/compare_engines.py` 生成差异报告——它能区分**真差异**和两类噪声（抓取时刻不同、实时榜单漂移）。

### 11 个信源各自怎么抓

| 源 | 方式 | 关键点 |
|---|---|---|
| **少数派** | RSS + v1 API 兜底 | 只在 feed 条数不足时调 API；全文只抓「派早报」 |
| **Telegram** | `t.me/s/<channel>` 公开预览 | **唯一真正的通用适配器**：18 个频道共用一套解析，无 API key、无登录 |
| **linux.do** | 晚报页分节 | 一个页面多个 `<section>`，每节独立成块 |
| **晚点** | POST `/site/index` | 需 `X-Requested-With`；**证书链不完整**，走 `RELAXED_TLS` |
| **极客公园** | RSS | 正文用通用抽取；链接 `http→https` 修正 |
| **阮一峰** | Atom + 归档页兜底 | 归档条目只有月份 → 日期是近似的 |
| **Anyway.Now** | `/now/` 页面 | 是**外链推荐列表**不是文章，故**不抓正文**（`/links/<id>` 是 301 跳转器） |
| **Hacker News** | Firebase API | 先取 id 列表，再逐条请求 |
| **微博热搜** | `ajax/side/hotSearch` | 需 `Referer: https://weibo.com/`；榜单无发布时间 |
| **知乎热榜** | `api.zhihu.com/topstory/hot-list` | 网页版 403，走 API；URL 从 API 形式改写为公开形式 |

**Telegram 是唯一被通用化的信源**——因为它天然是「同一套 HTML、多个频道」。其他源各有各的 quirk，所以每个是独立模块。这是**刻意的**：抽象只在真的同构时才有价值。

### 一个真实修复记录（说明解析的脆弱性）

Telegram 一条消息里可能有**两个** text div：引用的原消息（`js-message_reply_text`）和消息本身（`js-message_text`）。早期正则先命中引用块，导致**面板显示的是被引用的内容**。修复方式是先删掉引用块再匹配正文。这类问题只能靠真实数据发现——项目里所有解析都基于实测。

---

## 3. 阶段 2 · AI 层

### 为什么模型只输出「决策」

模型**不生成摘要文本**，只输出结构化的操作指令：

```json
{
  "schema": "newsletter.telegram-decisions/1",
  "day": "2026-10-13",
  "model": "pi:deepseek-v4.1-flash",
  "actions": [
    {"op": "merge", "primary": "telegram:A:1", "merge_ids": ["telegram:B:2"],
     "title": "...", "confidence": "high", "reason": "..."},
    {"op": "drop",  "id": "telegram:C:3", "reason": "..."},
    {"op": "spam",  "id": "telegram:D:4", "category": "promo", "reason": "..."}
  ]
}
```

好处：**可审计**（决策是数据，可 diff）、**可回滚**（原始数据没动过）、**幂等**（重复应用结果相同）、**安全**（非法决策被拒绝而不是执行）。

### 四个子阶段

**2a · Telegram 去重合并**（`prompts/telegram_merge.md`）

三个操作：

| 操作 | 作用 |
|---|---|
| `drop` | 删除重复/无信息量的消息 |
| `merge` | 合并同一事件的多条消息，**保留全部原文**供核对 |
| `spam` | 删除广告（7 类：`ad`/`promo`/`giveaway`/`crosspromo`/`affiliate`/`crypto`/`other`） |

广告判断有一条**防误杀规则**：一条消息只要陈述了**可核实的事实**就不能删，即使提到产品。「某公司发布新品，售价 5999 元」是新闻；只有整条都是「限时五折，点此购买」才是广告。提示词里还有硬规则 *"When unsure, do nothing"*。

合并会保留 `originals[]`（含原文和链接），前端有浮层可展开核对——**不丢失任何原始信息**。

### 少数派的派早报：采集层拆块

派早报是**一期含多条的合集**，不是一条新闻。采集时按正文的 `<h2>` 小节拆成一条一节：

```
API 列表(20 篇) → 取最新一期派早报 → 抓正文 → 按小节拆分
                                              ↓
                    推广节丢弃 / 新闻节 → 独立条目 / 简讯节 → 按顶层 <li> 再拆
                                              ↓
                        子条目取代整期，下游全部看到拆开后的新闻
```

- **id**：`sspai:<父hash>:<序号>`，序号是保留节内的 1-based 序号
- **推广节**（`少数派的近期动态`、`你可能错过的文章`）直接丢弃
- **简讯节**（`不妨一看的简讯` 等）按顶层 `<li>` 再拆，每条以首句为标题；嵌套的细节项归属其父条
- **简讯在页面上合并成一块**：分类仍逐条进行（每条进各自类目），但少数派面板把它画成一个「简讯」块，每条保留自己的锚点，所以汇总里的引用仍能跳到具体那一条
- **只取最新一期**：窗口里的旧期不进条目
- **派早报无条件抓正文**：拆块依赖小节，不受 `collect.fulltext` 策略约束（有意例外）
- **周末停稿**：派早报周六周日不更新（实测 21 期里周六周日各 0 期）。抓不到时显示「今日停稿」；工作日但当天那期还没出则显示「尚未发布」

**2c / 2d · 分类**（`prompts/classify.md`）

同一个提示词、同一套校验、同一套应用逻辑，跑在按 `nature` 切开的两半数据上：

| 子阶段 | 输入 | 内容 |
|---|---|---|
| 2c | `classify_input.json` | `news` 侧：新闻源 + 新闻频道 |
| 2d | `classify_forum_input.json` | `discussion` 侧：Hacker News、linux.do、分享频道 |

`longform`（阮一峰、Anyway、微信搬运工、知乎大巴扎）**两边都不进**——不定期更新的长文不是某一天的要闻。

- 类目名由模型自由命名，脚本不管；**跨天不可比是有意为之**，每天只有数量上的差别
- 输入每条只给 `title` + `summary`（≤ 400 字），**不含 `body_text`**，所以比 2b 便宜十倍左右
- 两次调用并行发出（`generate` 是同步 `subprocess.run`，释放 GIL），耗时约等于较慢的那一次
- 结果写独立的 `data/processed/categories.json`：分类失败不连带丢掉头条，反之亦然

脚本对模型给的分类强制执行形状规则（非法项拒绝并报告，不猜测执行）：

| 情况 | 处理 |
|---|---|
| id 不存在 / 属于另一半 | 拒绝 |
| 同一条被多个类目收下 | 只保留首次 |
| 同名类目出现两次 | 合并为一个 |
| `summary` 超过 60 字 | 截断 |
| 类目数超过 12 | 按条数保留前 12，其余并入「其他」 |
| 类目少于 2 条 | 并入「其他」 |
| 未被任何类目收下 | 脚本兜底进「其他」 |

所以每个类目块**覆盖整天**：已归类 218/220 会直接印在页面上。

**2b · 全天汇总**（`prompts/digest_summary.md`）

输入是**全部 11 个源**的条目（含长文），输出一段覆盖全天面貌的汇总 + 带权重的关键字。

- **不删除任何条目**，只在顶部叠加一个「今日汇总」面板
- 汇总覆盖**全天面貌**，不逐条罗列，**按主题分成 2–4 段**（每段一个线程），而不是一大块文字
- 每段自带 `keywords`，会在该段正文里**就地标记**（红色下划线）；模型改写掉的词会被跳过，不会报错
- 段下的关键字云是另一件事：全天跨段的排序，纯文本标签，**字号由模型给的权重决定**
- 长文**参与**汇总（深度报道也可能是一天的重点），只是不参与分类
- 模型额外输出 `source_ids`（**不显示**）作为审计线索，脚本校验这些 id 确实存在
- ⚠️ 汇总本身是自由文本，**没有 id 锚点**：脚本能查出「引用了不存在的条目」，但查不出「某句话与其依据不符」

### 校验层（`ai_stage.py`）

模型输出**全部经过校验**，任何非法项都被拒绝并报告：

- id 不存在 → 拒绝
- 引用了已被 `drop`/`spam` 掉的条目 → 拒绝
- 未知的 `spam` 分类 → 归为 `other`
- 合并引用了已被删为广告的消息 → 拒绝（而不是产出一个半空的合并）

校验结果会打印：`telegram: 0 ads removed, 4 dropped, 10 merged groups (69 -> 50 items), 0 rejected`。

### 模型溯源

模型会**自报身份**——实测 `deepseek-v4.1-flash` 会自称 `claude`。因此：

```python
parsed["model"] = actual                    # 以实际调用为准
parsed["model_self_reported"] = claimed     # 自报值单独留存供审计
```

否则 HTML 里会印出一个**从未运行过的模型名**。

### 后端抽象（`ai_model.py`）

```
BACKEND_TABLE = {
    "pi":    Backend(exe="pi",    base_args=("-p","--no-session","--no-tools",
                                             "--no-context-files"), model_flag="--model"),
    "codex": Backend(exe="codex", base_args=("exec","--skip-git-repo-check",
                                             "--sandbox","read-only"),
                     model_flag="--model", experimental=True),
}
```

**pi 的四个隔离参数一个都不能少**，尤其 `--no-context-files`：不加它会加载用户全局 `~/.pi/agent/AGENTS.md`，而那要求模型用英文回答并在结尾追加 `> Recap:` 引用块——**两者都会破坏 JSON 解析**。

其他工程细节：

- **输入走 stdin**：`digest_input.json` 约 **290 KB**（中文按 UTF-8 字节计），超过 Linux 单参数上限（128 KB）
- **输出防御性解析**：原文 → markdown 围栏内 → 首尾大括号，三层兜底
- **失败降级**：模型调用失败不会中断流程，只是没有 AI 面板
- **默认复用**：已有决策文件则不重复调用（`--ai-force` 覆盖）

---

## 4. 阶段 3 · 渲染层

`tools/render_digest.py` 把 `data/processed/` 渲染成**一个 HTML 文件**：

- **零外部请求**：所有图片 base64 内联，CSS/JS 内联，字体用系统字体栈。已用真实浏览器（Chrome over CDP）验证过：产物网络请求数 = **0**
- **双主题**：浅色（`#f7f4ef` 底 / `#1b1716` 正文 / `#c4291f` 强调）与深色（`#0c0b0b` 底 / `#efeceb` 正文 / `#ee5d50` 强调），单一红色强调色，字体为系统字体栈（`DB Punct Sans` → `Inter` → `SF Pro Text` → 系统无衬线）
- **富文本正文**：`modern` 引擎的正文走白名单净化（标签白名单 + 仅允许 `http(s)` href + 自动补齐配对标签），保留内链和小节标题
- **Telegram 瀑布流**：图片在文本上方，合并条目有浮层可展开原文

### 面板构成

```
今日汇总    ← 汇总（全天面貌 + 带权关键字）+ 新闻 + 讨论（任一有内容即出现）
少数派 / Telegram / linux.do / 晚点 / 极客公园 / 奇客 Solidot /
阮一峰 / Anyway.Now / Hacker News / 知乎热榜
```

顺序与名称来自 `config.toml` 的 `sources.order` / `sources.labels`。

「今日汇总」里的每条都带一个引用 chip（`去 在花 看原文 ↗`），点一下跳到它所在面板里的那一条并高亮。源名用更亮的颜色与两侧留白从周围文字里区分出来，末尾箭头表示它会离开当前这一行。chip 不带序号，所以同源的两条引用看起来一样——识别靠点击后的高亮。

深链也支持：`site/digest.html#p-sspai/it-<id>` 会直接落到那一条。

---

## 5. 归档（`tools/archive.py`）

`make daily` = `make all` + 归档。归档是**复制**，不是重跑，也不是移动：

```
archive/
  index.json            清单（最新在前）
  2026-10-13/
    meta.json           引擎、后端、条数、来源路径
    collected/          原始抓取数据
    ai/                 发给模型的输入 + 模型返回的决策
    processed/          应用决策后的数据（去重合并后的）
    digest.html         最终单文件日报的副本
```

**四样东西缺一不可**：原始数据、AI 决策、合并后的数据、最终 HTML。前三个是「结论是怎么得出的」的完整证据链，第四个是「当时长什么样」。

### 核心原则：根目录永远有一份最新日报

归档时 `site/digest.html` 是被 **`copy2` 复制**进快照的，**从不移动、从不删除**。所以：

- 无论何时，`site/digest.html` 都是**最新那一期**，路径固定，可以直接发给别人
- 下一次 `make all` / `make daily` **允许覆盖**它（这是正常行为，不是事故）
- 历史期数不会因此丢失——它们各自在 `archive/<day>/digest.html` 里

### 两个安全设计

1. **先在临时目录构建，全部成功后才原子替换。** 早先的版本「先删旧快照再重建」，一旦失败就会**销毁已有归档**——这是数据丢失 bug，已修并测试了失败路径。
2. **缺产物就报错，不写空快照。** `data/processed/` 或 `site/digest.html` 不存在时直接退出（码 1），而不是归档一个看起来成功的空壳。

### 为什么默认不复用 AI 决策

`archive` 不重跑流水线，所以它不存在这个问题；但 `make all` 会。`ai.reuse` **默认 `false`**，因为决策文件**只对生成它的那一天有效**：

- Telegram 消息 id 会递增，昨天的 id 今天不存在 → 决策静默失效（有警告，但退出码 0）
- **更危险的是 URL 类条目**（Hacker News、博客、RSS）：帖子在榜上停留多天，URL 不变 ⇒ id 不变 ⇒ **昨天的判断被应用到今天的数据上，零报错**，页面上顶着昨天的头条

所以默认每次都调模型。想省钱可以把 `reuse` 设为 `true`，此时仍会校验 `day`：跨天的文件自动重新生成，只有同一天重跑才复用。

### 某个源当天抓取失败时会怎样

**面板保留，但内容换成说明**——不显示旧数据，也不静默消失：

```
微博热搜                    未采集
本次未采集

抓取失败
本次未能取得数据，下方为空白而非旧内容
  FetchError: connection reset by peer
```

三种情况会走这条路，措辞不同：

| 原因 | 面板标题 | 触发条件 |
|---|---|---|
| `failed` | 抓取失败 | `collected/index.json` 记了 `ok: false`，附错误原文 |
| `stale` | 今日无新数据 | 目录里只有更早采集的文件，已跳过 |
| `missing` | 今日无数据 | 本次没有该源的任何记录 |

顶栏也相应诚实：`240 条 · 8/10 源今日更新 · 2 源未取得`。分母是**本次应采集的源数**，所以「7 个源坏了」不会再看起来像「7 个源全部正常」。

**被 `sources.enabled` 关掉的源不算失败**，不会出现在这里——它的沉默是预期的。

---

## 6. 配置系统（`tools/config.py`）

- **TOML**：`tomllib` 是 Python 3.11+ 标准库，**零新增依赖**，且支持注释
- **`defaults()` 就是 schema**：它同时是默认值、校验依据、`--show-config` 的渲染源
- **未知键报错**：配置文件的拼写错误必须响亮地失败，而不是静默保留旧值
- **`--show-config`** 输出合法 TOML + 每行来源标注，可 diff
- **开放表**：`sources.limits` / `sources.labels` / `sources.units` 的键是源名或「源:单元」，允许自由键（配置新增的 feed 也要能设标签），但**值的类型仍校验**

---

## 7. 扩展点

### 加一个信源（需要特殊处理）

1. 新建 `tools/collectors/<name>.py`，实现 `collect(limit, fulltext, engine, **kw) -> list[dict]`
2. 在 `tools/collectors/registry.py` 的 `SOURCES` 加一行
3. （可选）加进 `ORDER` / `SOURCE_LABELS`，或直接在 `config.toml` 里配

### 加一个普通 RSS 源

**只改配置**，不写代码：

```toml
[[feeds]]
id = "my-blog"
url = "https://example.com/feed.xml"
```

### 加一个模型后端

在 `tools/ai_model.py` 的 `BACKEND_TABLE` 加一条记录。命令的拼装、模型参数注入、提示词传递都由 `build_command()` 统一处理。

---

## 8. 测试与验证

```bash
make test      # 离线测试，不联网、不调模型
```

覆盖：采集解析（Telegram 引用块、图片路径、日期选择）、TLS 校验未被削弱、归档原子替换与版本命名、**跨天决策复用校验**、AI 决策校验、渲染器报告、配置优先级与校验、后端命令形状、模型溯源覆盖。

「不联网」是刻意的：每个 fetch 都被合成输入替换，所以测试快（<0.02s）且稳定。

---

## 9. 刻意的取舍

**没有做成配置项的**，以及为什么：

| 项 | 原因 |
|---|---|
| pi 的隔离参数 | **正确性参数**。删掉 `--no-context-files` 会污染 JSON——这不是偏好问题 |
| UA 字符串、时区、TLS 设置 | 同上，改坏只会带来难查的故障 |
| 各站 quirk（兜底 API、链接改写、正文标记） | **这是逻辑，不是配置**。塞进配置文件会让两边都更难懂 |

**已知限制**：

- Telegram 只能抓**公开**频道（走 `t.me/s/` 预览页）
- 微博/知乎热搜是**实时榜**，两次运行结果会漂移，不是可复现性问题
- 晚点的 TLS 证书链不完整，需特殊处理
- `--day today` 在凌晨返回 0 条，用 `auto`

---

## 10. 目录速查

```
Makefile                  常用命令
config/config.toml        全部配置
requirements.txt          钉死的依赖（含传递依赖）

tools/
  collect.py              阶段 1 编排器
  ai_apply.py             阶段 2 CLI
  ai_stage.py             阶段 2 纯函数（构建输入、校验、应用）
  ai_model.py             模型后端抽象（profile 表）
  render_digest.py        阶段 3 渲染器
  archive.py              每日归档
  config.py               配置层
  collectors/             采集层（base/extract/feed/registry/rss + 10 个源）
  demo_decisions.py       无模型演示（启发式假决策）
  compare_engines.py      引擎差异报告
  run_ab.py               一键跑两引擎对比
  telegram_preview.py     Telegram 预览页的薄封装

prompts/
  telegram_merge.md       阶段 2a 提示词
  classify.md             阶段 2c / 2d 提示词
  digest_summary.md       阶段 2b 提示词

site/digest.html          产物（单文件，零外部请求）
tests/                    离线回归测试
archive/                  每日快照（gitignore）
```

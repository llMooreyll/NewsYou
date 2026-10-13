# 使用文档

本文档是交付说明：整个系统怎么用、三个阶段各自做什么、时间规则怎么定、以及如何扩展新信源。

---

## 一、系统架构：三个阶段

```
阶段 1  抓取汇总        tools/collect.py     全自动
            ↓           data/collected/*.json
阶段 2  可选 AI 处理     tools/ai_apply.py    半自动（AI 只出决策，脚本负责执行）
            ↓           data/processed/*.json
阶段 3  渲染成 HTML      tools/render_digest.py   全自动
                        site/digest.html     单文件，可直接分享
```

### 为什么阶段 2 是「AI 出决策 + 脚本执行」而不是「AI 直接改 JSON」

这是本系统最关键的一个设计决定。AI **不直接编辑数据**，只输出一份「决策清单」：

| 好处 | 说明 |
|---|---|
| 可审计 | 每条改动是一条独立记录，能逐条查看 AI 为什么这么判断 |
| 可回滚 | `data/collected/` 的原始数据**永不被修改**，删掉决策文件即可还原 |
| 幂等 | 同一份决策重复执行结果一致，不会累积错误 |
| 安全 | AI 无法污染它没被要求修改的字段；非法指令被校验器拒绝并跳过 |
| 可测试 | 校验和执行逻辑不依赖模型，没有 AI 也能跑通全流程 |

**代价**：需要多跑一条命令。但换来的是「AI 出错不会毁数据」。

### 数据流

```
data/collected/          阶段 1 产出，原始快照，只读
  ├── index.json         各源新鲜度状态
  ├── telegram.json      7 个频道当日全部消息
  ├── sspai.json  ...    其余 9 个源
  └── telegram_duplicate_groups.json   哈希级完全重复（通常为 0）

data/ai/                 阶段 2 的输入与输出
  ├── telegram_input.json       ← 给 AI 的输入（2a）
  ├── telegram_decisions.json   ← AI 的回复（2a）
  ├── digest_input.json         ← 给 AI 的输入（2b）
  └── digest_decisions.json     ← AI 的回复（2b）

data/processed/          阶段 2 产出，阶段 3 的输入
  ├── index.json
  ├── telegram.json      已应用合并/删除
  ├── digest.json        今日汇总面板
  └── <其余源>.json      原样透传

site/digest.html         阶段 3 产出，单文件
```

---

## 二、快速开始

### 最简：只要抓取和渲染（不用 AI）

```bash
python tools/collect.py --all --outdir data/collected
python tools/ai_apply.py                 # 没有决策文件也会正常跑，只做透传
python tools/render_digest.py --indir data/processed --output site/digest.html
```

打开 `site/digest.html` 即可。此时没有「今日汇总」面板，其余 10 个信源面板正常显示。

### 完整：带 AI 处理

仓库里自带一份**手写的示例决策**（`tools/demo_decisions.py`），用来演示两种决策文件的格式。它不是模型输出，标题里也标注了 `not model output`。想直接看效果：

```bash
python tools/collect.py --all --outdir data/collected
python tools/demo_decisions.py      # 用真实重复对生成示例决策
python tools/ai_apply.py
python tools/render_digest.py --indir data/processed --output site/digest.html
```

真实使用时，把 `demo_decisions.py` 换成模型输出即可：

```bash
# 1. 抓取
python tools/collect.py --all --outdir data/collected

# 2a. Telegram 去重合并
#     把 data/ai/telegram_input.json 交给 AI（提示词已内嵌在文件的 instructions 字段）
#     把 AI 返回的 JSON 存为 data/ai/telegram_decisions.json

# 2a. 应用，并生成 2b 的输入
python tools/ai_apply.py

# 2b. 全量重要性排序
#     把 data/ai/digest_input.json 交给 AI
#     把返回的 JSON 存为 data/ai/digest_decisions.json

# 2b. 应用
python tools/ai_apply.py

# 3. 渲染
python tools/render_digest.py --indir data/processed --output site/digest.html
```

一条命令看全流程：

```bash
python tools/ai_apply.py --report     # 打印应用了哪些改动、拒绝了哪些
python tools/ai_apply.py --dry-run    # 只校验不写盘
```

---

## 三、时间规则（重要）

### 抓取的时间窗口：**每个信源各自决定**

系统**没有**统一的全局时间窗口。`--all` 不是「给我过去 24 小时」，而是「每个源返回它最新的 N 条，或它自己的那个时间单位」。

实测一次 `--all` 的跨度：

| 信源 | 时间语义 | 典型跨度 |
|---|---|---|
| `telegram` | **指定某一天** | 1 天 |
| `linuxdo` | **指定某一期** | 1 天 |
| `hackernews` / `weibo` / `zhihu` | 当前榜单快照 | 0–3 天 |
| `sspai` / `geekpark` / `latepost` | 最新 N 条 | 4–30 天 |
| `ruanyifeng` / `anywayfm` | 最新 N 条（低频） | 1–4 个月 |

### 可以按天指定的信源

```bash
python tools/collect.py telegram --day auto        # 默认：自动选最近有数据的一天
python tools/collect.py telegram --day today
python tools/collect.py telegram --day yesterday
python tools/collect.py telegram --day 2026-10-12
python tools/collect.py linuxdo --date 2026-10-11
python tools/collect.py --dates                    # 列出 linuxdo 所有可抓日期
```

> **注意**：`--day today` 在凌晨执行会返回 0 条（新的一天还没消息）。默认 `auto` 才是安全选择。

### 全文抓取策略：默认只抓当天更新

**除 linuxdo 和 telegram 外**，所有支持全文的信源默认遵循 `--fulltext today`：

| 模式 | 行为 |
|---|---|
| `today`（默认） | **只抓当天发布的条目**的全文；非当天只保留标题+链接 |
| `latest` | 当天发布的 + 最新一条 |
| `all` | 所有支持全文的条目 |
| `none` | 完全不抓全文 |
| `3` | 最新 3 条，不看日期 |

```bash
python tools/collect.py --all                          # 默认 today
python tools/collect.py --all --fulltext latest        # 放宽
python tools/collect.py sspai --fulltext all           # 只要少数派且全抓
python tools/collect.py --all --fulltext none          # 全都不抓，最快
```

各源的全文来源：

| 信源 | 全文范围 |
|---|---|
| `sspai` | 派早报（其余仅标题+链接） |
| `latepost` / `geekpark` | 支持全文的文章 |
| `ruanyifeng` / `anywayfm` | 支持全文的条目 |

### 时间戳可信度

每条数据都带 `date_source` 字段，标明时间从哪来：

| `date_source` | 含义 | 可信度 |
|---|---|---|
| `post` / `rss` / `atom` / `api` / `article_page` / `issue_date` | 源站直接提供 | 高 |
| `relative_balloon` | 页面相对时间反推 | 中 |
| `archive_month` | 只有年月，日期填 1 号 | 低（已标记） |
| `collection_time` | **源本身没有时间**，用抓取时刻 | 仅代表抓取时刻 |

微博热搜、知乎热榜属于最后一类——接口只返回当前榜，**没有历史**，所以时间戳就是抓取时间。

### 「今日未更新」标志

每个信源在 `index.json` 和面板头部都会标注：

```json
"status": {
  "updated_today": false,
  "staleness_days": 1,
  "latest_published_at": "2026-10-12T19:26:00+08:00"
}
```

HTML 上渲染成 `今日已更新` 或 `今日未更新 · 最新 N 天前`。抓取结束时终端也会汇总：

```
day 2026-10-13: 3 source(s) updated today, 7 stale
  stale: sspai(1d), linuxdo(1d), latepost(3d), geekpark(1d), ruanyifeng(1d), anywayfm(3d), zhihu(1d)
```

---

## 四、阶段 2 详解

### 2a — Telegram 去重合并

**为什么需要**：7 个中文频道互相转发，同一条新闻常有 3–4 个版本。实测某日 46 条消息中，哈希级完全重复为 **0 组**——各频道措辞从不逐字相同，所以只能靠语义判断。

AI 只做两个操作：

| 操作 | 使用条件 |
|---|---|
| `drop` | 同一事件，其中一个版本信息量**严格更少**（更短、缺数字、缺来源） |
| `merge` | 同一事件，两个版本**各有独有信息**（一个有数字、一个有来源） |

**AI 不能做**：删除含独有信息的条目、合并仅主题相同的事件、引用不存在的 id。

**合并后保留原始消息**：合并结果带 `ai_merged: true` 和 `originals` 数组，原始两条消息的全文、链接、时间戳全部保留。前端渲染成「AI 合并 · 2 条」徽章 + 「展开原始消息」按钮，可随时核对。

提示词：`prompts/telegram_merge.md`（同时内嵌在 `telegram_input.json` 的 `instructions` 字段里，不会丢）。

### 2b — 全量重要性排序

**不删除任何消息**，只在所有信源之上加一层「今日汇总」面板。

重要性判断有两个独立信号，AI 需在 `why` 字段说明用了哪个：

- **信号 A：多源印证** — 同一事件被多个独立信源报道
- **信号 B：事件本身重要** — 政策变化、重大发布、监管行动、安全事故等，单源也成立

**必须忽略**：推广、返利链接、抽奖、互动诱导、纯观点无新事实。

输出上限 10 条 highlight（宁少勿多）+ 最多 5 条 themes（当日脉络）。

提示词：`prompts/digest_highlights.md`。

### 校验器行为

AI 的输出会被严格校验，非法项**报告并跳过**，绝不猜测：

```
telegram: 0 dropped, 1 merged groups (46 -> 45 items), 6 rejected
  ! action 0: unknown id 'telegram:FAKE:999'
  ! action 1: unknown merge_ids ['telegram:NOPE:1']
  ! action 2: merge needs both title and summary
  ! action 4: unknown op 'explode'
  ! action 5: not an object
```

覆盖的校验：未知 id、空 merge、标题/摘要缺失、未知操作、非对象项、重复引用同一条目、importance 越界（500 → 100）。

---

## 五、阶段 3：渲染

```bash
python tools/render_digest.py --indir data/processed --output site/digest.html
python tools/render_digest.py --no-embed     # 图片不内嵌，文件更小但不可单独分享
```

产出**单文件 HTML**，CSS/JS/图片全部内联（图片转 base64），无任何外部请求，可直接发给别人。

### 界面设计

样式和脚本放在 `tools/digest_assets/digest.css` 与 `digest.js`，渲染时内联进 HTML，产物仍是单文件。

- **浅色 / 深色双主题**：首次打开跟随系统，右上角按钮手动切换并记在 `localStorage`；主题在首帧前确定，不闪烁。强调色为红色：深色下朱红 `#ee5d50`，浅色下朱砂 `#c4291f`，正文与辅助文字对比度均满足 WCAG AA。1px 发丝线分层，圆角统一 2px。
- **中西文混排**：衬线体用于标题和长文正文（宋体 / Noto Serif CJK 搭配 Source Serif、Charter、Georgia），无衬线体用于列表、卡片和榜单，等宽体用于数字与元信息。西文字体排在中文字体之前，中文引号和破折号通过 `unicode-range` 交给中文字体，保持全角。如果系统没有合适的中文衬线体，标题会整体回落为无衬线，避免西文衬线配中文黑体。
- **顶部导航为一行**：放不下时可横向滑动（触控板/滚轮均可），哪一侧还有被隐藏的标签，哪一侧就出现渐隐遮罩和箭头按钮。当前标签下方有滑动指示条，当日更新过的信源带一个小点。键盘 `1`–`9`、`0` 直达，`←` `→` 翻页。
- **侧边导航**：linux.do（板块）、少数派（派早报与近期文章同级，派早报下的各条新闻缩进一级）、今日汇总（头条/要点/脉络）带吸附侧栏和滚动高亮；Telegram 的侧栏是频道筛选。窄屏下侧栏变成吸附在顶栏下方的第二条横向滚动条。
- 动效共用一组缓动曲线：面板按切换方向左右滑入、列表错峰入场、榜单热度条生长、全文展开用 `grid-template-rows: 0fr → 1fr` 做真实高度过渡，并适配 `prefers-reduced-motion`。
- 没有 JS 时所有面板顺序展开，Telegram 退回 CSS 多列布局，内容不会丢。

### 各类面板

| 类型 | 信源 | 形式 |
|---|---|---|
| 榜单 | Hacker News / 微博 / 知乎 | 固定行高：排名 · 标题 · 热度条 · 数字列，一屏看完 |
| 卡片流 | Telegram | 按阅读顺序分配到最短的列（JS 瀑布流），图片尺寸在构建时写入，不会跳动 |
| 分段长文 | 少数派派早报 / linux.do | 侧栏导航 + 正文 |
| 文章列表 | 晚点 / 极客公园 / 阮一峰 / Anyway.Now | 日期列 + 标题 + 两行摘要，全文在展开区内 |

### Telegram 卡片的层次

频道 + 时间（等宽小字）→ 图片（点击放大）→ 回复引用 → 标题 → 正文（过长时折叠）→ 话题标签 → 阅读量、表情、原帖链接。频道签名（「频道: @x」「投稿: @bot」）和整行话题标签会从正文中剥离，标签单独显示。长消息在卡片里保留折叠预览，「展开全文」在浮层中打开。合并条目带「AI 合并」标记，「对照原始消息」也在浮层中打开；从全文浮层里打开时会叠在上一层之上，× / Esc / 点击背景只关闭最上一层，「返回」回到下一层。

---

## 六、扩展新信源

三步：

1. **写抓取函数** `source_<name>(limit, fulltext=..., **_) -> list[dict]`
   - 必须返回含 `title`、`url` 的 dict 列表
   - 建议用 `item()` 辅助函数构造，它会自动算 `content_hash` 和 `dedupe_key`
   - 时间戳尽量给 `published_at` + `date_source`
   - 全文写进 `body_text`，用 `fulltext_targets(items, fulltext)` 决定抓哪些

2. **注册** 到 `tools/collect.py` 的 `SOURCES` 字典：`"name": (source_name, 默认limit)`

3. **登记显示信息**：在 `tools/ai_stage.py` 的 `SOURCE_LABELS` 和 `tools/render_digest.py` 的 `SOURCE_META` / `ORDER` 里加上

然后：

```bash
python tools/collect.py <name> --limit 5 --list     # 先验证
```

系统会自动：纳入 `--all`、写进 `index.json`、计算新鲜度、纳入 AI 输入、出现在 HTML 导航里。

### 已实现的抓取策略

| 策略 | 用于 |
|---|---|
| RSS/Atom 解析（stdlib） | geekpark, sspai, ruanyifeng |
| JSON API | latepost, hackernews, weibo, zhihu, sspai |
| HTML + 平衡 div 扫描 | 派早报正文、latepost 正文、ruanyifeng 正文 |
| 分节 HTML 解析 | linuxdo（六个板块） |
| 公开预览分页 | telegram（`?before=<id>`） |

---

## 七、命令速查

```bash
# 阶段 1
python tools/collect.py --list-sources              # 列出所有信源
python tools/collect.py --all                       # 抓全部
python tools/collect.py telegram --day 2026-10-12   # 指定某天
python tools/collect.py linuxdo --date 2026-10-11   # 指定某期
python tools/collect.py --dates                     # 可用日期
python tools/collect.py sspai --limit 30 --list      # 单源预览
python tools/collect.py --all --fulltext latest      # 放宽全文策略
python tools/collect.py --all --no-images            # 不下载图片
python tools/collect.py --all --no-ai-input          # 不生成 AI 输入

# 阶段 2
python tools/demo_decisions.py                       # 生成手写示例决策（非模型输出）
python tools/ai_apply.py                            # 应用决策
python tools/ai_apply.py --dry-run                  # 只校验
python tools/ai_apply.py --report                   # 打印改动详情

# 阶段 3
python tools/render_digest.py --indir data/processed --output site/digest.html
```

---

## 八、隐私与合规

- 所有抓取都是**公开内容**的普通 HTTP 请求；telegram 走公开预览，不需要 `api_id`、不需要登录。
- 图片下载默认开启，仅存于本地 `data/telegram/media/`。这些是第三方新闻图片，版权属于原发布方，仅作个人参考。
- **AI 步骤不会自动联网**：`data/ai/*_input.json` 只是本地文件，是否交给模型由你决定。
- Telegram 现行 API 条款限制将平台数据用于 AI/ML 训练与微调。若严格解读，请保持阶段 2 为手动、或跳过阶段 2，仅依赖阶段 1 的去重 + 人工审核。
- 本文档不构成法律意见。

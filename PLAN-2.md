# NewsYou · 第二轮改造计划

> 状态：待评审，未开始实施。
> 基线：`master` @ `3172ba5`（第一轮 AI 分类改造已提交）。
> 上一轮设计基线：[PLAN.md](PLAN.md)。最后更新：2026-10-14。
>
> 本轮九项已定需求：
>
> 1. 少数派 RSS 降级为兜底（API 出问题才用）
> 2. 删除少数派 info 端点
> 3. 派早报在**采集层**拆块，拆完后下游全部看到拆块后的数据
> 4. 派早报每一条作为独立信息送入 AI 分类
> 5. 长文打标签、不送入分类；晚点与极客公园也算长文
> 6. **长文不从头条排除**；头条机制改为「汇总长句 + 关键字列表」，「要点」删除
> 7. 简讯**不拆**，整节作为一个整体进 AI；由提示词的子任务让 AI 拆解其中的多条消息
> 8. chip 只需显示来源；Telegram 的 chip 要带频道名
> 9. Telegram 显示频道中文名（用抓到的标题，不加覆盖表）
>
> 另已确认：
>
> - id 用序号
> - RSS 兜底时**不拆块**
> - `limits.sspai = 20` 语义不变（20 篇正文 + 拆出的多条是预期）
> - **只抓一期派早报**（当天那期），其余期不抓全文、也不进条目
> - 派早报周末停稿，抓不到时页面显示「今日停稿」
> - 汇总保留审计字段
> - 关键字纯文本、字号按 AI 给的权重变化
> - 汇总覆盖全天面貌，不逐条罗列

---

## 1. 现状核实（本轮结论的实测依据）

### 1.1 少数派现有四个端点、两种职责

| # | 端点 | 职责 | 实测 |
|---|---|---|---|
| 1 | `sspai.com/feed`（RSS） | 发现 | 只给 **10 条** |
| 2 | `api/v1/articles?limit=N` | 发现 | `limit=20` → 20 条 |
| 3 | `api/v1/article/info/get?id=` | 详情 | author / tags / released_at |
| 4 | `sspai.com/post/{id}` | 详情 | **全文与 sections 的唯一来源** |

`sources.limits.sspai = 20` 而 RSS 只给 10 条 → **API 备用每天都必然触发**，不是异常兜底。

### 1.2 没有单一端点同时给"正确摘要 + 全文"

同一个 id=116138 的 `summary`：

| 端点 | summary |
|---|---|
| `api/v1/articles`（列表） | 「少数派的近期动态给电话加上「辅助驾驶」？…送出50元面值京东卡。…」← 推广文案 |
| `api/v1/article/info/get` | 「Apple Watch 在中国大陆推出睡眠呼吸暂停迹象提示功能、Google 发布 Gemini 4 Argon 等。」← 真摘要 |

`info` 的 `body` 只有 **384 字**，内容是文章**尾部**的两个推广节，不是全文。全文只有端点 4 有（实测 8105 字 / 11 节）。

### 1.3 API 列表是 info 的严格超集（删 info 零损失）

实测同一批 12 篇：

| 字段 | 结论 |
|---|---|
| `author` | list 50/50 有值，与 info **逐条相同**，丢失 0/12 |
| `tags` | list 反而**更多**（`tags` + `custom_tags` 合并），丢失 0/12 |

例：`甜品卡之王` 在 list 有 7 个 tag（游戏/显卡/AI/市场行情/攻略/DIY攒机/PC），info 只有 1 个（游戏）。

### 1.4 派早报可用结构化字段精确识别

`api/v1/articles` 的 128 个字段含 `post_type`：

| 样本 | `post_type=1` | `post_type=3` |
|---|---|---|
| 50 条 | 47 条，标题含「派早报」**0** | 3 条，**3** |
| 200 条 | 194 条，**0** | 6 条，**6** |
| 500 条 | 479 条，**0** | 21 条，**21** |

**500 条样本零误判**，比 `"派早报" in title` 可靠。

### 1.5 `sections` 已是干净的「标题 + 正文」

```python
{'title': 'Apple 发布 HomePod mini 2、HomeView 等新品',
 'text':  '10 月 13 日，Apple 宣布推出新一代智能扬声器 HomePod mini 2。…',
 'html':  '<p>10 月 13 日，Apple 宣布推出新一代智能扬声器 <a href="…">…'}
```

每个 section 的 HTML **自包含且标签平衡**（只有 `<p>`/`<a>`）；同一期内 **标题唯一**（11 节 11 个唯一标题）。

### 1.6 派早报是日频、每期 7–11 节、周末停稿

实测 21 期（09-11 → 10-14）的星期分布：

| 星期 | 期数 |
|---|---|
| Mon | 4 |
| Tue | 5 |
| Wed | 5 |
| Thu | 4 |
| Fri | 3 |
| **Sat** | **0** |
| **Sun** | **0** |

**周六周日各 0 期，确认周末停稿。** 间隔跳跃也印证：`09-11(五)→09-14(一)`、`09-18(五)→09-21(一)`、`10-09(五)→10-12(一)` 都跳过周六周日；`09-30(三)→10-08(四)` 跳过 7 天（国庆假期）。

发布时刻在 **06:26 – 08:51** 之间。

### 1.6b 20 条窗口只含 1 期派早报

实测不同 limit 下窗口内的派早报期数：

| limit | 日期跨度 | 含派早报 |
|---|---|---|
| 20 | 10-13 15:00 → 10-14 15:00（约 1.2 天） | **1 期**（10-14） |
| 30 | 10-13 11:57 → 10-14 15:00 | **1 期** |
| 50 | 10-12 06:34 → 10-14 15:00 | 3 期 |

少数派平均每天 16.7 篇，所以 `limits.sspai = 20` 的窗口约覆盖 1.2 天，**通常只含当天那一期**。

→ 这解释了为什么"只抓一期"是可行的默认；也说明上一版计划担心的"1–3 期"只在 limit 被调大时出现。

### 1.7 节标题三类，两类正则 100% 覆盖

| 类别 | 标题变体 | 频次 |
|---|---|---|
| **新闻节** | 各自独立的新闻标题 | 6–8 节/期 |
| **简讯节** | `不妨一看的简讯` / `看看就行的简讯` / `看看就行的其他消息` / `看看就行的小道消息` | 1 节/期 |
| **推广节** | `少数派的近期动态`(4×) / `你可能错过的文章`(2×) / `你可能错过的好文章`(2×) | 0–2 节/期 |

```python
PROMO = re.compile(r"^(少数派的近期动态|你可能错过的(文章|好文章))$")
DIGEST = re.compile(r"^(不妨一看|看看就行)的")
```

⚠️ **简讯节不一定在最后**：116138 与 115197 里它排在两个推广节**之前**。不能用"末尾 N 节"过滤，必须按标题模式。

### 1.8 简讯节：整节 640–1400 字、内部 2–8 个顶层条目

实测 6 期的简讯节：

| 期 | 顶层条目 | text 长度 |
|---|---|---|
| 116138 | 4 | 711 |
| 116033 | 8 | ~1400 |
| 115845 | 2 | ~640 |
| 115455 | 2 | ~1286 |
| 115197 | 5 | ~1100 |
| 115532 | 4 | ~1286 |

**关键：`sections[].text` 保留了每条之间的换行**（`<li>` 被 trafilatura 转成 `\n` 分隔）：

```
'10 月 12 日，SammyGuru 独家披露三星…\n 美国联邦调查局（FBI）逮捕了…\n GamesBeat 爆料称…\n The Information 报道称…'
```

换行数 = 条数 − 1，所以**整节作为一条送进去，AI 也能看到"这里有几条"**。这比在脚本里拆更可靠——AI 能读到全文（711 字），而脚本只能靠 `<li>` 位置。

### 1.8b 简讯内部有嵌套，顶层条目是干净的（订正一处早期误判）

115532 那期的简讯有 **11 个 `<li>`、3 个 `<ul>`** —— 存在嵌套。按顶层 `<li>`（嵌套深度 = 1）提取后：

| 期 | 顶层 `<li>` | trafilatura 换行段 | 一致 |
|---|---|---|---|
| 116138 | 4 | 4 | ✓ |
| 116033 | 8 | 8 | ✓ |
| 115845 | 2 | 2 | ✓ |
| 115532 | **4** | **4** | ✓ |
| 115455 | 2 | 2 | ✓ |
| 115197 | 5 | 5 | ✓ |

**6/6 期完全一致。**

⚠️ **我在上一版计划里把这 9 条当成 9 条同级碎片，是误判。** 实际结构是：

```
顶层 1. 埃隆·马斯克在 X 平台宣布…Grok Bot…           ← 独立
顶层 2. 阶跃终端将于 10 月 13 日发布…STEPX Neo。      ← 独立
顶层 3. Apple 官宣 10 月 13 日…「Welcome home」新品活动。  ← 话题
         └ 嵌套 a. Apple 预计将推出首款配备屏幕的智能家居中枢…
         └ 嵌套 b. 功能方面，据爆料，该设备将整合摄像头…
         └ 嵌套 c. 系统方面，智能家居中枢据称将运行…
         └ 嵌套 d. Apple 还可能与 LG 合作提供视频门铃…
顶层 4. Mark Gurman 还表示，Apple 还计划于 10 月 27 日…  ← 话题
         └ 嵌套 a. iMac 主要是更新芯片到 M6…
         └ 嵌套 b. 新款 iPad mini 则预计升级 OLED 显示屏…
```

所以「功能方面…」「系统方面…」「iMac…」「新款 iPad mini…」**本来就是嵌套子项，不是同级碎片**。网页自身已经把归属关系编码进 HTML 了。

**结论**：简讯拆分比上一版判断的容易得多——顶层结构干净，且 trafilatura 的换行分段与顶层 `<li>` 精确对应（6/6）。脚本拆、AI 拆都可行。

按本轮决定，**脚本不拆、整节作为一条进 AI**（§3.3）；上面这些事实说明 AI 拿到的是 4 段干净的顶层条目，拆分质量有保障。

### 1.8c 简讯条目的文本特征

1. **尾部带「来源」链接标签**：`…计划于 2027 年全球推广。来源` —— 是原页面的链接文字，`text` 里保留了下来
2. **首句不能按 `：` 切**：`漫威影业 10 月 12 日宣布《复仇者联盟 5：毁灭之日》内地官宣定档…` —— 会在书名号内断开；只能按 `。`
3. **条目之间没有空行**，只有单个 `\n`；所以「按段落切」会得到 1 段，必须按 `\n` 切

### 1.8d 简讯作为整体进 2c 时的可见性缺口（必须处理）

`ai_stage.slim_classify_item` **不含 `body_text`**，且 `summary` 截到 400 字：

| | 简讯节 |
|---|---|
| `body_text` | 711 字（116033 约 1400 字） |
| 2c 的 `summary` 截断 | **400 字** |
| 结果 | 只能看到约 **56%**，第 2 条之后对分类不可见 |

→ 简讯整体进 2c 时，**它的 `summary` 必须放宽**（见 §3.3），否则"整节进 AI"名不副实。

### 1.9 少数派面板的 rail 现在是「一层父级 + 一层子项」

实测当前渲染出的 rail HTML：

```
┌─ rail-g（父级组）
├─ 派早报 (11)  → #sp-brief
│    Apple 发布 HomePod mini 2、HomeView 等新品   → #sp-f1-1
│    Apple Watch 在中国大陆推出睡眠呼吸暂停迹象提示功能 → #sp-f1-2
│    …
│    不妨一看的简讯                              → #sp-f1-9
│    少数派的近期动态                            → #sp-f1-10
│    你可能错过的文章                            → #sp-f1-11
├─ 近期文章 (19)  → #sp-others
```

`rail()` 的签名是 `(key, label, count)` 或 `(key, label, count, children)`，**只有一层 children（`.rail-sub`），没有递归嵌套**。

→ 若窗口里出现 2 期派早报，就无法呈现「派早报 > 期 > 节」两级。但按 §1.6b，20 条窗口通常只有 1 期，所以**本轮不需要改 `rail()`**；且已确认只抓一期，所以这个顾虑不成立（见 §10.3）。

### 1.9b 现有 rail 的子项里混着推广节

当前 rail 的 11 个子项含 `少数派的近期动态`、`你可能错过的文章`（推广节）。拆块过滤后这两项自然消失，rail 变干净。

### 1.10 Telegram 频道标题在同一次请求的 HTML 里

`walk_channel` 已下载 `t.me/s/<channel>`，标题就在其中：

```python
re.search(r'<div class="tgme_channel_info_header_title"[^>]*>(?:<span[^>]*>)?([^<]+)', html)
```

实测 18 个频道（节选）：

| 频道 | 标题 | 频道 | 标题 |
|---|---|---|---|
| `zaihuapd` | 在花 | `reorx_share` | Reorx's Forge |
| `tnews365` | 竹新社 | `douView` | douView × 银幕 |
| `wxbyg` | 微信搬运工 | `hacker_news_zh` | Hacker News 摘要 |
| `zhihu_bazaar` | 知乎大巴扎 | `NewlearnerChannel` | Newlearnerの自留地 |
| `Vwyxrd` | 追尾游戏热点 | `gledos_microblogging` | gledos Lia green 的微型博客 |
| `CNSmydream` | 来一点医学科学前沿 | `notonlyshare` | 啰哩啰嗦分享频道 |

⚠️ **4 个标题是英文/中英混合**（`reorx_share`、`douView`、`hacker_news_zh`、`NewlearnerChannel`）。已确认**不加覆盖表**，直接用抓到的标题。

### 1.11 现有渲染层对 sections 的依赖只在一处

全项目只有 `render_sspai_panel`（[render_digest.py:1080](/home/moorey/Projects/Newsletter/NewsYou/tools/render_digest.py#L1080)）读 `sections`。拆块影响局限在这一个函数。

### 1.12 一个既存不一致（拆块会顺带修掉）

实测当前渲染：**派早报专区只有 1 期**，另 2 期落到「近期文章」当普通文章：

```
派早报专区(feat):  派早报：Apple 发布 HomePod mini 2…     ← 只有抓了正文的这期
近期文章(ar):      …共 19 行，含：
                     派早报：一加发布一加 16，Google 推出 Fitbit Edge 等
                     派早报：工信部等就进一步加强汽车产品创新设计管理征求意见
```

原因：判定条件含 `(sections or body_text)`，而 `fulltext="today"` 只让当天那期抓到正文。

### 1.13 需求 5 的"分类部分"已经实现

`ai_stage.classify_groups` 只建 `news`/`discussion` 两组，`longform` 不在其中 → 长文**已经**不进分类。本轮只需把源归位。

### 1.14 头条现状（要改的对象）

```
今日汇总 tab
├ rail: 头条 / 要点 / 分类 / 讨论
├ 头条: 标题 + 摘要 + topics + why + 重要度 + 引用 chip
├ 要点: 最多 9 条（rank + 标题 + 摘要 + why + chip + importance）
├ 分类 / 讨论
└ 备注: stats_note + 模型名
```

相关代码点：

| 位置 | 作用 |
|---|---|
| `ai_stage.py:263` | response_schema 的 `headline` / `highlights` |
| `ai_stage.py:478` | `validate_digest_decisions`（校验 id 存在、排序、截断 10 条） |
| `ai_stage.py:669` | `apply_digest_decisions`（id → ref 解析） |
| `render_digest.py:649-688` | 头条卡 + 要点列表渲染 |
| `render_digest.py:1213` | `_digest_count`（tab 上的数字） |
| `render_digest.py:1247` | 面板存在条件 `digest.get("cards")` |
| `ai_apply.py:172,183` | `remove_leftovers` 认 `cards` |
| `archive.py:131` | 归档统计 `highlights` |
| `prompts/digest_highlights.md` | 2b 提示词 |

---

## 2. 头条机制重构

### 2.1 目标形态

```
今日汇总 tab
├ rail: 汇总 / 分类 / 讨论          ← 顶层 4 项变 3 项
├ 汇总: 一段覆盖全天消息的长句 + 关键字列表
├ 分类 / 讨论
└ 备注: stats_note + 模型名
```

「要点」整块删除（`dg-items` 段、`highlights` 校验与应用、`rank`/`importance`/`why`/`topics`）。

### 2.2 输出 schema

```json
{
  "schema": "newsletter.digest-decisions/2",
  "day": "2026-10-14",
  "model": "pi:deepseek-v4.1-flash",
  "summary": "一段覆盖当天全部消息面貌的长句，可以多句。",
  "keywords": [
    {"text": "Gemini 4", "weight": 95},
    {"text": "苹果发布会", "weight": 88},
    {"text": "存储涨价", "weight": 70}
  ],
  "source_ids": ["telegram:zaihuapd:44363", "sspai:6b12245f5d4224b0:3"],
  "stats_note": "一句话"
}
```

| 字段 | 说明 |
|---|---|
| `summary` | 覆盖全天面貌，**不逐条罗列**（已确认） |
| `keywords` | 纯文本 + 权重；`weight` 0–100 决定字号（已确认） |
| `source_ids` | **审计用，不显示**（已确认）；仅校验 id 存在 |
| `headline` | 删除，并入 `summary` |
| `highlights` | 删除 |

### 2.3 审计字段 `source_ids`

已确认保留。它的作用与限度要说清：

| 能做 | 不能做 |
|---|---|
| 校验模型声称依据的条目**确实存在** | 验证 summary 里每句话与这些条目**对应** |
| 发现模型凭空引用不存在的 id | 发现模型把 A 条目的内容写成 B 的事实 |
| 在 `report` 里记录覆盖率（"汇总引用了 12/214 条"） | 阻止模型在真实 id 之外编造细节 |

**所以它降低但不消除幻觉风险。** 校验规则：

| 情况 | 处理 |
|---|---|
| id 不存在 | 拒绝该 id 并计数（不因此丢弃整个 summary） |
| `source_ids` 为空或缺失 | 保留 summary，但在 report 里标记 `audited: false` |
| 全部 id 非法 | 保留 summary，report 里标记 |

**不因审计失败而丢弃 summary**：它是当天的唯一全局视角，丢了面板就空了。审计结果进 `report` 与 `index.json` 的 `stage2`。

### 2.4 关键字列表

| 项 | 决定 |
|---|---|
| 形态 | **纯文本标签**，不可点击（已确认） |
| 权重 | 模型给 `weight` 0–100（已确认） |
| 字号映射 | `font-size: clamp(11px, 11px + weight×0.06px, 17px)` —— weight 0 → 11px，100 → 17px |
| 数量 | 模型自定，脚本上限 **20**（超出截断并计数） |
| 排序 | 按 `weight` 降序 |
| 样式 | 新建 `.dg-kw` / `.dg-kw span`，不复用 `tp-c`（后者是 topics 的等宽小标签） |
| 校验 | `text` 非空、≤24 字；`weight` 夹到 0–100；重复 `text` 去重 |

关键字来源是**模型自由生成**（少数派 API 的 `keywords` 只覆盖少数条目，不足以作统一来源）。

### 2.5 长文不排除（已确认）

2b 的输入**继续包含 `longform`**。`build_digest_input` 不筛 nature，无需改动。

提示词要写清：长文可以出现在汇总里，但汇总覆盖全天面貌，不是只挑最重要的几条。

### 2.5b 汇总的长度上限

`summary` 上限 **800 字**（现在是 `headline` 400 字）。超长截断并计数，不拒绝。

### 2.6 连带改动

| 位置 | 改动 |
|---|---|
| `ai_stage.py` | `DIGEST_SCHEMA` 升到 `/2`；`build_digest_input` 的 response_schema 换字段；`validate_digest_decisions` 重写（删 highlights 逻辑）；`apply_digest_decisions` 大幅简化 |
| `ai_apply.py` | report 里 `highlights` → `keywords` + `audited`；`remove_leftovers` 的 `is_panel` 判定从 `"cards"` 改为 `"summary"` |
| `render_digest.py` | 删 `dg-items` 段与 `dg-lead` 卡；新增汇总块；`_digest_count` 与面板存在条件改用 `summary` |
| `archive.py` | 统计字段 `highlights` → `keywords` |
| `prompts/` | `digest_highlights.md` 改名 `digest_summary.md`（内容重写：汇总面貌 + 关键字权重 + 审计 id） |
| `prompts/classify.md` | 新增子任务：对 `is_digest` 条目拆解其中的独立消息（仅用于辅助归类，不产出条目） |
| `demo_decisions.py` | 假决策改为输出 summary + keywords（含 weight）+ source_ids |
| `digest.css` | 删 `.dg-lead*` / `.dg-it*` 死样式（若不再用）；新增汇总与关键字样式 |
| `TOPIC_LABEL` | 若不再有 `topics`，这个表和渲染可删（`TOPICS` 本身仍被 2a 的 merge 使用，保留） |
| 测试 | `test_pipeline_fixes.py` 的 `StageTwoRun`（用 headline/highlights）、`test_classification.py` 的 `briefing()` 都要改 |

---

## 3. 少数派采集重构

### 3.1 API 为主，RSS 兜底

**现状**：RSS 先（只 10 条），`len(items) < limit` 恒为真 → API 每天必跑。

**改为**：API 为主；仅当 API 抛 `FetchError` / `JSONDecodeError` 时用 RSS 兜底。

```python
items: list[dict] = []
try:
    items = _from_api(limit)
except (FetchError, json.JSONDecodeError) as exc:
    print(f"  ! sspai api failed, falling back to RSS (no issue split): {exc}",
          file=sys.stderr)
    items = feedmod.feed_items(FEED_URL, SOURCE, NAME, engine=engine, limit=limit)
```

`base.fetch` 重试耗尽后抛 `FetchError`（[base.py#L60](/home/moorey/Projects/Newsletter/NewsYou/tools/collectors/base.py#L60)），`fetch_json` 包装 JSON 错误，两者都捕获。

**兜底时明确不拆块**（已确认）：RSS 没有 `post_type`，无法识别派早报，该次运行退化为整期一条。日志必须写明。

**代价**：API 是未公开端点。降级后 API 挂掉时只剩 RSS 的 10 条（现在是"RSS 10 条 + API 部分"）。

### 3.2 删除 info 端点

删 `INFO_URL` 与 `_fetch_body` 里对它的调用。author / tags 改由**列表调用时**填入：

```python
items.append(base.item(
    SOURCE, NAME, title, url, kind="article",
    published_at=base.iso_cst(article.get("released_at") or article.get("created_at")),
    date_source="api",
    summary=base.strip_tags(article.get("summary") or "")[:900],
    author=(article.get("author") or {}).get("nickname"),
    tags=[t.get("title") for t in (article.get("tags") or []) if isinstance(t, dict)]
         + list(article.get("custom_tags") or []),
    extra={"collector": "api_v1", "views": article.get("views_count"),
           "post_type": article.get("post_type"),
           "keywords": article.get("keywords") or []},
))
```

⚠️ 兜底路径（RSS）没有 `post_type` / `keywords`，author 有（实测 10/10）。

### 3.3 拆块（核心）

#### 时机

`sspai.py collect()` 内，**在 `items[:limit]` 之后**（limit 语义 = "20 篇文章"，已确认不变）。

```
API 列表(20 篇) → 识别派早报(post_type==3) → 只取最新一期 → 抓正文 → split_sections
                                                              ↓
                        推广节丢弃 / 新闻节 → 子条目 / 简讯节 → 整节一条（不拆）
                                                              ↓
                                          子条目替换父条目，返回
```

采集层完成后 `data/collected/sspai.json` 就是子条目，**下游全部自动看到拆块后的数据**。

#### 只抓最新一期（已确认）

**窗口里只取最新一期派早报**，其余期**不进条目**（既不抓全文，也不作为条目出现）。

```python
issues = [e for e in items if e.get("post_type") == 3]
issues.sort(key=lambda e: str(e.get("published_at") or ""), reverse=True)
latest = issues[:1]                       # 只留最新一期
items  = [e for e in items if e.get("post_type") != 3] + latest
```

依据（§1.6b）：`limits.sspai = 20` 的窗口约覆盖 1.2 天，**通常只含当天那一期**；所以"只取一期"在默认配置下不丢东西。若 limit 被调大到 50+，旧期会被静默丢弃——这是有意的，因为日报只需要当天那期。

#### 正文抓取：派早报不受 fulltext 策略约束

```python
others  = [e for e in items if e.get("post_type") != 3]
targets = latest + [e for e in base.fulltext_targets(others, fulltext)]
```

即派早报**无条件抓正文**（每天 1 次请求）。原因：拆块依赖 `sections`，而 `fulltext="today"` 在某些时刻（如凌晨运行、当天那期还没出）可能不命中，导致该期无法拆块、nature 判定不一致。

这是**对全局策略的有意例外**，需在配置注释与 README 写明。

#### 子条目结构

```python
{
    "source": "sspai", "source_name": "少数派", "kind": "article",
    "id": "sspai:6b12245f5d4224b0:3",          # 显式设置，_stamp_identity 会保留
    "title": "Google 发布 Gemini 4 Argon",
    "url": "https://sspai.com/post/116138",     # 与父条目相同
    "published_at": <父条目>, "date_source": <父条目>,
    "author": <父条目>, "tags": <父条目>,
    "summary": <节文本前 1200 字>,
    "body_text": <节 text>, "body_html": <节 html>,
    "sections": [],                              # 子条目不再嵌套
    "extra": {
        "collector": "api_v1",
        "column": "派早报",                      # 单元标记，供 nature 解析
        "issue_id": "sspai:6b12245f5d4224b0",
        "issue_title": "派早报：Apple 发布 HomePod mini 2、HomeView 等新品",
        "issue_url": "https://sspai.com/post/116138",
        "issue_index": 3,                        # 保留节内 1-based 序号
        "issue_total": 9,
        "issue_date": "2026-10-14T08:40:39+08:00",
        "fulltext": True, "body_scope": …, "body_chars": …,
        "extract_engine": "modern",              # _use_rich 依赖它
    },
}
```

#### id 派生：`sspai:<父hash>:<序号>`

- 序号是**保留节内的 1-based 序号**（推广节丢弃后重排）
- `_stamp_identity` 保留预置 id（`item_id` 优先取 `entry["id"]` 再写回同值）
- 选序号而非标题 hash（已确认）：抗标题编辑；实测期内标题唯一但仍可能有重复
- 跨天稳定性不需要：决策文件有 day 闸门

#### 父条目：数据层丢弃，呈现层保留

这里澄清上一版计划里含混的「父条目」一词。它指的是**数据结构里的那条记录**，不是页面上的标题或目录。三个东西要分清：

| 概念 | 拆块后 | 说明 |
|---|---|---|
| **数据记录**（`items[]` 里代表整期的那条） | **丢弃** | 若保留，它会作为一条整体进分类，与子条目重复 |
| **正文标题**（页面上显示"派早报：…"） | **保留** | 子条目带 `issue_title`，渲染时用它做专期标题 |
| **目录父级**（左侧 rail 的「派早报」层级） | **保留** | 子条目带 `issue_id`，渲染时按它分组，rail 仍有父级 |

即：**页面观感不变**（仍有"派早报"这个父级和标题），只是数据层从 1 条变成 N 条，好让分类逐条进行。

**兜底**：派早报抓不到 sections 时（正文失败），保留父条目为单条，仍设 `extra["column"] = "派早报"` → nature 仍为 news。

#### 简讯节：不拆，整节作为一条（已确认）

简讯节（`不妨一看的简讯` / `看看就行的简讯` / `看看就行的其他消息` / `看看就行的小道消息`）**作为一个整体成为一条子条目**：

```python
{
    "id": "sspai:6b12245f5d4224b0:9",
    "title": "不妨一看的简讯",
    "summary": <整节 text，1200 字以内>,
    "body_text": <整节 text>,
    "body_html": <整节 html>,      # 保留 <ul>/<li> 结构，渲染时是列表
    "extra": {"column": "派早报", "issue_index": 9, "is_digest": True, ...},
}
```

- `extra["column"] = "派早报"` → nature = `news` → **进 2c 分类**（与其他新闻节一致）
- `extra["is_digest"] = True` 供渲染与提示词识别
- 不再脚本级按 `<li>` 拆分（虽然 §1.8b 证明可行）

**理由**：脚本拆会产出「功能方面，据爆料，该设备将整合摄像头…」这类脱离上下文的条目；交给 AI 拆能正确归并（§1.8b 的嵌套结构证明归属关系是明确的）。

**代价**：简讯在分类里是 1 条，而不是 2–8 条；`extra.is_digest` 标记让 AI 知道"这条里有多个话题"。

#### AI 子任务：让模型拆解简讯（可行性评估）

在 `prompts/classify.md` 里增加一个子任务：对 `is_digest` 条目，模型额外输出它识别出的独立消息。

**可行性评估**：

| 维度 | 评估 |
|---|---|
| 输入完整性 | ✅ `text` 的换行与顶层 `<li>` 精确对应（6/6），AI 能看到"有 4 条" |
| 归属判断 | ✅ 嵌套关系在原页面已编码（§1.8b），AI 读文本也能判断"功能方面…"属于上文 |
| 输出锚定 | ⚠️ 拆出的消息**没有 item_id**（它们不是独立条目），无法走"每条断言锚定 id"的校验 |
| 用途 | 两个可能：① 辅助分类判断（不影响分类结果结构）② 在页面上展开显示 |
| 风险 | 若用途是 ②，就需要一个"无 id 的展示块"，与现有"每条都有锚点"的约定冲突 |

**建议**：本轮让 AI 只做 **① 辅助分类**——即模型拆解简讯是为了**更准确地把这条简讯归入类目**，而不是把拆出的消息单独展示。这样：

- 不引入无锚点的展示块
- 不需要新的校验逻辑
- 收益是分类质量（模型理解"这条简讯含 4 个话题，主要属于 AI/数码"）

若你希望拆出的消息在页面上单独显示，则需要在 §10 增加一项设计（无 id 展示块的锚点策略）。

#### 简讯的 summary 必须放宽（否则"整节进 AI"名不副实）

`slim_classify_item` 现在把 `summary` 截到 **400 字**（`CLASSIFY_SUMMARY_LIMIT`），而简讯整节有 **640–1400 字**：

| | 简讯节 |
|---|---|
| `body_text` | 640–1400 字 |
| 2c 的 `summary` 截断 | 400 字 |
| 结果 | 只能看到约 **30–56%**，后面的条目对分类不可见 |

**改为**：`slim_classify_item` 对 `extra["is_digest"]` 的条目放宽到 **1600 字**（覆盖实测最长 1400 字）。

```python
limit = DIGEST_SUMMARY_LIMIT if (entry.get("extra") or {}).get("is_digest") \
        else CLASSIFY_SUMMARY_LIMIT
"summary": (entry.get("summary") or "")[:limit],
```

这是 2c 输入体积的**局部增量**：每天最多 1 条简讯 × 1200 字 ≈ 2.4 KB，可忽略。

⚠️ 同样的放宽**也要应用到 2b**（`slim_item` 的 summary 上限 4000 字，已够，无需改）。

#### 请求数

| | 现状 | 改后 |
|---|---|---|
| RSS | 1 | 0（仅兜底） |
| API 列表 | 1 | 1 |
| info | 1/期 | **0** |
| 正文页 | 1/期（仅当天） | 1（最新一期） |
| **合计** | **≈4** | **≈2–3** |

### 3.4 周末停稿的回退

**实测：21 期派早报里周六周日各 0 期**（§1.6）。所以周末运行时会取不到派早报。

#### 判定

派早报缺失有两种原因，要分开：

| 情况 | 判定 | 处理 |
|---|---|---|
| 周末/假期停稿 | 当天是周六/周日，或窗口内最新一期早于今天 | 显示「今日停稿」 |
| 采集异常 | 窗口里有 `post_type==3` 但正文抓取失败 | 显示父条目（退化为整期一条） |
| API 兜底模式 | RSS 路径无 `post_type` | 不显示停稿，正常走整期 |

#### 呈现

少数派面板的「派早报」段显示：

```html
<section class="sec" id="sp-brief">
  <h2 class="sec-h">派早报<span class="aside">今日停稿</span></h2>
  <p class="empty">周末与法定假期不更新，下一期在下一个工作日 06:00–09:00 发布。</p>
</section>
```

要点：
- 判定依据用**当天是否周末**（`datetime.weekday() >= 5`），比"窗口里没有派早报"更可靠——后者在凌晨运行、当天那期还没出时也会为真，那时应该说"今日尚未发布"而非"停稿"
- 需要在 `data/processed/sspai.json` 或 index 里传递一个标记，供渲染层判断。建议放在 `status` 里：`status["brief_state"] = "published" | "suspended" | "pending" | "unknown"`

| `brief_state` | 含义 | 页面文案 |
|---|---|---|
| `published` | 取到了当天那期 | 正常显示 |
| `suspended` | 周六/周日（或已知假期） | 今日停稿 |
| `pending` | 工作日但当天那期还没出（当前时刻早于 09:00 且无当天期） | 今日尚未发布 |
| `unknown` | 兜底模式（RSS）或无 `post_type` | 不显示特殊文案 |

---

## 4. nature 归位

### 4.1 目标

| 源 | nature | 进 2c/2d | 进 2b |
|---|---|---|---|
| `sspai` 派早报（子条目） | `news` | ✅ | ✅ |
| `sspai` 其他文章 | `longform` | ❌ | ✅ |
| `latepost` 晚点 | `longform` | ❌ | ✅ |
| `geekpark` 极客公园 | `longform` | ❌ | ✅ |
| `ruanyifeng` / `anywayfm` | `longform` | ❌ | ✅ |
| `linuxdo` / `hackernews` | `discussion` | ✅ 2d | ✅ |
| telegram 新闻频道 / `weibo` / `zhihu` / `solidot` | `news` | ✅ 2c | ✅ |

### 4.2 配置

```toml
[sources.nature]
discussion = ["linuxdo", "hackernews"]
longform   = ["sspai", "latepost", "geekpark", "ruanyifeng", "anywayfm"]

[sources.units]
"sspai:派早报" = "news"
# …Telegram 与 linux.do 既有条目不变
```

⚠️ **`sources.nature.*` 是列表替换语义**（不是合并）。必须把 `ruanyifeng`/`anywayfm` 一起写全，否则它们**静默掉回 `news`**（上一轮已实测）。配置注释必须写明。

### 4.3 单元字段

```python
UNIT_FIELD = {"telegram": "channel", "linuxdo": "section_name", "sspai": "column"}
```

- 派早报条目（子条目，或抓正文失败时的父条目）设 `extra["column"] = "派早报"` → 命中 `sspai:派早报` → `news`
- 其他少数派文章**不设** `column` → 单元为 `None` → 回落源级 `longform`

### 4.4 单元名警告要排除派生单元

`_stamp_identity` 末尾的 unmatched 警告（[collect.py#L112](/home/moorey/Projects/Newsletter/NewsYou/tools/collect.py#L112)）会误报：某天窗口里没有派早报时 `sspai:派早报` 一个都没匹配上 → 打印警告。派早报的单元是从数据派生的，不存在"配置写错"。处理：`sspai` 不参与该检查。

### 4.5 收敛重复判定

"什么算派早报"现在有三处独立实现，本轮统一为采集层写入的 `extra["column"]`：

| 位置 | 现用规则 | 改为 |
|---|---|---|
| `sspai._fulltext_eligible` | `"派早报" in title` | `post_type == 3` |
| `render_digest.py:1086` | `"派早报" in title and (sections or body_text)` | `extra["issue_id"]` |
| （新增）采集层识别 | — | `post_type == 3` |

---

## 5. Telegram 频道中文名

### 5.1 取名字段

从**第一页** HTML 提取（零额外请求）：

```python
TITLE = re.compile(r'<div class="tgme_channel_info_header_title"[^>]*>(?:<span[^>]*>)?([^<]+)')
```

存入每条目的 `extra["channel_title"]`，并放进 `last_report()` 的每频道条目。

### 5.2 显示位置（4 处）

| 位置 | 现状 | 改为 |
|---|---|---|
| `_tg_card` 卡片头 | `@zaihuapd` | 在花 |
| Telegram rail 频道筛选 | `@zaihuapd` | 在花 |
| `_ref_name` 引用 chip | `@zaihuapd` | 在花 |
| `_channel_note` 失败/空频道备注 | `@zaihuapd` | 在花 |

⚠️ rail 的 `data-filter` 属性**继续用原始频道 id**（筛选按 id 匹配），只改显示文本。

⚠️ chip 文本来自 `categories.json` 的 ref，ref 由 `apply_classify_decisions` 从条目构造 → **ref 必须带 `channel_title`**，否则 chip 仍显示 id。

### 5.3 回退链

```
extra.channel_title  →  @<channel id>
```

取不到标题时退回现状，不显示空白。

### 5.4 不加覆盖表（已确认）

英文标题（`Reorx's Forge` 等）直接用抓到的原文。

---

## 6. 数据契约变更

`data/collected/sspai.json`：

| 变更 | 说明 |
|---|---|
| 派早报从 1 条变 N 条 | N = 新闻节数 + 1（简讯整节一条） |
| 只出现**最新一期** | 其余期不进条目（§3.3） |
| 新增 `extra.column` | `"派早报"`；普通文章无 |
| 新增 `extra.issue_*` | `issue_id` / `issue_title` / `issue_url` / `issue_index` / `issue_total` / `issue_date` |
| 新增 `extra.is_digest` | 简讯节为 `true` |
| 新增 `extra.post_type` / `extra.keywords` | 列表端点的结构化字段 |
| 新增 `status.brief_state` | `published` / `suspended` / `pending` / `unknown` |
| `count` 变大 | 例：20 → 20 − 1 + 9 ≈ 28（一期 8 新闻节 + 1 简讯） |

`data/collected/telegram.json`：每条新增 `extra.channel_title`。

`data/ai/digest_decisions.json` 与 `data/processed/digest.json`：schema 升到 `/2`。

```json
{
  "schema": "newsletter.digest-decisions/2",
  "day": "2026-10-14",
  "model": "pi:…",
  "summary": "…",
  "keywords": [{"text": "…", "weight": 95}],
  "source_ids": ["…"],
  "stats_note": "…"
}
```

其他源契约不变。`nature` / `id` / `category` 沿用第一轮。

---

## 7. 渲染层改动

### 7.1 汇总块替换头条 + 要点

```html
<section class="sec" id="dg-summary">
  <h2 class="sec-h">汇总</h2>
  <p class="dg-hl">…覆盖全天面貌的长句…</p>
  <div class="dg-kw">
    <span style="--w:95">Gemini 4</span>
    <span style="--w:88">苹果发布会</span>
    <span style="--w:70">存储涨价</span>
  </div>
</section>
```

- `dg-lead`（头条卡）与 `dg-items`（要点列表）两段删除
- `dg-hl` 样式保留复用（原本就是导语样式）
- `.dg-kw span` 字号由 `--w` 决定：`font-size: clamp(11px, calc(11px + var(--w) * 0.06px), 17px)`

### 7.2 rail 顶层从 4 项变 3 项

```
汇总 (关键字数)  → #dg-summary
分类 (121)       → #dg-cats
讨论 (88)        → #dg-disc
```

### 7.3 少数派面板按 `issue_id` 分组

```python
children = [e for e in items if (e.get("extra") or {}).get("issue_id")]
others   = [e for e in items if not (e.get("extra") or {}).get("issue_id")]
issues   = group_by(children, key=lambda e: e["extra"]["issue_id"],
                    order=lambda e: e["extra"]["issue_index"])
```

### 7.4 每个节渲染为带锚点的块

```html
<section class="sec blk rv" id="it-sspai:6b12245f5d4224b0:3">
  <h4 class="blk-h"><span class="no">03</span>Google 发布 Gemini 4 Argon</h4>
  <div class="prose">…</div>
</section>
```

- 锚点从 `sp-f{fi}-{si}` 改为 `it-<子条目 id>`，与引用 chip 的 `data-jump` 对齐
- 专期头（`feat-h`）不再有 `it-` 锚点（不对应任何条目），只作分组标题，显示 `issue_title`
- 简讯节（`is_digest`）的 `body_html` 是 `<ul>/<li>`，走 `body_fragment` 的富文本路径（`_use_rich` 依赖 `extra.extract_engine`，子条目要带上）

### 7.5 停稿 / 未发布回退

| `brief_state` | 专区显示 |
|---|---|
| `published` | 正常的期头 + 各节 |
| `suspended` | `派早报` + `今日停稿` + 一句说明 |
| `pending` | `派早报` + `今日尚未发布` |
| `unknown` | 不显示专区（走「近期文章」，即兜底模式） |

### 7.6 rail 不再需要多期处理

只抓一期后，`rail()` 的两级结构（`派早报` > 各节）足够，**不改 `rail()`**（§10.3）。

### 7.7 顺带修掉的不一致

- 所有派早报都以拆块形式呈现，不再出现 §1.12 的"抓了正文的进专区、没抓的进近期文章"
- rail 子项不再包含推广节（`少数派的近期动态`、`你可能错过的文章`）

---

## 8. 改动清单

| # | 文件 | 改动 | 估量 |
|---|---|---|---|
| 1 | `tools/collectors/sspai.py` | API 为主 / RSS 兜底（兜底不拆块）；删 `INFO_URL` 与调用；author/tags 由列表填入；`_from_api`、`_split_issue`、推广/简讯正则、**只取最新一期**、`brief_state` 判定 | ~140 行 |
| 2 | `tools/collectors/sspai.py` | 派早报无条件抓正文；简讯整节一条（`is_digest`） | ~20 行 |
| 3 | `tools/collectors/registry.py` | `UNIT_FIELD` 加 `sspai: "column"` | 1 行 |
| 4 | `tools/collect.py` | unmatched 警告排除派生单元（sspai） | ~5 行 |
| 5 | `config/config.toml` | `longform` 加 `sspai`/`latepost`/`geekpark`；`sources.units` 加 `"sspai:派早报"`；注明替换语义 | ~8 行 |
| 6 | `tools/collectors/telegram.py` | 提取频道标题 → `extra["channel_title"]` + `last_report` 每频道带 `title` | ~15 行 |
| 7 | `tools/ai_stage.py` | `DIGEST_SCHEMA` 升 `/2`；response_schema 换 `summary`/`keywords`/`source_ids`；重写 `validate_digest_decisions`（含关键字与审计校验）；简化 `apply_digest_decisions`；ref 加 `channel_title`；`slim_classify_item` 的 summary 对 `is_digest` 放宽到 1600 字 | ~110 行（净减） |
| 8 | `tools/ai_apply.py` | report 字段（`keywords` / `audited`）；`remove_leftovers` 的 `is_panel` 改用 `summary` | ~12 行 |
| 9 | `tools/render_digest.py` | 汇总块替换头条+要点；删 `dg-items`/`dg-lead`；`_digest_count` 与面板条件改用 `summary`；**停稿/未发布回退文案**；少数派按 `issue_id` 分组；4 处频道名；删 `TOPIC_LABEL` 与 `topics()` | ~140 行 |
| 10 | `tools/digest_assets/digest.css` | 删 `.dg-lead*`/`.dg-it*`/`.dg-th*`/`.th*` 死样式；新增 `.dg-kw`（带权重的关键字标签） | ~35 行 |
| 11 | `prompts/digest_summary.md`（新）、删 `digest_highlights.md` | 重写：全天面貌 + 关键字权重 + 审计 id | — |
| 12 | `prompts/classify.md` | 新增子任务：`is_digest` 条目的内部拆解（仅辅助归类） | — |
| 13 | `tools/demo_decisions.py` | 假决策改为 summary + keywords(weight) + source_ids | ~25 行 |
| 14 | `tools/archive.py` | 统计字段 `highlights` → `keywords` | ~3 行 |
| 15 | `tests/` | 拆块 / 只取一期 / 推广过滤 / 简讯整节 / id / nature 归位 / 停稿状态 / 频道名 / 汇总 schema 与审计 / 关键字权重 | ~250 行 |
| 16 | `README.md` | 端点、拆块、nature 表、fulltext 例外、头条机制、停稿说明 | ~60 行 |

---

## 9. 实施顺序

| 批 | 内容 | 验收 |
|---|---|---|
| 1 | 清单 #6（Telegram 频道名，独立无依赖） | 卡片头 / rail / chip / 备注显示中文名；筛选仍按 id 生效 |
| 2 | 清单 #1–#2（API 为主、删 info、拆块、只取一期、停稿状态） | `collected/sspai.json` 子条目 id 唯一；推广节已丢；简讯是 1 条且带 `is_digest`；只出现 1 期；`brief_state` 正确；RSS 兜底可人为触发且不拆块 |
| 3 | 清单 #3–#5（nature 归位） | 派早报子条目 = news；其他少数派 + 晚点 + 极客公园 = longform；分类只收 news/discussion |
| 4 | 清单 #7–#10（头条机制 + 渲染层） | 汇总 + 带权关键字显示正确；要点消失；停稿文案出现；少数派按期分组且每节有 `it-` 锚点；chip 可跳转 |
| 5 | 清单 #11–#16（提示词、演示、归档、测试、文档） | 测试通过；README 更新 |

**批次 2 与 4 风险最高**（改采集核心路径与 2b 契约），完成后各跑一次真实 `make collect` / `make daily` 核对。

批次 2 验收时要**特意在周末语境下核对** `brief_state`：可以临时把系统时间假设为周六（或直接单测判定函数），确认显示「今日停稿」而不是空白或报错。

---

## 10. 决策记录

上一版计划的 8 项待确认已全部定案，另附 5 项由我按"其他小问题自己决定"处理。

### 10.1 已定案（你的决定）

| # | 事项 | 决定 | 影响 |
|---|---|---|---|
| 1 | 简讯是否脚本拆 | **不拆**，整节一条进 AI；提示词加子任务让 AI 拆解 | §3.3。⚠️ 我的建议是 AI 拆解**只用于辅助归类**，不产出条目——若你要拆出的消息也单独显示，需另设计无 id 展示块 |
| 2 | 汇总是否保留审计字段 | **保留** `source_ids`（不显示） | §2.3。降低但不消除幻觉风险 |
| 3 | 关键字形态 | **纯文本**，字号按 AI 给的权重变化 | §2.4。新增 `.dg-kw` 样式 |
| 4 | 多期派早报 | **只抓一期**（当天那期），其余期不抓全文、不进条目 | §3.3。20 条窗口通常只含 1 期，不丢东西 |
| 5 | rail 层级 | 见 §10.3（已澄清） | 本轮不需要改 `rail()` |
| 6 | 派早报停稿 | 周末停稿，抓不到时显示「今日停稿」 | §3.4。四种 `brief_state` |
| 7 | 汇总覆盖范围 | **全天面貌，不逐条罗列** | §2.5b。上限 800 字 |
| 8 | 其他小项 | 由我决定，见 §10.2 | — |

### 10.2 我决定的小项

| # | 事项 | 决定 | 理由 |
|---|---|---|---|
| 1 | 简讯条目是否算 `extra.column = "派早报"` | **算** | 否则会回落源级 `longform`，简讯里的新闻就进不了分类，与需求 4 矛盾 |
| 2 | 简讯的 `summary` 上限 | 对 `is_digest` 放宽到 **1600 字**（其他仍是 400） | 简讯整节 640–1400 字，400 字只能看到 30–56%，"整节进 AI"名不副实 |
| 3 | 未知 `post_type` | 按**普通文章**处理，不报错 | 防御性：站点加新类型时不应导致整源失败；若该值实际是派早报，退化为整期一条而非丢失 |
| 4 | 简讯尾部「来源」标签 | **保留** | 它是原页面的链接文字，剥离会丢信息；AI 与读者都能理解 |
| 5 | 简讯条目的 `published_at` | 用**派早报的发布日期** | 简讯条目本身没有独立日期；用父期日期与"当天要闻"语义一致 |
| 6 | 抓正文失败时保留的父条目 | 归入「近期文章」段，并在专区显示一条说明 | 专区显示"本期正文未取到"，避免读者以为当期无内容 |
| 7 | 旧 `digest_decisions.json`（schema `/1`） | 按 **schema 拒绝**并提示重新生成 | day 闸门只拦跨天，同一天换 schema 会被误用；显式拒绝更安全 |
| 8 | `TOPIC_LABEL` 与 `topics()` | 删除 | 汇总机制下不再有 `topics`；`TOPICS` 常量保留（2a 的 merge 仍在用） |
| 9 | `.dg-th` / `.th*` 死样式 | 一并删除 | 第一轮删 themes 时留下的死 CSS |
| 10 | 汇总的 `summary` 上限 | **800 字**，超长截断并计数 | 现在是 headline 400 字；全天面貌需要更多空间 |

### 10.3 rail 层级：上一版那句话的意思（已澄清）

上一版我写的「rail 只支持一层 children，无法呈现「派早报 > 期 > 节」两级」是在担心这个：

`rail()` 的签名是 `(key, label, count)` 或 `(key, label, count, children)`，其中 `children` 渲染成 `.rail-sub`（缩进一层）。**没有递归**，所以「父 > 子 > 孙」三级表达不出来。

当时的顾虑是：如果窗口里有 3 期派早报，理想的目录是

```
派早报
├─ 10-14 期
│   ├─ Apple 发布 HomePod mini 2…
│   └─ Google 发布 Gemini 4 Argon
├─ 10-13 期
│   └─ 一加发布一加 16
```

这需要三级。但**按你的决定只抓一期，这个问题就不存在了**——永远只有一期，目录就是现在的两级：

```
派早报 (9)
├─ Apple 发布 HomePod mini 2…  → #it-sspai:6b12…:1
├─ Google 发布 Gemini 4 Argon  → #it-sspai:6b12…:3
└─ 不妨一看的简讯              → #it-sspai:6b12…:9
近期文章 (17)
```

**所以本轮不改 `rail()`**，只把子项的目标从 `#sp-f1-3` 换成 `#it-<子条目 id>`，并过滤掉推广节。

### 10.4 仍然未验证的（需真实运行）

见 §11.1。

## 11. 未验证与取舍

### 11.1 未验证（需真实运行确认）

- 推广节 / 简讯节正则对未来新变体的覆盖（实测 6 期 100% 命中，样本小）
- `post_type` 除 1/3 外是否还有其他值
- RSS 兜底路径能否被真实触发（需 API 真的挂掉）
- 拆块后 2c 分类的类目数与上限 12 的冲突频率
- 频道标题依赖 CSS 类名 `tgme_channel_info_header_title`，站点改版会失效
- **汇总 + 关键字机制下模型输出的稳定性**：`summary` 是自由文本，`source_ids` 只能校验 id 存在，无法验证句子与来源的对应
- **关键字权重的分布质量**：模型可能给所有关键字相近的权重（如全 80），使字号差异不可见
- 拆块后 2b 输入体积变化（长文保留，条目数增加）
- `brief_state` 的判定边界：凌晨（当天那期未出）与周末的区分是否准确
- 简讯的 `summary` 放宽到 1600 字后，2c 输入体积与模型表现的平衡

### 11.2 取舍

| 取舍 | 原因 |
|---|---|
| 删 info 端点 | 实测 API 列表是其严格超集（tags 还更多），零损失 |
| RSS 降级为兜底 | 职责清晰、摘要质量更好；代价是 API 挂掉时只剩 10 条，且不拆块 |
| 派早报无条件抓正文 | 拆块依赖 sections；每天 1–3 次请求，是对全局 fulltext 策略的有意例外 |
| 数据层丢弃父条目、呈现层保留父级与标题 | 避免分类重复，同时保持页面观感 |
| id 用序号 | 抗标题编辑与重名；代价是插入节会让后续序号位移 |
| 头条改汇总 + 关键字 | 更简洁的全局视角；**代价是失去"每条断言锚定到 id"的校验保证**，`source_ids` 只部分补偿 |
| 简讯整节一条、由 AI 拆解 | 避免脚本拆产生脱离上下文的碎片；代价是分类里简讯只占 1 条，且 AI 拆解结果无 id 锚点、不产出条目 |
| 只抓一期派早报 | 日报只需当天那期；代价是 limit 调大到 50+ 时旧期被静默丢弃 |
| 关键字纯文本不可点击 | 保持简单；代价是关键字无法作为导航入口 |
| 未知 `post_type` 按普通文章处理 | 站点加新类型时不致整源失败；代价是若新类型是派早报，会退化为整期一条 |
| 频道名抓取、不加覆盖表 | 零维护；代价是 4 个频道显示英文/中英混合 |
| 长文保留在 2b | 长文与"今日要闻"不冲突；分类才是需要只看新闻/讨论的地方 |

---

## 12. 相关文件

```
PLAN-2.md                        本文档
PLAN.md                          第一轮计划（已实施）
config/config.toml               nature / units
tools/collectors/sspai.py        API 为主、删 info、拆块、只取一期、停稿状态
tools/collectors/telegram.py     频道标题提取
tools/collectors/registry.py     UNIT_FIELD 加 sspai
tools/collect.py                 unmatched 警告排除派生单元
tools/ai_stage.py                2b schema 升版、校验重写、简讯 summary 放宽
tools/ai_apply.py                report 字段、remove_leftovers
tools/render_digest.py           汇总块、停稿回退、少数派分组、频道名
tools/digest_assets/digest.css   死样式清理、带权关键字样式
prompts/digest_summary.md        阶段 2b 提示词（新）
prompts/classify.md              2c/2d 提示词（加简讯拆解子任务）
tools/demo_decisions.py          假决策适配
tools/archive.py                 统计字段
tests/                           拆块、只取一期、简讯、nature、停稿、频道名、汇总
README.md                        端点、拆块、nature、头条机制、停稿说明
```

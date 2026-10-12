# Collector reference

`tools/collect.py` implements seven source adapters. All are standard-library only, use no search tools, and are reproducible from a single command.

```bash
python tools/collect.py --list-sources
python tools/collect.py --all --fulltext --outdir data/collected
python tools/collect.py <source> --limit N --list
```

Common behaviour:

- retries with backoff on transient failures;
- one failing source never aborts the run (`index.json` records the error);
- canonical-key deduplication that collapses `http`/`https`, `www`, and tracking parameters, while preserving meaningful query strings;
- every item keeps its original URL for citation.

---

## 1. `ruanyifeng` — 阮一峰的网络日志

| | |
|---|---|
| Primary | `https://www.ruanyifeng.com/blog/atom.xml` |
| Fallback | `https://www.ruanyifeng.com/blog/archives.html` |
| Format | Atom XML (3 entries), then HTML anchor scrape (20 posts) |

The Atom feed only carries the newest three entries, so the archive page is used to reach ten or more. Archive links are deduplicated against feed links via canonical keys, which matters because the feed emits `http://` URLs while the archive emits `https://`. Archive entries lack exact publish times, so the month is used (`2026-08-01T00:00:00+08:00`) and marked as approximate by the collector name `archives_html`.

Parsed fields: `title`, `url`, `published_at`, `summary`, `collector`.

---

## 2. `anywayfm` — Anyway.Now 安妮薇看看

| | |
|---|---|
| Source | `https://anyway.fm/now/` |
| Format | HTML anchor scrape |

Each link is rendered as `<a href="links/<id>">title</a>`. The canonical link is `https://anyway.fm/links/<id>` (note: `/now/links/<id>` returns 404). Entries are ordered newest-first, so `position` records rank.

Parsed fields: `title`, `url`, `position`, `collector`.

---

## 3. `sspai` — 少数派

| | |
|---|---|
| Primary | `https://sspai.com/feed` (official RSS) |
| Secondary | `https://sspai.com/api/v1/articles?limit=50&offset=0` |
| Full text | `https://sspai.com/post/<id>` (HTML) |

The official feed is the cleanest source and is already date-stamped. The JSON API widens the search window when the feed's ten items do not contain enough 派早报 issues.

### Full-text extraction

The API's info endpoint (`/api/v1/article/info/get?id=<id>`) returns metadata but an **empty `body`**, so text must come from the article HTML. Each news item inside a 派早报 issue is a `post__body__extend__item` block:

```html
<div class="post__body__extend__item">
  <h2 class="post__body__extend__item__title">三季度全球 PC 出货量骤降</h2>
  <div class="post__body__extend__item__content wangEditor-txt">
    <p>据 The Resigter 报道…</p>
  </div>
</div>
```

Because the content div can contain nested elements, extraction uses a **balanced `<div>` scanner** rather than a non-greedy regex, which would truncate at the first `</div>`.

Enable with `--fulltext`. Adds `author`, `tags`, `sections[]` (`title`, `text`, `html`), `body_text`, `body_chars`. Verified on the newest issue: 9 sections, 4105 characters.

Useful API fields: `released_time` (unix, reliable) rather than `released_at` (null in practice).

---

## 4. `linuxdo` — linux.do 晚报 (date-based URLs)

| | |
|---|---|
| Date index | `https://linuxdo.club/issues/` |
| Issue page | `https://linuxdo.club/issues/<YYYY-MM-DD>/` |

The URL changes every day, so **the date is never hard-coded**. The archive index is read to discover available dates:

```bash
python tools/collect.py --dates
# 2026-10-12
# 2026-10-11
# 2026-10-10
```

With no `--date`, the newest available issue is used. `--date` pins a specific issue.

Items come from `<a class="title" href="…">` anchors grouped under `<h2 id="…">` section headings. Each item records `section` (anchor id) and `section_name` (heading text) so items stay attributable to 今日要闻 / AI 前线 / 开源开发 / 福利羊毛 / 社区闲话 / 站外速览.

Titles carry a trailing `↗` from the external-link marker; `clean_title()` strips it.

Parsed fields: `title`, `url`, `published_at`, `summary`, `issue_date`, `issue_title`, `section`, `section_name`.

---

## 5. `hackernews` — Hacker News

| | |
|---|---|
| Ranking | `https://hacker-news.firebaseio.com/v0/topstories.json` |
| Item detail | `https://hacker-news.firebaseio.com/v0/item/<id>.json` |

`topstories` returns 500 IDs; the collector fetches only the first `--limit` items. Item fetches run sequentially, so a limit of 10 takes roughly 10–15 seconds. `feed` is configurable (`topstories`, `newstories`, `beststories`, `askstories`, `showstories`).

Parsed fields: `title`, `url`, `hn_url`, `published_at`, `score`, `comments`, `author`.

Note: `url` is the external article; `hn_url` is the discussion. Keep both.

---

## 6. `weibo` — 微博热搜

| | |
|---|---|
| Endpoint | `https://weibo.com/ajax/side/hotSearch` |
| Header | `Referer: https://weibo.com/` |

Returns JSON with a `realtime` array of ~51 entries, each with `word`, `num` (heat), `realpos`, `label_name`.

Rejected alternatives and why:

| Endpoint | Result |
|---|---|
| `weibo.com/ajax/side/hotSearch` (no Referer) | HTTP 403 |
| `m.weibo.cn/api/container/getIndex?containerid=…` | returns the Sina Visitor System page |
| `s.weibo.com/top/summary` | returns the Sina Visitor System page |

The `Referer` header is required.

Parsed fields: `rank`, `title`, `url` (search link for the hashtag), `heat`, `label`.

---

## 7. `zhihu` — 知乎热榜

| | |
|---|---|
| Endpoint | `https://api.zhihu.com/topstory/hot-list?limit=50` |

Rejected alternatives: `www.zhihu.com/billboard` and `www.zhihu.com/hot` both return HTTP 403.

The API returns ~29 entries; `target.url` is an API URL and is rewritten to the public question URL (`https://www.zhihu.com/question/<id>`).

Parsed fields: `rank`, `title`, `url`, `heat`, `excerpt`, `published_at`.

---

## Output layout

```text
data/collected/
├── index.json        # per-source status, counts, errors, timestamp
├── ruanyifeng.json
├── anywayfm.json
├── sspai.json
├── linuxdo.json
├── hackernews.json
├── weibo.json
└── zhihu.json
```

Each file:

```json
{
  "source": "zhihu",
  "collected_at": "2026-10-12T15:53:44+00:00",
  "count": 10,
  "items": [ { "title": "...", "url": "...", "rank": 1, "heat": "671 万热度" } ]
}
```

---

## Rendering

```bash
python tools/render_digest.py --indir data/collected --output site/digest.html
```

Produces one styled page: a nav bar per source, 派早报 rendered as a feature card with its nested news items, ranked lists for the hot-search boards, and score/comment metadata for Hacker News.

---

## Design notes

1. **Feeds before scrapes.** Where an official RSS exists (`sspai`), it is preferred; scraping is the fallback.
2. **APIs before HTML.** `weibo`, `zhihu`, and `hackernews` all use JSON endpoints, which are far more stable than DOM scraping.
3. **Never hard-code volatile URLs.** `linuxdo` dates come from its archive index.
4. **Balanced parsing, not lazy regex.** Nested markup is handled with a depth-counting scanner.
5. **Deduplicate on canonical keys.** Scheme, `www`, and tracking parameters are normalised; meaningful query strings are preserved.
6. **Isolate failures.** A source that breaks records its error in `index.json` without stopping the others.
7. **Keep attribution.** Every item retains its original URL; summaries are excerpts, not generated text.

---

## Extending

To add a source:

1. write a function `source_<name>(limit, **kwargs) -> list[dict]`;
2. return a list of dicts with at least `title`, `url`, `source`, `source_name`, `collector`;
3. register it in `SOURCES` with a default limit;
4. add it to `ORDER` and `SOURCE_META` in `tools/render_digest.py`;
5. run it with `--list` to verify before wiring it into a schedule.

---

## Addendum: sources added after the first pass

### 晚点 LatePost (`latepost`)

| | |
|---|---|
| List | `POST https://www.latepost.com/site/index` with `page` / `limit` |
| Detail | `https://www.latepost.com/news/dj_detail?id=<id>` |

The homepage list is Vue-rendered, so the rows are **not** in the HTML. The
inline script calls `$.ajax({type:'post', url:'/site/index', data:{page,limit}})`,
which returns clean JSON:

```json
{"code":1,"data":[{"id":"3755","title":"…","abstract":"…","release_time":"10月10日",
                   "detail_url":"/news/dj_detail?id=3755","is_dj":"0","label":[""]}]}
```

Two quirks:

1. **The TLS chain is incomplete.** `openssl s_client` shows a valid
   `CN=www.latepost.com` certificate from Let's Encrypt, but the intermediate is
   not sent, so strict verification fails — `curl` fails too, not just Python.
   The collector uses a relaxed context for this host only and documents why.
2. **Dates appear in three places.** The API gives `release_time` as `10月10日`
   with no year; the detail header gives `10月10日 14:10`; and the page contains
   an unrelated `2026/10/10` string. The collector reads the **header element**
   (`article-header-date`) and infers the year, rather than scraping the first
   date-looking string — an early version picked a 2023 date out of the body text.

### 极客公园 (`geekpark`)

| | |
|---|---|
| Feed | `https://www.geekpark.net/rss` |
| Article | `https://www.geekpark.net/news/<id>` |

`https://www.geekpark.net/` itself returns **403** to a plain fetch, but `/rss`
works and carries 30 items with `pubDate`. Titles and descriptions are wrapped in
`<![CDATA[…]]>`, which must be stripped. The article body sits in
`<div class="article-content">` and is extracted with the balanced-div scanner.

### Telegram (`telegram`)

Seven channels, one day, all messages:

```bash
python tools/collect.py telegram --day auto --pages 4 --download-images
```

- `--day auto` selects the newest day present across the fetched window; at
  00:20 CST "today" would be nearly empty, so auto is the useful default.
- `--day today | yesterday | YYYY-MM-DD` pins it explicitly.
- Pagination walks `?before=<lowest id>` until the target day boundary, so a
  busy channel is captured completely rather than truncated at 20 posts.
- `--download-images` stores up to `--images-per-post` photos per message under
  `data/telegram/media/<channel>/<message_id>/`, and the renderer prefers those
  local files over live CDN URLs.

Pre-dedupe collapses byte-identical reposts and writes
`telegram_duplicate_groups.json`. On a real day it finds **zero** groups, because
channels never repeat each other verbatim — hence the reserved semantic pass in
[`AI-REVIEW.md`](AI-REVIEW.md).

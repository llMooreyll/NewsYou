# How to fetch WeChat Official Account articles

## The short answer

There is **no public API** that lets you read all articles from an arbitrary WeChat Official Account (公众号). WeChat deliberately closes that surface. Every working method is either:

1. **owner-scoped** — official APIs that only work for an account you control;
2. **indirect** — a third-party mirror, search index, WeRead (微信读书) subscription, or Telegram mirror;
3. **per-article** — fetch or browser-capture one article URL at a time.

For "all history + all future articles of one specific account", the practical answer is an **indirect subscription service that produces a feed**, then your pipeline polls that feed.

---

## What we observed directly

| Test | Result |
|---|---|
| `https://mp.weixin.qq.com/s/zk0KxuLzhmMJ4LPYW_OHMA` via browser CDP | Redirected to `mp/wappoc_appmsgcaptcha` with `环境异常 / 当前环境异常，完成验证后即可继续访问` |
| Resulting capture status | `access_challenge` (recorded, not bypassed) |
| `https://developers.weixin.qq.com/doc/subscription/api/` via browser CDP | Full API catalog captured successfully |

The verification wall is the core problem: article pages are reachable but rate-limited and challenge-gated. We do not bypass it.

---

## Layer 1 — Official server API (your own account only)

Official documentation: <https://developers.weixin.qq.com/doc/subscription/api/>

Relevant endpoint families for content:

| Purpose | Endpoint |
|---|---|
| Get access token | `/cgi-bin/token`, `/cgi-bin/stable_token` |
| List published messages | `/cgi-bin/freepublish/batchget` |
| Get published article info | `/cgi-bin/freepublish/getarticle` |
| Draft management | `/cgi-bin/draft/add`, `/cgi-bin/draft/batchget`, `/cgi-bin/draft/get`, `/cgi-bin/draft/update` |
| Permanent material list | `/cgi-bin/material/batchget_material` |
| Article read/share analytics | `/datacube/getarticleread`, `/datacube/getarticleshare`, `/datacube/getbizsummary`, `/datacube/getarticletotaldetail` |
| Comments on published articles | `/cgi-bin/comment/list`, `/cgi-bin/comment/open` |

Requirements: a verified Official Account, `AppID` + `AppSecret`, IP allowlist, `access_token` refreshed every 7200 seconds.

**Critical limitation:** this only returns *your own* account's content. It cannot read `zk0KxuLzhmMJ4LPYW_OHMA`-style articles from someone else's account.

Use it if you also publish, or if you want engagement analytics for your own posts.

---

## Layer 2 — Indirect RSS adapters (RSSHub)

RSSHub ships multiple WeChat routes precisely because direct scraping is unreliable. Its own namespace description says: *"公众号直接抓取困难，故目前提供几种间接抓取方案，请自行选择."*

| Route | Mechanism | Config needed |
|---|---|---|
| `/wechat/wechat2rss/:id` | Consumes a Wechat2RSS feed | none |
| `/wechat/mp/homepage/:biz/:hid/:cid?` | `mp.weixin.qq.com/mp/homepage` `appmsg_list` endpoint | none, but only works for accounts whose share link contains `homepage` |
| `/wechat/mp/msgalbum/:biz/:aid` | Article topic/tag album (`话题` tag) | none |
| `/newrank/wechat/:wxid` | NewRank mirror | `NEWRANK_COOKIE`; anti-crawler; daily token cap |
| `/wechat/sogou/:id` | Sogou WeChat search | config required, CAPTCHA-prone |
| `/wechat/tgchannel/:id/:mpName?` | Mirror of an account reposted into a Telegram channel | config required |
| `/wechat/ershicimi/:id` | 二十次幂 mirror | config; service stability varies |
| `/wechat/uread/:userid` | 优读 mirror | config; service stability varies |
| `/wechat/data258/:id?` | 微阅读 mirror | config; service stability varies |
| `/wechat/ce/:id` | CareerEngine mirror | config; service stability varies |

Practical verdict:

- `/wechat/wechat2rss/...` is the most reliable RSSHub WeChat route, because it just re-serves an existing feed.
- `/wechat/mp/homepage/...` is useful for accounts that expose a homepage template, and it reads the `appmsg_list` endpoint with `begin`/`count` pagination — that endpoint is the closest thing to a public "history list" for those accounts.
- `/newrank/wechat/...` works but is explicitly anti-crawler and quota-limited; use low frequency.
- The mirror routes (`ershicimi`, `uread`, `data258`, `ce`, `sogou`) break when the upstream mirror dies or adds challenges.

---

## Layer 3 — Self-hosted subscription services (recommended for "all history + future")

This is the approach that actually solves your question.

### WeWe RSS (MIT, open source)

<https://github.com/cooderl/wewe-rss>

- Based on **WeRead (微信读书)**, which hosts Official Account articles.
- Supports **historical articles** for a subscribed account.
- **Scheduled background updates** (cron), so future articles arrive automatically.
- Emits `.rss`, `.atom`, and `.json` per account, plus an all-feeds endpoint.
- Full-text output mode (`FEED_MODE=fulltext`), title include/exclude filters, OPML export.
- Docker or Docker Compose deployment; MySQL recommended, SQLite supported.
- Workflow: log in by scanning a QR code with WeChat, then add an account by pasting **any one of its article share links**.

Key environment variables:

```text
DATABASE_URL, DATABASE_TYPE, AUTH_CODE, SERVER_ORIGIN_URL,
FEED_MODE=fulltext, CRON_EXPRESSION, UPDATE_DELAY_TIME,
MAX_REQUEST_PER_MINUTE, ENABLE_CLEAN_HTML, PLATFORM_URL
```

Its own README warns about the real cost: adding subscriptions too fast can trigger platform rate-limiting ("今日小黑屋"), and the login session must not be configured to auto-expire after 24 hours. It also routes some requests through a proxy domain.

### Wechat2RSS

<https://github.com/ttttmr/wechat2rss> — long-running, low-latency (average ~6h), full-text, image/video proxying, follows account migrations. A free tier covers a set of public accounts; a self-hosted tier is a paid subscription.

### wechat-download-api

<https://github.com/tmwgsicp/wechat-download-api> — open-source article fetch + RSS API with export to Markdown/HTML/Word/PDF/EPUB/Excel/JSON.

### Why this layer is the answer

```text
one specific WeChat account
        |
        v
WeWe RSS / Wechat2RSS  (handles login, history, scheduling)
        |
        v
Atom/RSS feed for that account
        |
        v
our pipeline: poll -> dedupe -> normalize -> review -> newsletter
```

Your project does **not** need to solve WeChat's anti-scraping problem. It only needs to consume a feed. That is a much smaller and much more stable surface.

---

## Layer 4 — One-time full archive export

For a complete historical dump rather than a feed:

- **wechat-article-exporter** — uses the Official Account backend search interface to enumerate and bulk export an account's articles; requires a 公众号 account and can be blocked if the target account disabled search.
- **wnma3mz/wechat_articles_spider**, **Shaoshi17/WeChat-Article-Crawler**, **YWX/weixin_crawler** — crawlers that typically depend on a specific WeChat client version plus `mitmproxy`; they obtain article URLs for hundreds to ~1000 articles per account and are fragile across WeChat updates.
- **tmwgsicp/wechat-download-api** — export in 7 formats.

Best used once, then kept current with Layer 3.

---

## Layer 5 — Per-article browser capture (this project)

For an individual link you care about, `tools/cdp_capture.py` captures the rendered article through the already-authorized browser session:

```bash
python tools/cdp_capture.py \
  "https://mp.weixin.qq.com/s/ARTICLE_TOKEN" \
  --output data/browser-captures/wechat-article.json --wait 8 --min-text 800
```

If the session has already passed verification, this yields the real article text. If not, the capture honestly records `access_challenge`.

This layer is a **fallback and a manual inbox**, not a bulk collector.

---

## Recommended choice

| Your goal | Use |
|---|---|
| One specific account, history + future, lowest effort | Self-host **WeWe RSS**, subscribe the account, poll its Atom feed |
| Preference for a maintained paid service | **Wechat2RSS** self-hosted |
| Ad-hoc individual article | `tools/cdp_capture.py` |
| Only accounts with a homepage template | RSSHub `/wechat/mp/homepage/:biz/:hid` |
| One-time complete archive | wechat-article-exporter / download API |
| Your own account's analytics and published list | Official server API |

Recommended primary path for this project: **WeWe RSS → Atom feed → our adapter**, with browser capture as the per-article fallback.

---

## Incremental sync model

Once you have a feed, "all history" and "future articles" become an ordinary feed-consumption problem:

```text
first run
  -> read up to N historical entries (bounded backfill)
  -> store identity keys for every entry

later runs
  -> read the feed
  -> skip entries whose identity key already exists
  -> insert new entries
  -> if title/summary changed for a known key, record it as an observation, not a new item
```

### Identity keys for WeChat articles

WeChat article URLs carry stable identifiers:

```text
https://mp.weixin.qq.com/s?__biz=<BIZ>&mid=<MID>&idx=<IDX>&sn=<SN>
https://mp.weixin.qq.com/s/<SHORT_TOKEN>
```

Use, in order:

```text
1. (biz, mid, idx)          # most stable when present
2. sn                       # when biz/mid are absent
3. feed entry guid
4. sha256(canonical_url)
5. sha256(normalized_title + published_date)   # last resort
```

Strip tracking parameters (`chksm`, `scene`, `srcid`, `from`, `isappinstalled`, `key`, `pass_ticket`) before hashing. Keep the original URL for citation.

### Store per article

- account name and `__biz`;
- title, author/byline, publish timestamp;
- original URL and canonical URL;
- digest/summary from the feed;
- full text if the feed provides it (WeWe RSS `fulltext` / Wechat2RSS);
- cover image URL;
- feed id and entry guid;
- capture time, adapter name, adapter version;
- provenance marker: `observed_feed`, `observed_page`, or `editor_verified`.

---

## Risks and rules to respect

1. **Account restriction is the real cost.** Indirect methods depend on a logged-in WeChat/WeRead account. Mass-subscribing or aggressive polling can lock that account out temporarily. Add sources slowly and poll on a daily cadence, not every few minutes.
2. **Do not bypass challenges.** The `环境异常` verification is an access control. We record it and stop.
3. **Session files are credentials.** Keep WeRead/WeChat session data outside this repository and outside the public showcase.
4. **Copyright.** Keep links plus short attributed excerpts, not full republished articles, in anything you distribute.
5. **Third-party mirrors can disappear.** Treat `ershicimi`, `uread`, `data258`, `ce`, `sogou`, and NewRank routes as optional adapters with health checks, never as foundations.
6. **Prefer a feed over a crawler.** A feed is a public contract. A crawler is a maintenance liability.

---

## Sources reviewed

- WeChat server API documentation: <https://developers.weixin.qq.com/doc/subscription/api/>
- RSSHub WeChat namespace source: `lib/routes/wechat/` (`mp.ts`, `wechat2rss.ts`, `sogou.ts`, `uread.ts`, `data258.ts`, `ce.ts`, `ershcimi.ts`, `tgchannel.ts`, `msgalbum.ts`)
- RSSHub NewRank WeChat route: `lib/routes/newrank/wechat.ts`
- RSSHub WeChat full-text helper: `lib/utils/wechat-mp.ts`
- WeWe RSS: <https://github.com/cooderl/wewe-rss>
- Wechat2RSS: <https://github.com/ttttmr/wechat2rss>
- wechat-download-api: <https://github.com/tmwgsicp/wechat-download-api>
- Chinese RSS history for WeChat: <https://sspai.com/post/93845>

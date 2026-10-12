# Research report and decisions

## Executive conclusion

The most reliable design is a **source-adapter system with a URL inbox**, not a single universal crawler. The research shows that the requested services have very different access surfaces:

- RSS and podcast feeds are the most stable and portable.
- Reddit and X provide official API paths, but authentication, rate limits, and pricing matter.
- WeChat and Weibo have official developer surfaces mainly for authorized/owned accounts, not unrestricted access to arbitrary public content.
- Bilibili has an official developer platform and public page/API ecosystem, but subtitle coverage and anti-crawling behavior vary.
- RSSHub provides a valuable adapter ecosystem, but routes can depend on authentication, break when platforms change, and should be self-hosted/monitored if relied upon.
- Browser/CDP capture is the practical personal fallback for user-selected pages, but it should remain narrow, rate-limited, and authorization-aware.
- Podcasts are the best media starting point because RSS is the publishing contract; transcript availability varies, so ASR is a second-stage fallback.

## Findings by problem

### 1. Feed normalization and aggregation

**Reference projects:**

- [RSSHub](https://github.com/DIYgod/RSSHub) — converts many websites and platforms into RSS/Atom-style feeds and has thousands of community routes.
- [FreshRSS](https://github.com/FreshRSS/FreshRSS) — self-hosted feed aggregator with an API and useful feed-management patterns.
- [Miniflux](https://github.com/miniflux/v2) — a smaller, opinionated feed reader worth studying for polling and reader API behavior.
- [Folo](https://github.com/RSSNext/Folo) — a modern RSS reader that pairs well with RSSHub and demonstrates a user-facing discovery/reading workflow.
- [Omnivore](https://github.com/omnivore-app/omnivore) and [Karakeep](https://github.com/karakeep-app/karakeep) — useful references for a URL inbox, read-later workflow, and content archive.
- [trafilatura](https://github.com/adbar/trafilatura) — a Python extractor for main text and metadata from ordinary web pages.

**Decision:** use RSS/Atom as the primary ingestion contract, but build a first-class URL inbox so the system does not depend on every platform having RSS.

### 2. WeChat

The official WeChat documentation exposes server APIs for authorized Official Accounts. Search results and the official developer surface do not provide a general, unrestricted API for reading arbitrary public accounts at newsletter scale. RSSHub has a NewRank/WeChat route, and third-party services advertise structured access, but these should be treated as optional and change-sensitive.

**Decision:**

1. recurring source: user supplies a permitted feed/route or a list of article URLs;
2. one-off capture: open the article in the authorized Helium browser and capture the rendered article;
3. fallback: paste text or save a URL through the inbox;
4. no broad crawler, cookie harvesting, or anti-bot bypass.

References:

- [WeChat server API documentation](https://developers.weixin.qq.com/doc/subscription/api/)
- [RSSHub WeChat route source](https://github.com/DIYgod/RSSHub/blob/master/lib/routes/newrank/wechat.ts)

### 3. X

The official [X API](https://docs.x.com/x-api/introduction) supports posts, users, lists, threads, quote posts, search, filtered streams, and trends. It uses usage-based pricing, so cost must be visible in the source configuration and logs. RSSHub routes can work, but search results show they may depend on session credentials and are not a stable substitute for the official API.

**Decision:**

- use the X API for a small set of selected accounts/lists/searches when budget and access are acceptable;
- use browser capture for individual URLs or small user-selected sets;
- do not design the first version around unrestricted historical search;
- store post IDs and thread relationships for deduplication.

References:

- [X API introduction](https://docs.x.com/x-api/introduction)
- [X API pricing](https://docs.x.com/x-api/getting-started/pricing)
- [RSSHub usage](https://rsshub.netlify.app/usage)

### 4. Reddit

Reddit documents a Data API and OAuth path. Reddit is a good early structured adapter because subreddit/user scopes are explicit and post/comment objects have stable IDs and permalinks. API rules, rate limits, deleted content, and community norms still apply.

**Decision:** implement Reddit before the more fragile Chinese social platforms. Start with configured subreddit feeds, user feeds, and saved URLs. Store both the post and enough thread context to explain the claim.

Reference:

- [Reddit Data API Wiki](https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki)

### 5. Bilibili

Bilibili has an [official open platform](https://openhome.bilibili.com/doc) and developer service agreement. The surrounding ecosystem exposes public metadata and community tools for captions/danmaku, but coverage is inconsistent. RSSHub's own issue history records instability and anti-crawling problems for Bilibili routes.

**Decision:**

- capture video metadata first;
- use available creator/platform captions or transcript files;
- otherwise process only media the user is authorized to process, using a permitted path and local ASR;
- keep timestamps and a transcript confidence field;
- treat danmaku/comments as a separate, optional signal rather than mixing them into spoken content.

References:

- [Bilibili open platform documentation](https://openhome.bilibili.com/doc)
- [Bilibili developer agreement](https://open.bilibili.com/agreement/developer-service)
- [RSSHub Bilibili route issue](https://github.com/DIYgod/RSSHub/issues/17824)
- [Bilibili API collection reference](https://github.com/pskdje/bilibili-API-collect)

### 6. Weibo

Weibo's open platform has OAuth and API documentation, but practical access depends on app type, permissions, tokens, and current platform policy. RSSHub contains Weibo routes, yet issue history suggests route health can vary.

**Decision:**

- support official access only where an app is authorized for the required data;
- initially prioritize URL-based browser capture for selected posts/profiles;
- treat RSSHub as a monitored optional source, not a hard dependency;
- preserve repost/quoted relationships and original post IDs.

References:

- [Weibo Open Platform](https://open.weibo.com/)
- [RSSHub Weibo route source](https://github.com/DIYgod/RSSHub/blob/master/lib/routes/weibo/friends.ts)
- [RSSHub route health example](https://github.com/DIYgod/RSSHub/issues/20836)

### 7. Podcasts: Chinese and English

Podcast RSS is the strongest cross-platform foundation. Useful directories and APIs include:

- [Podcast Index API](https://podcastindex-org.github.io/docs-api/) — feed and episode discovery plus podcast metadata;
- [Listen Notes API](https://www.listennotes.com/api/docs/) — commercial directory/search API;
- [Podchaser API](https://api-docs.podchaser.com/) — commercial podcast/episode/creator metadata;
- [gPodder podcast directories](https://gpodder.github.io/docs/podcast-directories.html) — open ecosystem reference;
- [Chinese-Podcasts](https://github.com/alaskasquirrel/Chinese-Podcasts) — a curated Chinese podcast list/reference;
- [tidings-rss](https://github.com/fuxiaoai/tidings-rss) — live-verified OPML collections, including Chinese podcast and technology sources;
- [podcast-dl](https://github.com/xiaods/podcast-dl) — Chinese-documented RSS download/transcription reference;
- [Podcast Index transcript namespace examples](https://github.com/Podcastindex-org/podcast-namespace/blob/main/docs/examples/transcripts/transcripts.md).

Chinese platforms such as 小宇宙 may display shows whose underlying feed is hosted elsewhere. The system should store both the platform page and the actual RSS feed when available, rather than treating a platform page as the only canonical source.

**Decision:** implement podcast RSS discovery and ingestion early. Add transcript detection, then optional local ASR with [Whisper](https://github.com/openai/whisper) or [faster-whisper](https://github.com/SYSTRAN/faster-whisper). Do not automatically download an entire show archive.

### 8. Browser/CDP capture

Playwright documents `connectOverCDP`, which lets a client attach to an existing Chromium instance. This matches the Helium-browser requirement, assuming Helium exposes a compatible remote debugging endpoint.

**Decision:** implement a small, explicit browser-capture adapter that:

- accepts a CDP endpoint from configuration;
- connects without creating a new browser profile;
- captures only supplied URLs or configured source pages;
- stores visible text, metadata, links, and a screenshot for diagnostics;
- never exports cookies by default;
- uses per-domain rate limits and a manual stop/pause control;
- records whether content was captured from a logged-in session.

Reference:

- [Playwright `connectOverCDP`](https://playwright.dev/docs/api/class-browsertype#browser-type-connect-over-cdp)

### 9. Audio/video transcription

[OpenAI Whisper](https://github.com/openai/whisper) supports multilingual speech recognition and translation. [faster-whisper](https://github.com/SYSTRAN/faster-whisper) provides a CTranslate2 implementation with timestamps and lower resource usage in many deployments. The podcast and Bilibili reference projects show the same general pattern: RSS/page metadata → audio → transcript → searchable index → summary.

**Decision:** transcripts are derived artifacts, never replacements for source evidence. Store model name, version, language, timestamp segments, and confidence/quality flags.

## Proposed technology choices

| Need | Initial choice | Why |
|---|---|---|
| API/admin | FastAPI | Small Python service, easy local operation |
| Schedule/worker | One local worker first; Redis queue later | Avoid premature infrastructure |
| Database | SQLite first, PostgreSQL later | No installation or service requirement for prototype |
| Raw storage | `data/raw/` content-addressed files | Simple and auditable |
| Text extraction | trafilatura + platform-specific adapters | Good ordinary-page baseline |
| Browser | Playwright over Helium CDP | Uses the existing authorized browser session |
| Podcast parsing | feedparser + Podcast Index | RSS-first and portable |
| ASR | optional Whisper/faster-whisper worker | Chinese/English support without mandatory cloud costs |
| Search | SQLite FTS5 first | Local, easy, no system changes |
| Output | Markdown and static HTML first | Easy to inspect and publish later |

## What the research changed

1. WeChat and Weibo should not be promised as simple API integrations.
2. RSSHub should be an optional compatibility layer with health checks.
3. Podcasts should be an early vertical slice because RSS and enclosure metadata are standardized.
4. Browser capture should focus on user-selected URLs before profile-wide crawling.
5. Raw evidence and derived summaries must be separate records.
6. The newsletter needs an editorial queue from day one; fully automatic publishing is unsafe for mixed-quality social content.

## Browser DevTools observations

A live Chrome DevTools Protocol endpoint was found at `http://127.0.0.1:9222` and tested without installing any package. The project now includes a standard-library client at [`tools/cdp_capture.py`](../tools/cdp_capture.py). Captures are in [`data/browser-captures/`](../data/browser-captures/).

Observed results:

- **X API docs:** full rendered navigation and main documentation text were captured, including the current pay-per-use language and post/list/search capabilities.
- **WeChat Official Account docs:** the rendered API catalog was captured. It exposes account credentials, drafts, published-message lookup, article analytics, comments, user management, and related functions for the authorized Official Account. This supports the decision that the official API is account-scoped rather than an unrestricted arbitrary-publication reader.
- **Xiaoyuzhou:** a public podcast page yielded show metadata, episode links, descriptions, timestamps, explicit transcript links, and an underlying RSS URL embedded in the show notes. This proves that browser capture is useful for feed discovery and transcript discovery even when the platform page is not itself the canonical feed.
- **RSSHub GitHub and Podcast Index docs:** the initial DOM capture contained very little text because those pages render or hydrate content differently. The adapter therefore needs a load/readiness strategy and, where appropriate, a direct HTTP/API fallback.
- **Bilibili Open Platform:** the tested page remained `about:blank` in the new target. This must be treated as an adapter failure, not as evidence that the platform has no documentation.
- **Weibo Open Platform:** the browser returned `ERR_CONNECTION_CLOSED` from this environment. Weibo integration therefore requires a connectivity test and cannot be assumed to work from every network.

These results demonstrate why the browser adapter must record final URL, page title, error pages, readiness status, and extracted text length. A successful navigation request is not necessarily a successful content capture.

## Search limitations

Some official Chinese platform pages are difficult to index or return incomplete documentation through public search. The design therefore marks platform-specific adapters as conditional and requires a local connectivity test before implementation. This report intentionally does not claim an endpoint is stable merely because a third-party scraper documents it.

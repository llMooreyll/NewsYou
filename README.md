# Personal Newsletter Intelligence System

This repository is the design starting point for a personal newsletter that collects, understands, and curates technology, film, and society content across Chinese and English sources.

## Current status

The pipeline is **working end to end**: ten sources collect with plain HTTP, every item carries a timestamp and its original link, and one command renders a reviewable digest.

```bash
python tools/collect.py --all --download-images --outdir data/collected
python tools/render_digest.py --indir data/collected --output site/digest.html
```

Standard library only. No installs, no search tools, no system changes.

Read the documents in this order:

1. [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — the end-to-end method.
2. [`docs/COLLECTORS.md`](docs/COLLECTORS.md) — every source, endpoint, and parsing decision.
3. [`docs/AI-REVIEW.md`](docs/AI-REVIEW.md) — the reserved AI dedupe/rank step.
4. [`docs/RESEARCH.md`](docs/RESEARCH.md) — online research, reference projects, and decisions.
5. [`docs/WECHAT.md`](docs/WECHAT.md) — how to get all current and future articles from one WeChat account.
6. [`docs/TELEGRAM.md`](docs/TELEGRAM.md) — Telegram channel collection plus a Telethon implementation guide.
7. [`docs/TELEGRAM-NO-API.md`](docs/TELEGRAM-NO-API.md) — five ways to collect Telegram without an `api_id`.
8. [`docs/RSSHUB.md`](docs/RSSHUB.md) — what RSSHub can and cannot fetch (measured coverage).
9. [`docs/SOURCE_MATRIX.md`](docs/SOURCE_MATRIX.md) — how each service should be captured, with fallbacks and constraints.
10. [`docs/CONTENT_MODEL.md`](docs/CONTENT_MODEL.md) — the common data contract and provenance model.
11. [`config/sources.example.yaml`](config/sources.example.yaml) — the source configuration format.
12. [`docs/ROADMAP.md`](docs/ROADMAP.md) — the staged implementation plan.

## Core principle

> Collect broadly, preserve the original evidence, transform cautiously, and publish only claims that can be traced back to a source.

The newsletter should not pretend that a generated summary is the source. Every published item must retain its original URL, capture time, extraction method, language, transcript or article evidence, and processing confidence.

## Proposed first implementation

- **Backend:** Python 3.12, FastAPI, a worker queue, and PostgreSQL (SQLite is acceptable for the first local prototype).
- **Collection:** RSS/Atom and official APIs first; a dependency-free CDP probe or a future Playwright adapter connected to the Helium browser endpoint for user-authorized browser capture; URL inbox for manual saves.
- **Media:** existing captions/transcripts first, then `ffmpeg` plus Whisper/faster-whisper for permitted audio/video.
- **Storage:** immutable raw captures in local object storage, normalized records in PostgreSQL, and full-text/vector indexes for search and clustering.
- **Editorial workflow:** an inbox for review, not fully autonomous publishing.
- **Output:** HTML email, Markdown archive, and an RSS feed of the newsletter itself.

## Browser DevTools proof

The environment exposes a Chrome DevTools Protocol endpoint at `http://127.0.0.1:9222`. [`tools/cdp_capture.py`](tools/cdp_capture.py) connects using only Python's standard library, opens a separate tab, and records bounded rendered text without exporting browser cookies.

Example:

```bash
python tools/cdp_capture.py https://example.com \
  --output data/browser-captures/example.json
```

Research captures are stored in [`data/browser-captures/`](data/browser-captures/). They include successful rendered captures and failed-access evidence, because adapter design must account for both.

## Collectors

[`tools/collect.py`](tools/collect.py) implements ten source adapters, standard library only:

```bash
python tools/collect.py --list-sources
python tools/collect.py --all --download-images --outdir data/collected
python tools/collect.py sspai --limit 20
python tools/collect.py telegram --day 2026-10-12 --pages 4
python tools/collect.py --dates          # linuxdo issue dates, auto-discovered
```

| Source | Endpoint | Full text | Timestamp |
|---|---|---|---|
| `sspai` 少数派 | `/feed` + `/api/v1/articles` | 派早报全文，其余仅标题+链接 | RSS / API |
| `telegram` ×7 | `t.me/s/<channel>` | — | post |
| `linuxdo` | `/issues/<date>/` | — | issue date |
| `latepost` 晚点 | `POST /site/index` | 最新 3 篇 | article page |
| `geekpark` 极客公园 | `/rss` | 最新 3 篇 | RSS |
| `ruanyifeng` | `/blog/atom.xml` + archives | 最新一期全文 | Atom |
| `anywayfm` | `/now/` | 最新一期全文 | page |
| `hackernews` | `firebaseio.com/v0` | — | API |
| `weibo` 微博热搜 | `/ajax/side/hotSearch` | — | capture time |
| `zhihu` 知乎热榜 | `api.zhihu.com/topstory` | — | API |

Design rules enforced by the collectors:

- **every item carries a timestamp**; when a source genuinely has none (hot-search boards), `published_at` falls back to the collection time and `date_source` says so instead of inventing a date;
- **volatile URLs are never hard-coded** — linuxdo issue dates are discovered from its archive index;
- **full text is scoped** — 派早报, plus the latest issue for 阮一峰 and Anyway.Now, plus the newest articles for 晚点 and 极客公园;
- **one failing source never aborts the run**; its error lands in `index.json`.

### Telegram

Seven channels for the target day, in one panel:

```
@MaiTungNews  @tnews365  @zaihuapd  @xhqcankao
@GodlyNews1  @NewlearnerChannel  @DNSPODT
```

No `api_id`, no login, no bot — the public `t.me/s/` preview, paged backwards with `?before=<message id>`. `--day auto` picks the newest day present. Images can be saved locally with `--download-images` so the page never depends on a live CDN.

### Rendering

```bash
python tools/render_digest.py --indir data/collected --output site/digest.html
```

Black background, single red accent, no rounded corners, sticky tab nav, keyboard `1–9`. Telegram renders as a masonry card wall; linux.do shows its six sections inside one panel; 派早报 renders as a full-text feature.

### The reserved AI step

Seven channels repost the same events in different words, so hash equality finds nothing:

```text
60 messages · exact-duplicate groups: 0
```

`tools/collect.py` therefore emits `telegram_ai_queue.json` — a local file containing the messages plus the model instructions and expected response schema — for a semantic dedupe/rank pass. Nothing is sent anywhere automatically. See [`docs/AI-REVIEW.md`](docs/AI-REVIEW.md).

See [`docs/COLLECTORS.md`](docs/COLLECTORS.md) for the full endpoint and parsing reference.

## Telegram without credentials
[`tools/telegram_preview.py`](tools/telegram_preview.py) is the low-level public-preview fetcher used by `collect.py`, and also runs standalone — no `api_id`, no login, no bot.

```bash
python tools/telegram_preview.py telegram --pages 3 \
  --output data/telegram/telegram-news.json
```

Verified: 20 posts per page, paged backwards with `?before=<message id>`, 60 posts over 3 pages with no duplicates. See [`docs/TELEGRAM-NO-API.md`](docs/TELEGRAM-NO-API.md) for the four other no-credential methods.

## Important boundary

The system is for personal research and curation. It should respect each platform's terms, robots/access controls, rate limits, copyright, privacy requirements, and API licenses. It must not bypass paywalls, DRM, CAPTCHAs, login controls, or technical restrictions. For authenticated sites, the browser capture mode uses the user's already-authorized session and stores the smallest useful artifact.

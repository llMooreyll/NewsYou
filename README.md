# Personal Newsletter Intelligence System

This repository is the design starting point for a personal newsletter that collects, understands, and curates technology, film, and society content across Chinese and English sources.

## Current status

The first milestone is the **method and system design**, not a platform-specific scraper. The system is intentionally designed as a layered collector because no single API or scraper can reliably cover WeChat, X, Bilibili, Weibo, Reddit, podcasts, and ordinary websites.

Read the documents in this order:

1. [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — the end-to-end method.
2. [`docs/RESEARCH.md`](docs/RESEARCH.md) — online research, reference projects, and decisions.
3. [`docs/WECHAT.md`](docs/WECHAT.md) — how to get all current and future articles from one WeChat account.
4. [`docs/TELEGRAM.md`](docs/TELEGRAM.md) — Telegram channel collection plus a Telethon implementation guide.
5. [`docs/TELEGRAM-NO-API.md`](docs/TELEGRAM-NO-API.md) — five ways to collect Telegram without an `api_id`.
6. [`docs/RSSHUB.md`](docs/RSSHUB.md) — what RSSHub can and cannot fetch (measured coverage).
7. [`docs/SOURCE_MATRIX.md`](docs/SOURCE_MATRIX.md) — how each service should be captured, with fallbacks and constraints.
8. [`docs/CONTENT_MODEL.md`](docs/CONTENT_MODEL.md) — the common data contract and provenance model.
9. [`config/sources.example.yaml`](config/sources.example.yaml) — the first source configuration format.
10. [`docs/ROADMAP.md`](docs/ROADMAP.md) — the staged implementation plan.

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

## Feed and API collectors

[`tools/feed_collect.py`](tools/feed_collect.py) is a standard-library-only collector for seven sources. No search tools, no installs, fully reproducible:

```bash
python tools/feed_collect.py --list-sources
python tools/feed_collect.py --all --fulltext --outdir data/collected
python tools/feed_collect.py ruanyifeng --limit 10
python tools/feed_collect.py linuxdo --date 2026-10-11
python tools/feed_collect.py --dates          # linuxdo issue dates, auto-discovered
```

| Source | Endpoint | Notes |
|---|---|---|
| `ruanyifeng` | Atom feed + `archives.html` | feed gives 3 entries, archive widens to 20 |
| `anywayfm` | `anyway.fm/now/` link list | newest-first link entries |
| `sspai` | `/feed` + `/api/v1/articles` | filtered to 派早报; `--fulltext` pulls the article body |
| `linuxdo` | `linuxdo.club/issues/<date>/` | date discovered from `/issues/`, never hard-coded |
| `hackernews` | Firebase API | `topstories` + per-item detail |
| `weibo` | `weibo.com/ajax/side/hotSearch` | hot search board with heat values |
| `zhihu` | `api.zhihu.com/topstory/hot-list` | hot list with heat and timestamps |

Render everything into one page:

```bash
python tools/render_digest_html.py --indir data/collected --output site/captures/digest.html
```

See [`docs/COLLECTORS.md`](docs/COLLECTORS.md) for the full endpoint and parsing reference.

## Telegram without credentials

[`tools/telegram_preview.py`](tools/telegram_preview.py) collects public Telegram channels via the public `t.me/s/` preview — no `api_id`, no login, no bot. Standard library only.

```bash
python tools/telegram_preview.py telegram --pages 3 \
  --output data/telegram/telegram-news.json
```

Verified: 20 posts per page, paged backwards with `?before=<message id>`, 60 posts over 3 pages with no duplicates. See [`docs/TELEGRAM-NO-API.md`](docs/TELEGRAM-NO-API.md) for the four other no-credential methods.

## Important boundary

The system is for personal research and curation. It should respect each platform's terms, robots/access controls, rate limits, copyright, privacy requirements, and API licenses. It must not bypass paywalls, DRM, CAPTCHAs, login controls, or technical restrictions. For authenticated sites, the browser capture mode uses the user's already-authorized session and stores the smallest useful artifact.

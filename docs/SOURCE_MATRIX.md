# Source capture matrix

This matrix defines the first safe method for each requested service. “Preferred” means the normal path. “Fallback” means a user-selected or explicitly configured alternative, not an invitation to bypass platform controls.

| Source | Preferred capture | Fallback | What to preserve | Main risks / limits |
|---|---|---|---|---|
| WeChat public articles | A self-hosted subscription service (WeWe RSS over WeRead, or Wechat2RSS) producing one Atom feed per account; official API only for accounts you own | Individual article URL capture via the authorized browser; RSSHub `/wechat/mp/homepage` for accounts with a homepage template; one-time archive export | Account name and `__biz`, title, byline, publish time, original + canonical URL, digest, full text if the feed supplies it, cover image, feed guid | No public API for arbitrary accounts; article pages are rate-limited and challenge-gated; indirect services depend on a logged-in WeChat/WeRead account and can trigger temporary account restriction; mirror routes are unstable |
| X posts/threads | Official X API for configured users, lists, searches, or filtered streams | Capture a user-selected post/thread in the Helium browser; manual URL inbox; RSSHub only as an optional adapter | Post IDs, author, timestamps, thread order, quoted/replied posts, links/media, raw text | API uses usage-based pricing; broad search can be expensive or unavailable at the desired tier; browser pages are dynamic and login-dependent |
| Bilibili videos | Public metadata/page capture; use creator/platform-provided subtitles or transcript when exposed | User-selected browser capture; process an allowed audio/video URL with `ffmpeg` + Whisper; manual transcript import | BV/AV ID, title, uploader, date, description, chapters, caption source, timestamped transcript, original URL | Captions may be absent or inaccurate; do not bypass DRM, geo restrictions, login controls, or download restrictions; media retention can create copyright obligations |
| Weibo posts | Official access where the account/app has permission and the needed endpoint | User-selected post/profile capture through the authorized browser; manual URL inbox; optional RSSHub route | Post ID, author, timestamp, text, quoted/reposted relationship, links/images, capture time | API availability and permissions vary; dynamic pages and anti-automation controls; do not crawl broadly or evade restrictions |
| Reddit posts/comments | Reddit Data API with OAuth for selected subreddits/users/searches; use official feed endpoints where permitted | Public URL capture; manual URL inbox | Post/comment IDs, subreddit, author, score/time if available, permalink, thread context, edit/deletion state | OAuth, rate limits, API terms, deleted content, community rules; treat posts as claims and keep source attribution |
| English podcasts | Direct RSS/Atom feed; Podcast Index for discovery and metadata | Listen Notes/Taddy or another licensed directory if credentials are later added; manual RSS URL | Show/episode IDs, feed URL, enclosure URL, title, description, date, duration, transcript URL, chapters | Directory coverage differs; audio is often copyrighted; avoid downloading every episode automatically |
| Chinese podcasts | Direct show RSS when available; Podcast Index or a known directory for discovery; manual feed entry | Capture show/episode page in browser; manual audio/transcript import | Original-language title/description, show/episode URL, feed URL, enclosure, transcript/show notes, language | Some Chinese platforms expose metadata but not a stable RSS feed; platform terms and regional access vary |
| Telegram channels | Public channels: `t.me/s/<channel>` preview, no credentials, ~20 posts/page paged with `?before=<id>`; user-authorized MTProto/TDLib client when an `api_id` is available | Telegram Desktop JSON export for one-time history (private channels/groups); Bot API `channel_post` for channels you administer; browser-session capture for private channels | Chat/channel ID, message ID, permalink, publication/edit time, original text/entities, captions, media metadata, forwards/replies, views, capture time | User session is highly sensitive; API terms and anti-abuse monitoring; private-channel membership required; Telegram hides some channels from the public preview; bots need channel admin rights and cannot read history; current API terms restrict AI/ML use of Telegram data |
| RSSHub (adapter layer) | Self-hosted instance for cookie-requiring routes; use as a feed provider inside the pipeline, never as the foundation | Public instance for read-only, no-config routes | Route path, upstream platform, config requirements, health status, last successful item | ~1,983 platform namespaces but routes break when sites change; **no Reddit namespace**; no direct WeChat scraping; authenticated routes need your cookies; not a historical archive |
| Ordinary websites/newsletters | RSS/Atom, sitemap, or public article URL; subscribe to newsletters via a dedicated inbox only with permission | Readability extraction from the user-selected page; manual paste | Canonical URL, title, byline, publication time, article body, links, images, feed metadata | Paywalls, robots/access restrictions, copyright, tracking URLs, syndicated duplicates |

## Adapter confidence

Every normalized field should have a provenance marker:

- `observed_api` — returned by an official API;
- `observed_feed` — read from RSS/Atom or podcast metadata;
- `observed_page` — visible on the captured page;
- `observed_transcript` — supplied by the publisher/platform;
- `derived_asr` — generated from permitted media by ASR;
- `derived_translation` — machine translation;
- `derived_summary` — generated summary;
- `editor_verified` — checked or corrected by the user.

The newsletter renderer should visibly distinguish source evidence from generated text in the internal review screen. The public issue can stay readable, but it must retain the source link and timestamp/quote evidence.

## Recommended implementation order

### Phase 1: low-risk structured sources

1. RSS/Atom websites.
2. Podcast RSS feeds.
3. Podcast Index discovery.
4. Reddit OAuth adapter.
5. URL inbox and Markdown output.

### Phase 2: browser-assisted individual captures

1. Helium CDP connection test.
2. One URL at a time for WeChat.
3. One URL at a time for X.
4. One URL at a time for Weibo.
5. One URL at a time for Bilibili.

The browser adapter should initially capture only the currently opened/user-selected page. It should not implement broad profile crawling until rate limits, retention, and terms have been reviewed.

### Phase 3: media understanding

1. Detect publisher-provided captions/transcripts.
2. Import podcast transcript namespace data when present.
3. Add local Whisper/faster-whisper as an optional worker.
4. Add timestamp citations and chapter extraction.
5. Add speaker diarization only if it proves useful.

### Phase 4: editorial intelligence

1. Deduplication.
2. Topic classification.
3. Clustering.
4. Evidence-backed summaries.
5. Ranking and feedback.
6. HTML email/archive publication.

## Reference projects and specifications

- [RSSHub](https://github.com/DIYgod/RSSHub) — broad RSS adapter ecosystem; use as an optional feed source, not as a guarantee that every route is stable or permitted.
- [FreshRSS](https://github.com/FreshRSS/FreshRSS) — self-hosted RSS reader and API reference for feed management ideas.
- [X API](https://docs.x.com/x-api/introduction) — official posts, users, lists, trends, and filtered-stream access; current pricing is usage-based.
- [Reddit Data API Wiki](https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki) — official API entry point and OAuth guidance.
- [Podcast Index API](https://podcastindex-org.github.io/docs-api/) — open podcast discovery and metadata API.
- [Podcast namespace transcripts](https://github.com/Podcastindex-org/podcast-namespace/blob/main/docs/examples/transcripts/transcripts.md) — transcript metadata conventions.
- [OpenAI Whisper](https://github.com/openai/whisper) — multilingual speech recognition and translation model; self-hostable under its repository license.
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — media extraction utility with broad site support; use only for content and access paths you are authorized to process, and do not use it to defeat restrictions.
- [RSS-to-Whisper](https://github.com/baz8080/rss-to-whisper) — reference pattern for podcast RSS, transcription, and local search.
- [Better Morning](https://github.com/00sapo/better-morning) — reference pattern for RSS-to-LLM digest generation.

These projects are references, not dependencies of this initial design. No package is installed by this project at this stage.

# Delivery roadmap

This roadmap is designed for a local, safe prototype. It assumes no system-wide installation and no external service is required to inspect the design documents.

## Milestone 0 — method and evidence (complete)

- [x] End-to-end architecture.
- [x] Platform capture matrix.
- [x] Research notes and reference projects.
- [x] Common content/provenance model.
- [x] Dependency-free browser CDP probe.
- [x] Live CDP captures of X docs, WeChat docs, Xiaoyuzhou, RSSHub, and Podcast Index attempts.
- [x] Record browser limitations instead of hiding failures.

## Milestone 1 — local vertical slice

Goal: ingest a few structured sources and produce a Markdown issue.

- [ ] Add a small local Python package under this folder.
- [ ] Read `config/sources.yaml`.
- [ ] Poll RSS/Atom feeds with conditional requests.
- [ ] Parse podcast RSS enclosures and transcript links.
- [ ] Accept URLs from a text file or local HTTP endpoint.
- [ ] Normalize into the content model.
- [ ] Store raw/normalized data under `data/`.
- [ ] Generate Markdown output with source links.
- [ ] Add deterministic deduplication and tests using fixtures.

No browser automation or media download is required to complete this milestone.

## Milestone 2 — reviewable browser capture

- [ ] Add a local browser-capture job around `tools/cdp_capture.py`.
- [ ] Support an allowlist of URLs/domains.
- [ ] Add selector profiles for WeChat, X, Weibo, and Bilibili one platform at a time.
- [ ] Save visible text, metadata, links, and diagnostics.
- [ ] Add a manual pause/stop control.
- [ ] Add per-domain rate limiting and capture quotas.
- [ ] Add tests with saved HTML fixtures, not live scraping in CI.

The initial browser job must capture user-selected pages rather than crawl an entire profile.

## Milestone 3 — Reddit and podcast enrichment

- [ ] Add Reddit OAuth configuration without storing tokens in source files.
- [ ] Add selected subreddit/user polling.
- [ ] Add Podcast Index search and feed resolution.
- [ ] Detect publisher transcripts and timestamped chapters.
- [ ] Add episode-level deduplication.
- [ ] Add transcript search.

## Milestone 4 — optional local media processing

- [ ] Add a queue for explicitly permitted media.
- [ ] Detect captions before attempting ASR.
- [ ] Add `ffmpeg` and Whisper/faster-whisper as optional executables configured by path.
- [ ] Store timestamped segments and model metadata.
- [ ] Add language detection and Chinese/English translation as separate fields.
- [ ] Add manual transcript quality review.

This milestone must remain optional because media downloads can be expensive, slow, copyright-sensitive, or unavailable.

## Milestone 5 — editorial intelligence

- [ ] Topic classification for technology, film, and society.
- [ ] Entity extraction and canonical entity records.
- [ ] Duplicate detection and story clustering.
- [ ] Evidence-backed summaries.
- [ ] Explainable ranking.
- [ ] Accept/reject/defer feedback.
- [ ] Issue preview and revisions.

## Milestone 6 — showcase website

- [ ] Build a static local showcase under `site/`.
- [ ] Show the pipeline diagram and source matrix.
- [ ] Show sample normalized items using synthetic or public reference data.
- [ ] Show evidence links and capture method badges.
- [ ] Show a sample newsletter issue.
- [ ] Keep credentials, cookies, raw private captures, and local browser data outside the public showcase.

## Acceptance criteria for the first usable prototype

A prototype is successful when it can:

1. read at least three RSS feeds;
2. ingest one podcast feed;
3. accept one arbitrary URL;
4. optionally capture one user-selected browser URL over CDP;
5. preserve raw and normalized artifacts separately;
6. produce one Markdown issue with citations;
7. show why each item was selected;
8. retry without duplicating content;
9. run without modifying the host system or installing global software.

## Explicit non-goals for the first release

- universal crawling of every social post;
- bypassing CAPTCHAs, DRM, paywalls, login controls, or geo restrictions;
- collecting private messages or private accounts;
- automatic publishing without review;
- large media archives;
- hidden cookie export;
- claiming that a third-party route is an official API;
- relying on a cloud LLM or paid API without a configuration switch.

#!/usr/bin/env python3
"""Reddit — per-subreddit hot lists, read from the public Atom feeds.

Reddit has no zero-configuration JSON API any more: the anonymous `.json`
endpoints answer 403 and official API registration has been closed to
individual projects since 2025. The **Atom feeds still work without a login**,
which is what this collector uses — `www.reddit.com/r/<sub>/hot/.rss`.

What that costs, measured against the live site:

* A feed carries 25 entries and each one has only a title, a permalink, an
  author and a timestamp. There is no score, no comment count and no body text.
  The `content` element exists but holds boilerplate ("submitted by /u/x [link]
  [comments]"); the useful part of it is the `[link]` target, which is where the
  post actually points.
* Reddit sorts the feed itself, so `hot` is already a ranking and the missing
  score does not matter.
* The rate limit is the real constraint: roughly **one request per minute per
  address**, and it is not lifted by changing the user agent. Requests are
  therefore strictly serial with a delay between subreddits, and this collector
  deliberately does not use the shared parallel helper — doing so would trip the
  limit and get the whole run throttled.

An empty feed body with a 429 is expected while the window resets; it is
reported per subreddit and skipped rather than failing the source.
"""
from __future__ import annotations

import re
import sys
import time

from . import base

SOURCE = "reddit"
NAME = "Reddit"

FEED_URL = "https://www.reddit.com/r/{sub}/{sort}/.rss"

DEFAULT_SUBREDDITS = ["hardware", "technology", "godot"]

# Measured: 15s between requests still produced 429s, 60s was reliable.
DEFAULT_DELAY = 60.0

_LINK = re.compile(r'<a href="([^"]+)">\[link\]</a>')
_TAGS = re.compile(r"<[^>]+>")

def _external_url(content: str) -> str | None:
    """The post's own `[link]` target, when it points somewhere readable.

    Link posts point at the article. Image and video posts point at Reddit's
    own media hosts, and text posts and galleries have no target at all — for
    those the permalink *is* the content, so the item carries that and this
    returns None. Anything still on reddit.com is the permalink in another
    form and is dropped for the same reason.
    """
    match = _LINK.search(content or "")
    if not match:
        return None
    url = match.group(1).strip()
    if not url.startswith(("http://", "https://")):
        return None
    return None if _media_kind(url) else url

def _media_kind(url: str) -> str:
    """Classify a `[link]` target: '' for a real article, else what it is.

    Worth telling apart because a subreddit's character shows in the mix: a
    showcase board is nearly all video, a news board nearly all articles.
    """
    host = base.canonical_key(url).split("/", 1)[0]
    if host == "reddit.com":
        return "gallery" if "/gallery/" in url else "self"
    if host == "v.redd.it":
        return "video"
    if host in ("i.redd.it", "preview.redd.it"):
        return "image"
    return ""

def _summary(content: str) -> str:
    """The feed's content is boilerplate; keep only anything that is not.

    Usually that means an empty string, which is the honest answer — the title
    is the post, and the panel shows the link target's domain beside it.
    """
    text = base.strip_tags(_TAGS.sub(" ", content or ""))
    text = re.sub(r"submitted by\s+/\S+", "", text)
    text = re.sub(r"\[(?:link|comments)\]", "", text)
    return re.sub(r"\s+", " ", text).strip()

def collect(limit: int = 75, subreddits: list[str] | None = None,
            sort: str = "hot", delay: float = DEFAULT_DELAY, **_):
    """Each subreddit contributes its whole feed (25 posts).

    `limit` therefore has to cover every subreddit — it is a ceiling on the
    source, not on one board — which is why it defaults to 25 x 3.
    """
    subs = list(subreddits or DEFAULT_SUBREDDITS)
    items: list[dict] = []
    report: dict[str, dict] = {}

    for index, sub in enumerate(subs):
        if index and delay:
            time.sleep(delay)
        url = FEED_URL.format(sub=sub, sort=sort)
        try:
            raw = base.fetch(url)
            entries = base_parse(raw)
        except base.FetchError as exc:
            print(f"  ! reddit r/{sub} failed: {exc}", file=sys.stderr)
            report[sub] = {"ok": False, "error": str(exc), "fetched": 0}
            continue
        if not entries:
            # A 429 leaves the body empty rather than raising, so an empty parse
            # is most often throttling and is worth saying out loud.
            print(f"  ! reddit r/{sub}: feed empty (rate limited?)", file=sys.stderr)
            report[sub] = {"ok": False, "error": "empty feed (rate limited?)", "fetched": 0}
            continue

        for entry in entries:
            external = _external_url(entry.get("summary_html") or "")
            raw_link = _LINK.search(entry.get("summary_html") or "")
            items.append(base.item(
                SOURCE, NAME, entry["title"],
                entry["link"] or f"https://www.reddit.com/r/{sub}/",
                kind="link",
                published_at=entry.get("published"),
                date_source="rss",
                author=(entry.get("author") or "").lstrip("/u/").lstrip("/") or None,
                summary=_summary(entry.get("summary_html") or ""),
                extra={
                    "collector": "atom_feed",
                    "subreddit": sub,
                    "external_url": external,
                    "domain": base.canonical_key(external).split("/", 1)[0] if external else "",
                    # 'video' / 'image' / 'gallery' / 'self' / '' — the panel
                    # says what kind of post this is instead of showing a media
                    # host as if it were a publication.
                    "media_kind": _media_kind(raw_link.group(1)) if raw_link else "self",
                },
            ))
        report[sub] = {"ok": True, "fetched": len(entries)}

    _LAST_REPORT.clear()
    _LAST_REPORT.update({"subreddits": report, "sort": sort})
    items.sort(key=lambda e: str(e.get("published_at") or ""), reverse=True)
    return items[:limit]

def base_parse(raw: str) -> list[dict]:
    """Parse the feed through the shared extractor.

    Reddit's Atom puts the body in `<content>`, while the shared parser reads
    `<summary>`; feedparser exposes both, so this normalises the one into the
    other rather than teaching the shared parser about Reddit.
    """
    from . import extract as ex

    entries = ex.parse_feed(raw, "modern")
    if entries and not any(e.get("summary_html") for e in entries):
        import feedparser
        for parsed, entry in zip(feedparser.parse(raw).entries, entries):
            content = parsed.get("content") or []
            if content:
                entry["summary_html"] = content[0].get("value") or ""
    return entries

_LAST_REPORT: dict = {}

def last_report() -> dict:
    return dict(_LAST_REPORT)

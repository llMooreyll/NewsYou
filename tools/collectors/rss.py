#!/usr/bin/env python3
"""Generic RSS/Atom source, driven entirely by configuration.

This is the "no code required" path: an ordinary feed needs nothing but an id, a
name and a url in config.toml, and it shows up as its own panel.

    [[feeds]]
    id       = "example-blog"
    name     = "示例博客"
    url      = "https://example.com/feed.xml"
    fulltext = "today"

Sites that need more than a feed — a fallback API when the feed is short, a
non-RSS list page, URL rewriting — keep their own module in tools/collectors/.
Those quirks are logic, not configuration, and pretending otherwise makes both
harder to read. See sspai.py (API fallback) and latepost.py (POST pagination).
"""
from __future__ import annotations

from . import base
from . import feed as feedmod

def collect(limit: int = 20, fulltext=base.FULLTEXT_DEFAULT, engine: str = "legacy",
            *, url: str | None = None, name: str | None = None,
            source: str | None = None, kind: str = "article",
            date_source: str = "rss",
            workers: int = base.DEFAULT_WORKERS, start=base.WINDOW_UNSET, **_):
    """Fetch one configured feed and normalise it into items."""
    if not url:
        raise ValueError("rss source needs a 'url'")
    source = source or name or url
    name = name or source

    items = feedmod.feed_items(url, source, name, engine=engine, kind=kind,
                               date_source=date_source, limit=limit)
    if fulltext != "none" and items:
        feedmod.fetch_bodies(items, engine=engine, fulltext=fulltext,
                             start=start, include_images=True, workers=workers)
    return items

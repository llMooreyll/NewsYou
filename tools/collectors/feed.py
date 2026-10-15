#!/usr/bin/env python3
"""Shared helper for the feed-based sources (少数派 / 极客公园 / 阮一峰).

Each of those sources is "fetch a feed, turn entries into items, optionally
fetch article bodies". That skeleton lives here once; the source modules supply
the feed URL, the full-text policy details and any fallback.
"""
from __future__ import annotations

import sys
import time

from . import base
from .base import FetchError
from . import extract as ex

def feed_items(feed_url: str, source: str, source_name: str, *, engine: str,
               kind: str = "article", date_source: str = "rss",
               link_fix=None, limit: int | None = None) -> list[dict]:
    """Fetch a feed and normalise its entries into items."""
    raw = base.fetch(feed_url)
    items: list[dict] = []
    for entry in ex.parse_feed(raw, engine):
        url = entry["link"]
        if link_fix:
            url = link_fix(url)
        items.append(base.item(
            source, source_name, entry["title"], url, kind=kind,
            published_at=entry.get("published"),
            date_source=date_source,
            summary=entry.get("summary") or "",
            author=entry.get("author"),
            extra={"collector": f"feed:{engine}"},
        ))
        if limit and len(items) >= limit:
            break
    return items

def fetch_bodies(items: list[dict], *, engine: str, fulltext, start=base.WINDOW_UNSET,
                 legacy_marker: str | None = None, sleep: float = 0.4,
                 include_images: bool = False,
                 workers: int = base.DEFAULT_WORKERS) -> None:
    """Fetch and attach article bodies for the items the policy selects.

    Bodies are independent per item, so they are fetched concurrently and
    written back in order. `sleep` still applies, inside each worker, so a
    source is not hammered any harder than it was when this was serial — the
    pause is now overlapped with other fetches instead of adding up.
    """
    targets = list(base.fulltext_targets(items, fulltext, start))

    def body_for(entry: dict) -> dict | None:
        try:
            html = base.fetch(entry["url"])
        except FetchError as exc:
            print(f"  ! body failed {entry['url']}: {exc}", file=sys.stderr)
            return None
        if sleep:
            time.sleep(sleep)
        return ex.extract_article(html, entry["url"], engine,
                                  legacy_marker=legacy_marker,
                                  include_images=include_images)

    for entry, body in zip(targets, base.parallel_map(body_for, targets, workers)):
        if body:
            base.write_body(entry, body, engine=engine,
                            scope=base.fulltext_scope_label(entry))

#!/usr/bin/env python3
"""Hacker News. Firebase API: a story-id list, then one request per story."""
from __future__ import annotations

from . import base
from .base import FetchError

SOURCE = "hackernews"
NAME = "Hacker News"

API = "https://hacker-news.firebaseio.com/v0/{feed}.json"
ITEM = "https://hacker-news.firebaseio.com/v0/item/{id}.json"

def collect(limit: int = 15, feed: str = "topstories",
            workers: int = base.DEFAULT_WORKERS, **_):
    """The story list, then one request per story — those run together.

    The API is one endpoint per item, so fetching them one at a time made this
    the slowest source after Telegram. `parallel_map` keeps the list's order,
    so the board still reads in rank order.
    """
    ids = base.fetch_json(API.format(feed=feed))

    def story(story_id) -> dict | None:
        try:
            record = base.fetch_json(ITEM.format(id=story_id))
        except (FetchError, ValueError):
            return None
        if not record or record.get("type") != "story":
            return None
        return base.item(
            SOURCE, NAME, record.get("title") or "",
            record.get("url") or f"https://news.ycombinator.com/item?id={story_id}",
            kind="link", published_at=record.get("time"), date_source="api",
            author=record.get("by"),
            extra={"collector": "firebase_api", "score": record.get("score"),
                   "comments": record.get("descendants"),
                   "hn_url": f"https://news.ycombinator.com/item?id={story_id}"},
        )

    return [item for item in base.parallel_map(story, ids[:limit], workers) if item]

#!/usr/bin/env python3
"""Hacker News. Firebase API: a story-id list, then one request per story."""
from __future__ import annotations

from . import base
from .base import FetchError

SOURCE = "hackernews"
NAME = "Hacker News"

API = "https://hacker-news.firebaseio.com/v0/{feed}.json"
ITEM = "https://hacker-news.firebaseio.com/v0/item/{id}.json"

def collect(limit: int = 15, feed: str = "topstories", **_):
    ids = base.fetch_json(API.format(feed=feed))
    items: list[dict] = []
    for story_id in ids[:limit]:
        try:
            story = base.fetch_json(ITEM.format(id=story_id))
        except (FetchError, ValueError):
            continue
        if not story or story.get("type") != "story":
            continue
        items.append(base.item(
            SOURCE, NAME, story.get("title") or "",
            story.get("url") or f"https://news.ycombinator.com/item?id={story_id}",
            kind="link", published_at=story.get("time"), date_source="api",
            author=story.get("by"),
            extra={"collector": "firebase_api", "score": story.get("score"),
                   "comments": story.get("descendants"),
                   "hn_url": f"https://news.ycombinator.com/item?id={story_id}"},
        ))
    return items

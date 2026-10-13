#!/usr/bin/env python3
"""知乎热榜.

www.zhihu.com/hot answers 403, so this uses the topstory API. The API returns
question URLs in API form, which are rewritten to the public question URL.
"""
from __future__ import annotations

from . import base

SOURCE = "zhihu"
NAME = "知乎热榜"

API = "https://api.zhihu.com/topstory/hot-list?limit=50"

def collect(limit: int = 15, **_):
    data = base.fetch_json(API)
    items: list[dict] = []
    for index, entry in enumerate(data.get("data", []), 1):
        target = entry.get("target") or {}
        title = target.get("title") or entry.get("title")
        if not title:
            continue
        url = target.get("url") or ""
        if "api.zhihu.com/questions/" in url:
            url = "https://www.zhihu.com/question/" + url.rstrip("/").rsplit("/", 1)[-1]
        items.append(base.item(
            SOURCE, NAME, title, url, kind="hotsearch",
            published_at=target.get("created"), date_source="api",
            summary=target.get("excerpt") or "",
            extra={"collector": "topstory_api", "rank": index,
                   "heat": entry.get("detail_text") or ""},
        ))
        if len(items) >= limit:
            break
    return items

#!/usr/bin/env python3
"""微博热搜. The board has no publication time, so the capture time is used and
date_source records that honestly instead of inventing a date."""
from __future__ import annotations

import urllib.parse

from . import base

SOURCE = "weibo"
NAME = "微博热搜"

API = "https://weibo.com/ajax/side/hotSearch"
REFERER = "https://weibo.com/"

def collect(limit: int = 15, **_):
    data = base.fetch_json(API, referer=REFERER)
    items: list[dict] = []
    for entry in (data.get("data", {}).get("realtime") or []):
        word = entry.get("word")
        if not word:
            continue
        items.append(base.item(
            SOURCE, NAME, word,
            "https://s.weibo.com/weibo?q=" + urllib.parse.quote(f"#{word}#"),
            kind="hotsearch", published_at=None, date_source="collection_time",
            extra={"collector": "ajax_hotsearch",
                   "rank": entry.get("realpos") or len(items) + 1,
                   "heat": entry.get("num"),
                   "label": entry.get("label_name") or entry.get("icon_desc") or ""},
        ))
        if len(items) >= limit:
            break
    return items

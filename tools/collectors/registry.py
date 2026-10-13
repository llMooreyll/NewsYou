#!/usr/bin/env python3
"""The single registry that wires every source module together.

Adding a source is two steps:
  1. create tools/collectors/<name>.py exposing collect(limit, fulltext, engine, **)
  2. add one line to SOURCES below

Nothing else in the pipeline needs to change.
"""
from __future__ import annotations

from . import (anywayfm, geekpark, hackernews, latepost, linuxdo, ruanyifeng,
               rss, sspai, telegram, weibo, zhihu)

# name -> (module, default limit)
SOURCES: dict[str, tuple] = {
    "ruanyifeng": (ruanyifeng, 10),
    "anywayfm": (anywayfm, 10),
    "sspai": (sspai, 20),
    "linuxdo": (linuxdo, 300),
    "latepost": (latepost, 15),
    "geekpark": (geekpark, 15),
    "hackernews": (hackernews, 15),
    "weibo": (weibo, 15),
    "zhihu": (zhihu, 15),
    "telegram": (telegram, 400),
}

# Panel order in the digest. Kept here so the collector, the AI stage and the
# renderer all agree on one source of truth.
ORDER = ["sspai", "telegram", "linuxdo", "latepost", "geekpark", "ruanyifeng",
         "anywayfm", "hackernews", "weibo", "zhihu"]

SOURCE_LABELS = {
    "sspai": "少数派",
    "telegram": "Telegram",
    "linuxdo": "linux.do 晚报",
    "latepost": "晚点 LatePost",
    "geekpark": "极客公园",
    "ruanyifeng": "阮一峰的网络日志",
    "anywayfm": "Anyway.Now 安妮薇看看",
    "hackernews": "Hacker News",
    "weibo": "微博热搜",
    "zhihu": "知乎热榜",
}

# Sources whose `collect` accepts a fulltext policy.
# anywayfm is absent on purpose: /now/ is a list of recommended links and its
# "full text" would be a third-party article reached via a 301 redirect.
FULLTEXT_SOURCES = {"ruanyifeng", "sspai", "latepost", "geekpark"}

def get(name: str):
    return SOURCES[name][0]

def default_limit(name: str) -> int:
    return SOURCES[name][1]

def label(name: str) -> str:
    return SOURCE_LABELS.get(name, name)


# --------------------------------------------------------------------------
# Config-defined generic feeds
# --------------------------------------------------------------------------

# Every [[feeds]] entry in config.toml lands here, keyed by its id. They are
# served by the generic rss module, so adding a feed is a config edit rather
# than a new file.
FEED_CONFIG: dict[str, dict] = {}

def configure_feeds(feeds: list[dict] | None) -> list[str]:
    """Register the configured generic feeds. Returns their ids, in order.

    Called once by the orchestrator after configuration is resolved. Repeating
    it is harmless: entries are replaced, not appended.
    """
    ids: list[str] = []
    for entry in feeds or []:
        name = entry["id"]
        if name in SOURCES and name not in FEED_CONFIG:
            raise ValueError(
                f"feed id {name!r} collides with a built-in source; pick another id")
        SOURCES[name] = (rss, int(entry.get("limit") or 20))
        FEED_CONFIG[name] = entry
        ids.append(name)
    return ids

def feed_config(name: str) -> dict | None:
    return FEED_CONFIG.get(name)

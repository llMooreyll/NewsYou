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
#
# `solidot` is a shipped `[[feeds]]` entry rather than a module, but it is a
# panel like any other, so it is named here too: this list is the default for
# sources.order, and a name missing from it would land at the end of the tab
# row. A name here that is not registered is simply skipped.
ORDER = ["sspai", "telegram", "linuxdo", "latepost", "geekpark", "solidot",
         "ruanyifeng", "anywayfm", "hackernews", "weibo", "zhihu"]

SOURCE_LABELS = {
    "sspai": "少数派",
    "telegram": "Telegram",
    "linuxdo": "linux.do 晚报",
    "latepost": "晚点 LatePost",
    "geekpark": "极客公园",
    "solidot": "奇客 Solidot",
    "ruanyifeng": "阮一峰的网络日志",
    "anywayfm": "Anyway.Now 安妮薇看看",
    "hackernews": "Hacker News",
    "weibo": "微博热搜",
    "zhihu": "知乎热榜",
}

# --------------------------------------------------------------------------
# Information nature
# --------------------------------------------------------------------------

# news       reporting; the news side of stage 2c
# discussion sharing and community threads; the discussion side of stage 2d
# longform   irregularly updated long reads and collections; never classified
#
# collect.py stamps the resolved value on every item. It is deliberately not
# `kind`: `kind` is a rendering concern (board / article / message) and the two
# do not line up — Telegram carries all three natures under one `kind`.
NATURE_VALUES = ("news", "discussion", "longform")

# Source-level nature. A source not listed here is news, so the common case
# needs no configuration at all.
SOURCE_NATURE = {
    "linuxdo": "discussion",
    "hackernews": "discussion",
    "ruanyifeng": "longform",
    "anywayfm": "longform",
}

# Where a source names a sub-unit inside an item. This is a data location, not
# a preference, which is why it lives here and not in config.toml.
UNIT_FIELD = {"telegram": "channel", "linuxdo": "section_name"}

# "<source>:<unit>" -> nature. The shipped default for sources.units; a unit
# missing from it follows its source. Telegram is one source carrying channels
# of all three natures, so almost all of its overrides are here.
UNIT_NATURE = {
    "telegram:reorx_share": "discussion",
    "telegram:notonlyshare": "discussion",
    "telegram:bigwalnut": "discussion",
    "telegram:codedump_notes": "discussion",
    "telegram:hacker_news_zh": "discussion",
    "telegram:gledos_microblogging": "discussion",
    "telegram:wxbyg": "longform",
    "telegram:zhihu_bazaar": "longform",
    # The one linux.do section that paraphrases other people's news; the rest
    # of the issue is the community talking about itself.
    "linuxdo:站外速览": "news",
}

def nature_groups() -> dict[str, list[str]]:
    """SOURCE_NATURE as the config shape, in panel order."""
    out: dict[str, list[str]] = {}
    for name in ORDER:
        value = SOURCE_NATURE.get(name)
        if value:
            out.setdefault(value, []).append(name)
    return out

def source_nature_of(name: str, unit: str | None, *, source_nature: dict,
                     unit_nature: dict) -> str:
    """One item's nature: its unit's override, else its source's, else news."""
    if unit is not None:
        override = unit_nature.get(f"{name}:{unit}")
        if override:
            return str(override)
    return str(source_nature.get(name) or "news")

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

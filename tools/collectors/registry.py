#!/usr/bin/env python3
"""The single registry that wires every source module together.

Adding a source is two steps:
  1. create tools/collectors/<name>.py exposing collect(limit, fulltext, engine, **)
  2. add one line to SOURCES below

Nothing else in the pipeline needs to change.
"""
from __future__ import annotations

from . import (anywayfm, geekpark, hackernews, latepost, linuxdo, reddit,
               ruanyifeng, rss, sspai, telegram, v2ex, weibo, zhihu)

# name -> (module, default limit)
SOURCES: dict[str, tuple] = {
    "ruanyifeng": (ruanyifeng, 10),
    "anywayfm": (anywayfm, 10),
    "sspai": (sspai, 20),
    "linuxdo": (linuxdo, 300),
    "latepost": (latepost, 15),
    "geekpark": (geekpark, 15),
    "hackernews": (hackernews, 15),
    "v2ex": (v2ex, 40),
    "reddit": (reddit, 75),
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
         "ruanyifeng", "anywayfm", "hackernews", "v2ex", "reddit", "weibo", "zhihu"]

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
    "v2ex": "V2EX",
    "reddit": "Reddit",
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
#
# sspai, latepost and geekpark are long-form: a 派早报 story is a day's news
# (see UNIT_NATURE below) but a 晚点 report or a 极客公园 piece is an article,
# not an event, so none of the three belongs in the day's categories.
SOURCE_NATURE = {
    "linuxdo": "discussion",
    "hackernews": "discussion",
    # Both are forums: a thread is a discussion, even when it links to an
    # article, and neither is a day's news wire.
    "v2ex": "discussion",
    "reddit": "discussion",
    "sspai": "longform",
    "latepost": "longform",
    "geekpark": "longform",
    "ruanyifeng": "longform",
    "anywayfm": "longform",
}

# Where a source names a sub-unit inside an item. This is a data location, not
# a preference, which is why it lives here and not in config.toml.
#
# `sspai`'s unit is not a field of the raw data — it is set by the collector
# when it splits 派早报 into per-story items. Everything else in that source is
# long-form, so the split is what decides which items reach classification.
UNIT_FIELD = {"telegram": "channel", "linuxdo": "section_name", "sspai": "column"}

# Sources whose unit is derived by the collector rather than read from the
# source's own data. A unit name here is never a configuration typo, so
# collect.py skips the "sources.units names no item matched" warning for them.
DERIVED_UNITS = {"sspai"}

# "<source>:<unit>" -> nature. The shipped default for sources.units; a unit
# missing from it follows its source. Telegram is one source carrying channels
# of all three natures, so almost all of its overrides are here.
UNIT_NATURE = {
    # 少数派 is long-form; only the stories split out of its daily brief are
    # news. The collector sets `column` on those, so this is what puts them in
    # the classification and leaves the rest of the source out of it.
    "sspai:派早报": "news",
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

# Sources switched off by default. `weibo` is here because its hot-search list
# is noisy and rarely survives a day, but the collector and its board renderer
# both stay in the tree: deleting the name turns the source back on.
# `reddit` is here for a different reason — it works, but the public feeds allow
# roughly one request a minute, so three subreddits cost two minutes of waiting
# on every run. It is switched off until that is worth it, or until there is a
# cheaper way to read it; the collector and its board stay for the same reason
# as weibo's.
DEFAULT_DISABLED = ["weibo", "reddit"]

# Sources whose `collect` accepts a fulltext policy.
# anywayfm is absent on purpose: /now/ is a list of recommended links and its
# "full text" would be a third-party article reached via a 301 redirect.
# v2ex and reddit are absent too: neither offers a body worth fetching (V2EX
# hot rows are titles, Reddit feeds carry no selftext).
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

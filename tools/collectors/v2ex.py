#!/usr/bin/env python3
"""V2EX — the hot list, ordered by reply count.

One request to `/?tab=hot` returns the whole board already sorted by replies,
newest activity first, and each row carries its node (板块), author, reply count
and last-reply time. That is everything the panel needs, so no per-topic request
is made: the site's own ranking is the point of this source.

Two things measured on the live site that shape the code:

* `?tab=hot` is sorted by reply count; `?tab=all` and `?tab=recent` are not.
  Only this tab is usable for a "hottest" board.
* Node pages (`/go/<node>?p=N`) page fine but are sorted by *last reply time*,
  not by reply count — a per-node hot list would mean paging and re-sorting
  locally, and the median topic in a node page has single-digit replies. The
  global tab is both cheaper and more informative, which is why it is what this
  collector uses.
"""
from __future__ import annotations

import re
import sys

from . import base

SOURCE = "v2ex"
NAME = "V2EX"

HOT_URL = "https://www.v2ex.com/?tab=hot"

# The board is a list of `div.cell item` blocks; each row is split on the next.
_ROW_SPLIT = re.compile(r'(?=<div class="cell item")')
# id, reply count (the link's own anchor), title
_TOPIC = re.compile(
    r'<a href="/t/(?P<id>\d+)#reply(?P<replies>\d+)" class="topic-link"'
    r' id="topic-link-\d+">(?P<title>[^<]*)</a>')
_NODE = re.compile(r'<a class="node" href="/go/(?P<name>[a-z0-9]+)">(?P<title>[^<]*)</a>')
_AUTHOR = re.compile(r'<strong><a href="/member/[^"]+">(?P<who>[^<]+)</a></strong>')
# The first timestamp in a row is when the topic was last replied to, which is
# the time a hot list is really about.
_LAST_REPLY = re.compile(r'<span title="(?P<when>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d \+08:00)">')
_VOTES = re.compile(r'class="votes">.*?&nbsp;\s*(\d+)', re.S)

def _int(value, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default

def parse_hot(html: str) -> list[dict]:
    """Parse the hot board. Returns items in the page's own order."""
    items: list[dict] = []
    for row in _ROW_SPLIT.split(html):
        topic = _TOPIC.search(row)
        if not topic:
            continue
        node = _NODE.search(row)
        author = _AUTHOR.search(row)
        when = _LAST_REPLY.search(row)
        votes = _VOTES.search(row)
        topic_id = topic.group("id")
        title = base.strip_tags(topic.group("title")).strip()
        if not title:
            continue
        node_title = base.strip_tags(node.group("title")).strip() if node else ""
        items.append(base.item(
            SOURCE, NAME, title, f"https://www.v2ex.com/t/{topic_id}",
            kind="topic",
            published_at=base.iso_cst(when.group("when")) if when else None,
            # Not the posting time: it is when the topic was last replied to.
            # Naming it honestly keeps `is_today` meaning "active today", which
            # for a reply-count board is the useful reading.
            date_source="last_reply",
            author=author.group("who").strip() if author else None,
            extra={
                "collector": "hot_tab",
                "topic_id": topic_id,
                "node_name": node.group("name") if node else "",
                "node_title": node_title,
                "replies": _int(topic.group("replies")),
                "votes": _int(votes.group(1)) if votes else 0,
            },
        ))
    return items

def collect(limit: int = 40, **_):
    html = base.fetch(HOT_URL)
    items = parse_hot(html)
    if not items:
        print("  ! v2ex: hot board parsed to nothing; the page layout may have "
              "changed", file=sys.stderr)
    # The page is already sorted by reply count; sorting again makes the
    # invariant explicit and survives a page that forgets to.
    items.sort(key=lambda e: e["extra"].get("replies") or 0, reverse=True)
    return items[:limit]

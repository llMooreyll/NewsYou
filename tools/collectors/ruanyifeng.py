#!/usr/bin/env python3
"""阮一峰的网络日志 (ruanyifeng).

Atom feed first; the archives page is used as a fallback so the list is never
short. Archive entries only expose a month, so their date is approximate and
flagged as such.
"""
from __future__ import annotations

import re
import sys
import time

from . import base
from . import extract as ex
from . import feed as feedmod
from .base import FetchError

SOURCE = "ruanyifeng"
NAME = "阮一峰的网络日志"

FEED_URL = "https://www.ruanyifeng.com/blog/atom.xml"
ARCHIVE_URL = "https://www.ruanyifeng.com/blog/archives.html"
LEGACY_MARKER = 'class="asset-content entry-content"'

def _link_fix(url: str) -> str:
    return url.replace("http://www.ruanyifeng.com", "https://www.ruanyifeng.com")

def collect(limit: int = 10, fulltext=base.FULLTEXT_DEFAULT, engine: str = "legacy",
            workers: int = base.DEFAULT_WORKERS, **_):
    items = feedmod.feed_items(FEED_URL, SOURCE, NAME, engine=engine,
                               kind="newsletter", link_fix=_link_fix, limit=limit)

    if len(items) < limit:
        try:
            page = base.fetch(ARCHIVE_URL)
            seen = {base.canonical_key(i["url"]) for i in items}
            for url, text in re.findall(r'href="([^"]+)"[^>]*>(.*?)</a>', page, re.S):
                if not re.search(r"/blog/20\d\d/\d\d/[^\"#]+\.html$", url):
                    continue
                label = base.strip_tags(text)
                if not label or "继续阅读" in label or "留言" in label:
                    continue
                key = base.canonical_key(url)
                if key in seen:
                    continue
                seen.add(key)
                match = re.search(r"/blog/(\d{4})/(\d{2})/", url)
                published = f"{match.group(1)}-{match.group(2)}-01T00:00:00+08:00" if match else None
                items.append(base.item(
                    SOURCE, NAME, label, url, kind="newsletter",
                    published_at=published, date_source="archive_month",
                    extra={"collector": "archives_html", "date_approximate": True},
                ))
                if len(items) >= limit:
                    break
        except FetchError as exc:
            print(f"  ! archives failed: {exc}", file=sys.stderr)

    items = items[:limit]
    _fetch_bodies(items, engine=engine, fulltext=fulltext, workers=workers)
    return items

def _fetch_bodies(items: list[dict], *, engine: str, fulltext,
                  workers: int = base.DEFAULT_WORKERS) -> None:
    targets = list(base.fulltext_targets(items, fulltext))

    def body_for(entry: dict) -> dict | None:
        try:
            html = base.fetch(entry["url"])
        except FetchError as exc:
            print(f"  ! body failed {entry['url']}: {exc}", file=sys.stderr)
            return None
        body = ex.extract_article(html, entry["url"], engine,
                                  legacy_marker=LEGACY_MARKER, include_images=True)
        if body and body.get("text"):
            # the trailing "相关文章" block is site furniture, not content
            body["text"] = re.sub(r"相关文章\s*$", "", body["text"]).strip()
        time.sleep(0.4)
        return body

    for entry, body in zip(targets, base.parallel_map(body_for, targets, workers)):
        if body:
            base.write_body(entry, body, engine=engine,
                            scope=base.fulltext_scope_label(entry))

#!/usr/bin/env python3
"""晚点 LatePost.

The list comes from a form POST to /site/index (an XHR endpoint that needs the
X-Requested-With header). The site serves an incomplete certificate chain, so
requests use base.EXTRA_TRUST (full verification plus Let's Encrypt's
cross-signed Root YR; see base.py). The article header carries the real date and author,
which override the relative date from the list.
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse

from . import base
from . import extract as ex
from .base import FetchError

SOURCE = "latepost"
NAME = "晚点 LatePost"

LIST_URL = "https://www.latepost.com/site/index"
DETAIL_PREFIX = "https://www.latepost.com"
LEGACY_MARKER = 'class="article-body'

def _list(pages: int = 1, per_page: int = 20) -> list[dict]:
    out: list[dict] = []
    for page in range(1, pages + 1):
        body = urllib.parse.urlencode({"page": page, "limit": per_page}).encode()
        raw = base.fetch(LIST_URL, data=body, extra_trust=True, headers={
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Origin": "https://www.latepost.com",
            "Referer": "https://www.latepost.com/",
        }, method="POST")
        rows = json.loads(raw).get("data") or []
        if not rows:
            break
        out.extend(rows)
    return out

def collect(limit: int = 15, fulltext=base.FULLTEXT_DEFAULT, engine: str = "legacy",
            start=None, **_):
    rows = _list(pages=max(1, (limit // 20) + 1), per_page=20)
    now = base.datetime.now(base.CST)
    items: list[dict] = []
    for row in rows[:limit]:
        # The API returns label records ({"label": …, "label_url": …}); the
        # item contract is a list of strings, and str(dict) in the panel would
        # print the whole record as the tag text.
        labels = [x.get("label") for x in (row.get("label") or [])
                  if isinstance(x, dict) and x.get("label")]
        items.append(base.item(
            SOURCE, NAME, row.get("title") or "",
            DETAIL_PREFIX + (row.get("detail_url") or ""), kind="article",
            published_at=base.cn_date_to_iso(row.get("release_time") or "", now),
            date_source="api_relative",
            summary=row.get("abstract") or row.get("intro") or "",
            tags=labels,
            extra={"collector": "site_index_api", "id": row.get("id"),
                   "is_exclusive": row.get("is_dj") == "1"},
        ))

    for entry in base.fulltext_targets(items, fulltext, start):
        scope = base.fulltext_scope_label(entry)
        try:
            detail = base.fetch(entry["url"], extra_trust=True)
        except FetchError as exc:
            print(f"  ! latepost body failed: {exc}", file=sys.stderr)
            continue
        _apply_header(entry, detail)
        body = ex.extract_article(detail, entry["url"], engine, legacy_marker=LEGACY_MARKER)
        # write_body keeps the header date unless the extractor found something
        # strictly more precise (see base._prefer_new_date)
        base.write_body(entry, body, engine=engine, scope=scope)
        time.sleep(0.4)

    return items

def _apply_header(entry: dict, detail: str) -> None:
    """Date and author live in the article header, not in the body."""
    marker = detail.find('class="article-header-a-d"')
    header = base.balanced_div(detail, detail.rfind("<div", 0, marker)) if marker > 0 else ""
    date_el = re.search(r'class="article-header-date"[^>]*>(.*?)</div>', header or detail, re.S)
    published = base.cn_datetime_to_iso(base.strip_tags(date_el.group(1))) if date_el else None
    if published:
        entry["published_at"] = published
        entry["date_source"] = "article_page"
    author = re.search(r'<span class="label">(.*?)</span>', header, re.S)
    if author:
        entry["author"] = base.strip_tags(author.group(1))

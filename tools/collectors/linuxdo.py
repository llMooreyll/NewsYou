#!/usr/bin/env python3
"""linux.do 晚报.

One dated issue page contains several <section id="..."> blocks. Each block is
kept as its own slice of the panel with a section label.

The label is read from the page's own heading. The map below is only a fallback
for when the heading is missing — hardcoding it alone is how the panel ended up
showing "今日要闻" while the site actually says "头条".
"""
from __future__ import annotations

import re

from . import base
from .base import FetchError

SOURCE = "linuxdo"
NAME = "linux.do 晚报"

ISSUES_URL = "https://linuxdo.club/issues/"
ISSUE_URL = "https://linuxdo.club/issues/{date}/"

FALLBACK_SECTION_NAMES = {
    "top-stories": "今日要闻",
    "ai": "AI 前线",
    "dev": "开源开发",
    "bonus": "福利羊毛",
    "chat": "社区闲话",
    "outside": "站外速览",
    "comments": "读者评论",
}

def available_dates() -> list[str]:
    page = base.fetch(ISSUES_URL)
    return sorted(set(re.findall(r"(\d{4}-\d{2}-\d{2})", page)), reverse=True)

def _section_spans(page: str) -> list[tuple[str, str, str]]:
    """Return (section_id, heading, body) for each <section id="..."> block."""
    opens: list[tuple[str, int, int]] = []
    for tag_match in re.finditer(r"<section[^>]*>", page, re.I):
        id_match = re.search(r'\sid="([^"]+)"', tag_match.group(0))
        if id_match:
            opens.append((id_match.group(1), tag_match.start(), tag_match.end()))

    spans: list[tuple[str, str, str]] = []
    for index, (section_id, _start, content_start) in enumerate(opens):
        end = opens[index + 1][1] if index + 1 < len(opens) else len(page)
        body = page[content_start:end]
        heading = ""
        # the backreference must point at the heading level, not the text
        match = re.search(r"<h([12])[^>]*>(.*?)</h\1>", body, re.S)
        if match:
            heading = base.strip_tags(match.group(2))
        spans.append((section_id, heading, body))
    return spans

def collect(limit: int = 300, date: str | None = None, **_):
    if date is None:
        dates = available_dates()
        if not dates:
            raise FetchError("no linuxdo issue dates found")
        date = dates[0]

    page = base.fetch(ISSUE_URL.format(date=date))
    title_match = re.search(r"<title>(.*?)</title>", page, re.S)
    issue_title = base.strip_tags(title_match.group(1)) if title_match else None

    items: list[dict] = []
    for section_id, heading, body in _section_spans(page):
        section_name = heading or FALLBACK_SECTION_NAMES.get(section_id, section_id)
        count_match = re.search(r'<span class="count"[^>]*>(.*?)</span>', body, re.S)
        declared = base.strip_tags(count_match.group(1)) if count_match else ""
        for href, title_html, desc_html in re.findall(
                r'<a class="title"[^>]*href="([^"]+)"[^>]*>(.*?)</a>\s*(?:<p class="desc">(.*?)</p>)?',
                body, re.S):
            title = base.strip_tags(title_html)
            if not title:
                continue
            items.append(base.item(
                SOURCE, NAME, title, href, kind="topic",
                published_at=f"{date}T22:00:00+08:00", date_source="issue_date",
                summary=base.strip_tags(desc_html or ""),
                extra={"collector": "issue_page", "issue_date": date, "issue_title": issue_title,
                       "section": section_id, "section_name": section_name,
                       "section_declared_count": declared,
                       "section_name_source": "page" if heading else "fallback"},
            ))
            if len(items) >= limit:
                return items
    return items[:limit]

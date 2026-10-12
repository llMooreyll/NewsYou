#!/usr/bin/env python3
"""Unified collector for every newsletter source.

Standard library only. No search tools, no installs, fully reproducible.

Sources
-------
  ruanyifeng   阮一峰的网络日志      Atom feed + archives, full text for the latest issue
  anywayfm     Anyway.Now           /now/ news items with absolute dates
  sspai        少数派               all items; 派早报 gets full text, others title+link
  linuxdo      linux.do 晚报         all six sections of one date-based issue
  latepost     晚点 LatePost         JSON list API + article body
  geekpark     极客公园             RSS feed + article body
  hackernews   Hacker News          Firebase API
  weibo        微博热搜             hot search board
  zhihu        知乎热榜             hot list
  telegram     Telegram channels    all messages of the target day, 7 channels

Every item carries a timestamp. When a source genuinely has no publication
time (hot-search boards), `published_at` falls back to the collection time and
`date_source` records that honestly instead of inventing a date.

Usage
-----
    python tools/collect.py --list-sources
    python tools/collect.py --all --outdir data/collected
    python tools/collect.py sspai --limit 20
    python tools/collect.py telegram --day auto
    python tools/collect.py telegram --day 2026-10-12
    python tools/collect.py --dates
"""
from __future__ import annotations

import argparse
import hashlib
import html as html_mod
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from telegram_preview import PreviewError  # noqa: E402
from telegram_preview import fetch as tg_fetch  # noqa: E402
from telegram_preview import parse_posts as tg_parse_posts  # noqa: E402

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
CST = timezone(timedelta(hours=8))
UTC = timezone.utc

TELEGRAM_CHANNELS = [
    "MaiTungNews",
    "tnews365",
    "zaihuapd",
    "xhqcankao",
    "GodlyNews1",
    "NewlearnerChannel",
    "DNSPODT",
]

# latepost serves an incomplete certificate chain, so strict verification fails
# even though the certificate itself is valid and matches the hostname.
RELAXED_TLS = ssl.create_default_context()
RELAXED_TLS.check_hostname = False
RELAXED_TLS.verify_mode = ssl.CERT_NONE


class FetchError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def fetch(url: str, *, referer: str | None = None, timeout: int = 25, retries: int = 2,
          headers: dict | None = None, relaxed_tls: bool = False, data: bytes | None = None,
          method: str | None = None) -> str:
    hdrs = {
        "User-Agent": UA,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml,application/json;q=0.9,*/*;q=0.8",
    }
    if referer:
        hdrs["Referer"] = referer
    if headers:
        hdrs.update(headers)

    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(url, headers=hdrs, data=data, method=method)
            context = RELAXED_TLS if relaxed_tls else None
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                return response.read().decode("utf-8", "replace")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            last = exc
            if attempt < retries:
                time.sleep(1.2 * (attempt + 1))
    raise FetchError(f"{url}: {last}")


def fetch_json(url: str, **kw):
    return json.loads(fetch(url, **kw))


# --------------------------------------------------------------------------
# Text / HTML helpers
# --------------------------------------------------------------------------

def strip_tags(fragment: str) -> str:
    fragment = re.sub(r"<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"</p>", "\n\n", fragment)
    fragment = re.sub(r"<script.*?</script>", "", fragment, flags=re.S)
    fragment = re.sub(r"<style.*?</style>", "", fragment, flags=re.S)
    fragment = re.sub(r"<[^>]+>", "", fragment)
    text = html_mod.unescape(fragment)
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def balanced_div(html: str, start: int) -> str:
    """Return the full <div>...</div> substring starting at `start`."""
    if start < 0 or not html.startswith("<div", start):
        return ""
    tag_re = re.compile(r"<(/?)div\b[^>]*>", re.I)
    depth = 0
    index = start
    while True:
        match = tag_re.search(html, index)
        if not match:
            return html[start:]
        if match.group(1) == "/":
            depth -= 1
            if depth == 0:
                return html[start:match.end()]
        else:
            depth += 1
        index = match.end()


def container_at(html: str, marker: str) -> str:
    """Find a class/id marker, walk back to its opening <div>, return balanced div."""
    pos = html.find(marker)
    if pos < 0:
        return ""
    return balanced_div(html, html.rfind("<div", 0, pos))


def clean_title(text: str) -> str:
    text = re.sub(r"^\s*<!\[CDATA\[", "", (text or "").strip())
    text = re.sub(r"\]\]>\s*$", "", text)
    text = re.sub(r"\s*[\u2197\u2196\u2198\u2199\u2934\u2935\u27a1]+\s*$", "", text.strip())
    return re.sub(r"\s+", " ", text).strip()


TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
                   "spm", "from", "share_token", "ref"}


def canonical_key(url: str) -> str:
    if not url:
        return ""
    parsed = urllib.parse.urlparse(url)
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = (parsed.path or "").rstrip("/")
    query = ""
    if parsed.query:
        pairs = [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
                 if k.lower() not in TRACKING_PARAMS]
        if pairs:
            query = "?" + urllib.parse.urlencode(pairs)
    return f"{host}{path}{query}"


def content_hash(text: str) -> str:
    normalized = re.sub(r"[\s\u3000]+", "", (text or "").lower())
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", "", normalized)
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# Time helpers
# --------------------------------------------------------------------------

def to_cst(value) -> datetime | None:
    dt = parse_dt(value)
    return dt.astimezone(CST) if dt else None


def parse_dt(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=UTC)
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
                "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt.replace(tzinfo=dt.tzinfo or CST)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def iso_cst(value) -> str | None:
    dt = parse_dt(value)
    return dt.astimezone(CST).isoformat() if dt else None


def infer_year(month: int, day: int, reference: datetime) -> int:
    """For sources that omit the year (e.g. '10月10日')."""
    year = reference.year
    if month > reference.month + 1:
        year -= 1
    return year


def cn_date_to_iso(text: str, reference: datetime | None = None) -> str | None:
    """'10 月 10 日' / '2026年10月10日' -> ISO in CST."""
    reference = reference or datetime.now(CST)
    match = re.search(r"(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*日", text or "")
    if not match:
        return None
    year = int(match.group(1)) if match.group(1) else infer_year(int(match.group(2)), int(match.group(3)), reference)
    try:
        return datetime(year, int(match.group(2)), int(match.group(3)), 12, 0, tzinfo=CST).isoformat()
    except ValueError:
        return None


def cn_datetime_to_iso(text: str, reference: datetime | None = None) -> str | None:
    """'10月10日 14:10' -> ISO in CST, preserving the time when present."""
    reference = reference or datetime.now(CST)
    match = re.search(
        r"(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*日(?:\s*(\d{1,2}):(\d{2}))?",
        text or "")
    if not match:
        return None
    year = int(match.group(1)) if match.group(1) else infer_year(int(match.group(2)), int(match.group(3)), reference)
    hour = int(match.group(4)) if match.group(4) else 12
    minute = int(match.group(5)) if match.group(5) else 0
    try:
        return datetime(year, int(match.group(2)), int(match.group(3)), hour, minute, tzinfo=CST).isoformat()
    except ValueError:
        return None


def item(source: str, source_name: str, title: str, url: str, *, kind: str = "article",
         published_at=None, date_source: str = "page", summary: str = "", author=None,
         tags=None, body_text=None, body_html=None, sections=None, extra=None) -> dict:
    record = {
        "source": source,
        "source_name": source_name,
        "kind": kind,
        "title": clean_title(title),
        "url": url,
        "published_at": iso_cst(published_at),
        "date_source": date_source,
        "author": author,
        "summary": (summary or "").strip()[:1200],
        "tags": tags or [],
        "body_text": body_text,
        "body_html": body_html,
        "sections": sections,
        "extra": extra or {},
    }
    record["dedupe_key"] = content_hash(record["title"] + "|" + (record["summary"] or ""))
    record["content_hash"] = content_hash(record["title"])
    return record


def stamp_now(record: dict) -> dict:
    """Guarantee a timestamp; record honestly when it is the capture time."""
    if not record.get("published_at"):
        record["published_at"] = datetime.now(CST).isoformat()
        record["date_source"] = "collection_time"
    return record


# --------------------------------------------------------------------------
# 1. 阮一峰的网络日志
# --------------------------------------------------------------------------

def ruanyifeng_body(url: str) -> str:
    page = fetch(url)
    block = container_at(page, 'class="asset-content entry-content"')
    if not block:
        return ""
    block = re.sub(r"<h2[^>]*>\s*相关文章\s*</h2>.*$", "", block, flags=re.S)
    return strip_tags(block)


def source_ruanyifeng(limit: int = 10, fulltext_latest: bool = True, **_):
    items: list[dict] = []
    try:
        atom = fetch("https://www.ruanyifeng.com/blog/atom.xml")
        for entry in re.findall(r"<entry[\s>].*?</entry>", atom, re.S):
            title = re.search(r"<title[^>]*>(.*?)</title>", entry, re.S)
            link = re.search(r'<link[^>]*href="([^"]+)"', entry)
            updated = re.search(r"<updated>(.*?)</updated>", entry)
            summary = re.search(r"<summary[^>]*>(.*?)</summary>", entry, re.S)
            if not title or not link:
                continue
            items.append(item(
                "ruanyifeng", "阮一峰的网络日志", title.group(1),
                link.group(1).replace("http://www.ruanyifeng.com", "https://www.ruanyifeng.com"),
                kind="newsletter", published_at=updated.group(1) if updated else None,
                date_source="atom", summary=strip_tags(summary.group(1))[:800] if summary else "",
                extra={"collector": "atom"},
            ))
    except FetchError as exc:
        print(f"  ! atom failed: {exc}", file=sys.stderr)

    if len(items) < limit:
        try:
            page = fetch("https://www.ruanyifeng.com/blog/archives.html")
            seen = {canonical_key(i["url"]) for i in items}
            for url, text in re.findall(r'href="([^"]+)"[^>]*>(.*?)</a>', page, re.S):
                if not re.search(r'/blog/20\d\d/\d\d/[^"#]+\.html$', url):
                    continue
                label = strip_tags(text)
                if not label or "继续阅读" in label or "留言" in label:
                    continue
                key = canonical_key(url)
                if key in seen:
                    continue
                seen.add(key)
                match = re.search(r"/blog/(\d{4})/(\d{2})/", url)
                published = f"{match.group(1)}-{match.group(2)}-01T00:00:00+08:00" if match else None
                items.append(item(
                    "ruanyifeng", "阮一峰的网络日志", label, url, kind="newsletter",
                    published_at=published, date_source="archive_month",
                    extra={"collector": "archives_html", "date_approximate": True},
                ))
                if len(items) >= limit:
                    break
        except FetchError as exc:
            print(f"  ! archives failed: {exc}", file=sys.stderr)

    items = items[:limit]
    today = datetime.now(CST).date()

    for index, entry in enumerate(items):
        published = to_cst(entry["published_at"])
        is_today = bool(published and published.date() == today)
        is_latest = index == 0
        if not (is_today or (fulltext_latest and is_latest)):
            continue
        try:
            body = ruanyifeng_body(entry["url"])
        except FetchError as exc:
            print(f"  ! body failed {entry['url']}: {exc}", file=sys.stderr)
            continue
        if body:
            entry["body_text"] = body
            entry["extra"]["body_scope"] = "today" if is_today else "latest"
            entry["extra"]["body_chars"] = len(body)
        time.sleep(0.4)

    return items


# --------------------------------------------------------------------------
# 2. Anyway.Now
# --------------------------------------------------------------------------

def source_anywayfm(limit: int = 10, fulltext_latest: bool = True, **_):
    page = fetch("https://anyway.fm/now/")
    items: list[dict] = []
    # split on the item boundary so each block is self-contained
    parts = re.split(r'(?=<div class="news-item)', page)
    for part in parts:
        id_match = re.search(r'<div class="news-item[^"]*" id="n(\d+)"', part)
        if not id_match:
            continue
        title_match = re.search(r'<div class="news-title">\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', part, re.S)
        if not title_match:
            continue
        desc_match = re.search(r'<div class="news-desc[^"]*">(.*?)</div>', part, re.S)
        author_match = re.search(r'<div class="news-author">(.*?)</div>', part, re.S)
        date_match = re.search(r"data-balloon='([^']+)'", part)
        published = cn_date_to_iso(date_match.group(1)) if date_match else None
        items.append(item(
            "anywayfm", "Anyway.Now 安妮薇看看", title_match.group(2),
            "https://anyway.fm/" + title_match.group(1).lstrip("/"),
            kind="link", published_at=published,
            date_source="relative_balloon" if published else "unknown",
            summary=strip_tags(desc_match.group(1)) if desc_match else "",
            author=strip_tags(author_match.group(1)) if author_match else None,
            extra={"collector": "now_page", "position": len(items) + 1},
        ))
        if len(items) >= limit:
            break

    today = datetime.now(CST).date()
    for index, entry in enumerate(items):
        published = to_cst(entry["published_at"])
        is_today = bool(published and published.date() == today)
        is_latest = index == 0
        if not (is_today or (fulltext_latest and is_latest)):
            continue
        try:
            detail = fetch(entry["url"])
        except FetchError as exc:
            print(f"  ! body failed {entry['url']}: {exc}", file=sys.stderr)
            continue
        block = container_at(detail, 'class="note-content"')
        if block:
            body = strip_tags(block)
            entry["body_text"] = body
            entry["extra"]["body_scope"] = "today" if is_today else "latest"
            entry["extra"]["body_chars"] = len(body)
            heading = re.search(r"<h1[^>]*>(.*?)</h1>", detail, re.S)
            if heading:
                entry["extra"]["original_title"] = strip_tags(heading.group(1))
        time.sleep(0.4)

    return items


# --------------------------------------------------------------------------
# 3. 少数派
# --------------------------------------------------------------------------

def sspai_body(article_id: int) -> dict:
    meta: dict = {}
    try:
        meta = (fetch_json(f"https://sspai.com/api/v1/article/info/get?id={article_id}").get("data") or {})
    except (FetchError, json.JSONDecodeError):
        meta = {}

    page = fetch(f"https://sspai.com/post/{article_id}")
    body_html = container_at(page, 'class="article-body"')

    sections: list[dict] = []
    if body_html:
        headings = [strip_tags(t) for t in re.findall(
            r'<h2[^>]*class="[^"]*post__body__extend__item__title[^"]*"[^>]*>(.*?)</h2>', body_html, re.S)]
        for n, match in enumerate(re.finditer(r'class="[^"]*wangEditor-txt[^"]*"', body_html)):
            block = balanced_div(body_html, body_html.rfind("<div", 0, match.start()))
            sections.append({
                "title": headings[n] if n < len(headings) else None,
                "text": strip_tags(block),
                "html": block,
            })

    author = meta.get("author")
    return {
        "author": author.get("nickname") if isinstance(author, dict) else None,
        "released_at": iso_cst(meta.get("released_time") or meta.get("released_at")),
        "tags": [t.get("title") for t in (meta.get("tags") or []) if isinstance(t, dict)],
        "summary": strip_tags(meta.get("summary") or ""),
        "sections": sections,
        "body_html": body_html,
        "body_text": "\n\n".join((s["title"] + "\n" if s["title"] else "") + s["text"] for s in sections).strip(),
    }


def source_sspai(limit: int = 20, **_):
    """All items. 派早报 gets full text; everything else keeps title + link."""
    items: list[dict] = []
    seen: set[str] = set()

    try:
        feed = fetch("https://sspai.com/feed")
        for entry in re.findall(r"<item>.*?</item>", feed, re.S):
            title = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", entry, re.S)
            link = re.search(r"<link>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</link>", entry, re.S)
            pub = re.search(r"<pubDate>(.*?)</pubDate>", entry, re.S)
            desc = re.search(r"<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>", entry, re.S)
            if not title or not link:
                continue
            url = link.group(1).strip()
            if url in seen:
                continue
            seen.add(url)
            items.append(item(
                "sspai", "少数派", title.group(1), url, kind="article",
                published_at=pub.group(1) if pub else None, date_source="rss",
                summary=strip_tags(desc.group(1))[:900] if desc else "",
                extra={"collector": "rss"},
            ))
    except FetchError as exc:
        print(f"  ! sspai feed failed: {exc}", file=sys.stderr)

    if len(items) < limit:
        try:
            data = fetch_json("https://sspai.com/api/v1/articles?limit=50&offset=0")
            for article in data.get("list", []):
                title = article.get("title") or ""
                if not title:
                    continue
                url = f"https://sspai.com/post/{article['id']}"
                if url in seen:
                    continue
                seen.add(url)
                items.append(item(
                    "sspai", "少数派", title, url, kind="article",
                    published_at=iso_cst(article.get("released_at") or article.get("created_at")),
                    date_source="api", summary=strip_tags(article.get("summary") or "")[:900],
                    extra={"collector": "api_v1", "views": article.get("views_count")},
                ))
                if len(items) >= limit:
                    break
        except (FetchError, json.JSONDecodeError) as exc:
            print(f"  ! sspai api failed: {exc}", file=sys.stderr)

    items.sort(key=lambda x: str(x.get("published_at") or ""), reverse=True)
    items = items[:limit]

    for entry in items:
        if "派早报" not in entry["title"]:
            continue
        try:
            article_id = int(entry["url"].rstrip("/").rsplit("/", 1)[-1])
            body = sspai_body(article_id)
        except (ValueError, FetchError, json.JSONDecodeError) as exc:
            print(f"  ! sspai fulltext failed {entry['url']}: {exc}", file=sys.stderr)
            continue
        entry["author"] = body.get("author") or entry.get("author")
        entry["tags"] = body.get("tags") or entry.get("tags")
        entry["sections"] = body.get("sections")
        entry["body_text"] = body.get("body_text")
        entry["body_html"] = body.get("body_html")
        if body.get("released_at"):
            entry["published_at"] = body["released_at"]
        entry["extra"]["fulltext"] = True
        entry["extra"]["body_chars"] = len(body.get("body_text") or "")
        time.sleep(0.5)

    return items


# --------------------------------------------------------------------------
# 4. linux.do 晚报
# --------------------------------------------------------------------------

LINUXDO_SECTIONS = {
    "top-stories": "今日要闻",
    "ai": "AI 前线",
    "dev": "开源开发",
    "bonus": "福利羊毛",
    "chat": "社区闲话",
    "outside": "站外速览",
    "comments": "读者评论",
}


def linuxdo_available_dates() -> list[str]:
    page = fetch("https://linuxdo.club/issues/")
    return sorted(set(re.findall(r"(\d{4}-\d{2}-\d{2})", page)), reverse=True)


def source_linuxdo(limit: int = 300, date: str | None = None, **_):
    """All sections of one dated issue, kept in one panel with section labels."""
    if date is None:
        dates = linuxdo_available_dates()
        if not dates:
            raise FetchError("no linuxdo issue dates found")
        date = dates[0]

    page = fetch(f"https://linuxdo.club/issues/{date}/")
    issue_title = None
    match = re.search(r"<title>(.*?)</title>", page, re.S)
    if match:
        issue_title = strip_tags(match.group(1))

    # Sections are <section class="section" id="ai"> … </section>, plus a
    # trailing <section class="comments" id="comments"> block. Parse each
    # opening tag's own id, then slice content up to the next section tag.
    opens: list[tuple[str, int, int]] = []
    for tag_match in re.finditer(r"<section[^>]*>", page, re.I):
        id_match = re.search(r'\sid="([^"]+)"', tag_match.group(0))
        if id_match:
            opens.append((id_match.group(1), tag_match.start(), tag_match.end()))

    spans: list[tuple[str, str]] = []
    for index, (section_id, _start, content_start) in enumerate(opens):
        end = opens[index + 1][1] if index + 1 < len(opens) else len(page)
        spans.append((section_id, page[content_start:end]))

    items: list[dict] = []
    for section_id, body in spans:
        section_name = LINUXDO_SECTIONS.get(section_id, section_id)
        count_match = re.search(r'<span class="count"[^>]*>(.*?)</span>', body, re.S)
        declared = strip_tags(count_match.group(1)) if count_match else ""
        for href, title_html, desc_html in re.findall(
                r'<a class="title"[^>]*href="([^"]+)"[^>]*>(.*?)</a>\s*(?:<p class="desc">(.*?)</p>)?',
                body, re.S):
            title = strip_tags(title_html)
            if not title:
                continue
            items.append(item(
                "linuxdo", "linux.do 晚报", title, href, kind="topic",
                published_at=f"{date}T22:00:00+08:00", date_source="issue_date",
                summary=strip_tags(desc_html or ""),
                extra={"collector": "issue_page", "issue_date": date, "issue_title": issue_title,
                       "section": section_id, "section_name": section_name,
                       "section_declared_count": declared},
            ))
            if len(items) >= limit:
                return items
    return items[:limit]


# --------------------------------------------------------------------------
# 5. 晚点 LatePost
# --------------------------------------------------------------------------

def latepost_list(pages: int = 1, per_page: int = 20) -> list[dict]:
    out: list[dict] = []
    for page in range(1, pages + 1):
        body = urllib.parse.urlencode({"page": page, "limit": per_page}).encode()
        raw = fetch("https://www.latepost.com/site/index", data=body, relaxed_tls=True, headers={
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Origin": "https://www.latepost.com",
            "Referer": "https://www.latepost.com/",
        }, method="POST")
        payload = json.loads(raw)
        rows = payload.get("data") or []
        if not rows:
            break
        out.extend(rows)
    return out


def source_latepost(limit: int = 15, fulltext: int = 3, **_):
    rows = latepost_list(pages=max(1, (limit // 20) + 1), per_page=20)
    now = datetime.now(CST)
    items: list[dict] = []
    for row in rows[:limit]:
        url = "https://www.latepost.com" + (row.get("detail_url") or "")
        published = cn_date_to_iso(row.get("release_time") or "", now)
        labels = [x for x in (row.get("label") or []) if x]
        items.append(item(
            "latepost", "晚点 LatePost", row.get("title") or "", url, kind="article",
            published_at=published, date_source="api_relative",
            summary=row.get("abstract") or row.get("intro") or "",
            tags=labels,
            extra={"collector": "site_index_api", "id": row.get("id"), "is_exclusive": row.get("is_dj") == "1"},
        ))

    for entry in items[:max(0, fulltext)]:
        try:
            detail = fetch(entry["url"], relaxed_tls=True)
        except FetchError as exc:
            print(f"  ! latepost body failed: {exc}", file=sys.stderr)
            continue
        # date + author live in the article header, not in the body
        marker = detail.find('class="article-header-a-d"')
        header = balanced_div(detail, detail.rfind("<div", 0, marker)) if marker > 0 else ""
        date_el = re.search(r'class="article-header-date"[^>]*>(.*?)</div>', header or detail, re.S)
        published = cn_datetime_to_iso(strip_tags(date_el.group(1))) if date_el else None
        if published:
            entry["published_at"] = published
            entry["date_source"] = "article_page"
        author = re.search(r'<span class="label">(.*?)</span>', header, re.S)
        if author:
            entry["author"] = strip_tags(author.group(1))
        block = container_at(detail, 'class="article-body')
        if block:
            body = strip_tags(block)
            entry["body_text"] = body
            entry["extra"]["body_chars"] = len(body)
            entry["extra"]["fulltext"] = True
        time.sleep(0.4)

    return items


# --------------------------------------------------------------------------
# 6. 极客公园
# --------------------------------------------------------------------------

def source_geekpark(limit: int = 15, fulltext: int = 3, **_):
    feed = fetch("https://www.geekpark.net/rss")
    items: list[dict] = []
    for entry in re.findall(r"<item>.*?</item>", feed, re.S):
        title = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", entry, re.S)
        link = re.search(r"<link>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</link>", entry, re.S)
        pub = re.search(r"<pubDate>(.*?)</pubDate>", entry, re.S)
        desc = re.search(r"<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>", entry, re.S)
        if not title or not link:
            continue
        items.append(item(
            "geekpark", "极客公园", title.group(1), link.group(1).strip().replace("http://", "https://"),
            kind="article", published_at=pub.group(1) if pub else None, date_source="rss",
            summary=strip_tags(re.sub(r"<!\[CDATA\[|\]\]>", "", desc.group(1)))[:900] if desc else "",
            extra={"collector": "rss"},
        ))
        if len(items) >= limit:
            break

    for entry in items[:max(0, fulltext)]:
        try:
            page = fetch(entry["url"])
        except FetchError as exc:
            print(f"  ! geekpark body failed: {exc}", file=sys.stderr)
            continue
        block = container_at(page, 'class="article-content"')
        if block:
            body = strip_tags(block)
            entry["body_text"] = body
            entry["extra"]["body_chars"] = len(body)
            entry["extra"]["fulltext"] = True
        time.sleep(0.4)

    return items


# --------------------------------------------------------------------------
# 7. Hacker News
# --------------------------------------------------------------------------

def source_hackernews(limit: int = 15, feed: str = "topstories", **_):
    ids = fetch_json(f"https://hacker-news.firebaseio.com/v0/{feed}.json")
    items: list[dict] = []
    for story_id in ids[:limit]:
        try:
            story = fetch_json(f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json")
        except (FetchError, json.JSONDecodeError):
            continue
        if not story or story.get("type") != "story":
            continue
        items.append(item(
            "hackernews", "Hacker News", story.get("title") or "",
            story.get("url") or f"https://news.ycombinator.com/item?id={story_id}",
            kind="link", published_at=story.get("time"), date_source="api",
            author=story.get("by"),
            extra={"collector": "firebase_api", "score": story.get("score"),
                   "comments": story.get("descendants"),
                   "hn_url": f"https://news.ycombinator.com/item?id={story_id}"},
        ))
    return items


# --------------------------------------------------------------------------
# 8. 微博热搜
# --------------------------------------------------------------------------

def source_weibo(limit: int = 15, **_):
    data = fetch_json("https://weibo.com/ajax/side/hotSearch", referer="https://weibo.com/")
    items: list[dict] = []
    for entry in (data.get("data", {}).get("realtime") or []):
        word = entry.get("word")
        if not word:
            continue
        items.append(item(
            "weibo", "微博热搜", word,
            "https://s.weibo.com/weibo?q=" + urllib.parse.quote(f"#{word}#"),
            kind="hotsearch", published_at=None, date_source="collection_time",
            extra={"collector": "ajax_hotsearch", "rank": entry.get("realpos") or len(items) + 1,
                   "heat": entry.get("num"), "label": entry.get("label_name") or entry.get("icon_desc") or ""},
        ))
        if len(items) >= limit:
            break
    return items


# --------------------------------------------------------------------------
# 9. 知乎热榜
# --------------------------------------------------------------------------

def source_zhihu(limit: int = 15, **_):
    data = fetch_json("https://api.zhihu.com/topstory/hot-list?limit=50")
    items: list[dict] = []
    for index, entry in enumerate(data.get("data", []), 1):
        target = entry.get("target") or {}
        title = target.get("title") or entry.get("title")
        if not title:
            continue
        url = target.get("url") or ""
        if "api.zhihu.com/questions/" in url:
            url = "https://www.zhihu.com/question/" + url.rstrip("/").rsplit("/", 1)[-1]
        items.append(item(
            "zhihu", "知乎热榜", title, url, kind="hotsearch",
            published_at=target.get("created"), date_source="api",
            summary=target.get("excerpt") or "",
            extra={"collector": "topstory_api", "rank": index,
                   "heat": entry.get("detail_text") or ""},
        ))
        if len(items) >= limit:
            break
    return items


# --------------------------------------------------------------------------
# 10. Telegram
# --------------------------------------------------------------------------

def resolve_day(day: str, reference: datetime | None = None) -> str | None:
    """'today' | 'yesterday' | 'auto' | 'YYYY-MM-DD' -> YYYY-MM-DD (CST)."""
    reference = reference or datetime.now(CST)
    if day in ("today", "auto"):
        return None if day == "auto" else reference.date().isoformat()
    if day == "yesterday":
        return (reference.date() - timedelta(days=1)).isoformat()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day or ""):
        return day
    return None


def download_telegram_images(items: list[dict], media_root: Path, per_post: int = 2,
                             cap: int = 200, timeout: int = 25, delay: float = 0.3) -> int:
    """Download post photos locally so the digest never depends on a live CDN."""
    from telegram_preview import download_images

    by_channel: dict[str, list[dict]] = {}
    for entry in items:
        by_channel.setdefault(entry["extra"]["channel"], []).append(entry)

    total = 0
    for channel, group in by_channel.items():
        if total >= cap:
            break
        shim = [{"message_id": e["extra"].get("message_id"), "photos": e["extra"].get("photos") or []}
                for e in group]
        try:
            files, _bytes, _skipped = download_images(
                shim, channel, media_root, per_post, cap - total, timeout, delay)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! image download failed for {channel}: {exc}", file=sys.stderr)
            continue
        total += files
        for entry, shim_entry in zip(group, shim):
            entry["extra"]["local_images"] = shim_entry.get("local_images") or []
    return total


def source_telegram(limit: int = 400, day: str = "auto", pages: int = 4,
                    channels: list[str] | None = None, **_):
    channels = channels or TELEGRAM_CHANNELS
    target = resolve_day(day)
    auto = target is None
    collected: list[dict] = []

    for channel in channels:
        cursor: int | None = None
        channel_items: list[dict] = []
        for page_index in range(max(1, pages)):
            url = f"https://t.me/s/{channel}"
            if cursor is not None:
                url += f"?before={cursor}"
            try:
                html = tg_fetch(url, timeout=25)
            except PreviewError as exc:
                print(f"  ! telegram {channel} failed: {exc}", file=sys.stderr)
                break
            posts = tg_parse_posts(html)
            if not posts:
                break
            channel_items.extend(posts)
            numeric = [p["message_id"] for p in posts if isinstance(p["message_id"], int)]
            if not numeric:
                break
            cursor = min(numeric)
            if target is not None:
                oldest = min((to_cst(p.get("published_at")) for p in posts if to_cst(p.get("published_at"))),
                             default=None)
                if oldest and oldest.date() < datetime.fromisoformat(target).date():
                    break
            time.sleep(0.8)

        # pick the day: explicit target, or the newest day present for auto
        day_counts: dict[str, int] = {}
        for post in channel_items:
            dt = to_cst(post.get("published_at"))
            if dt:
                day_counts[dt.date().isoformat()] = day_counts.get(dt.date().isoformat(), 0) + 1
        channel_day = target if target else (max(day_counts) if day_counts else None)

        for post in channel_items:
            dt = to_cst(post.get("published_at"))
            if not dt or not channel_day or dt.date().isoformat() != channel_day:
                continue
            text = post.get("text") or ""
            collected.append(item(
                "telegram", f"Telegram · @{channel}", (text.splitlines() or [""])[0][:120] or f"#{post['message_id']}",
                post.get("permalink") or f"https://t.me/{channel}/{post['message_id']}",
                kind="message", published_at=dt.isoformat(), date_source="post",
                summary=text,
                extra={
                    "collector": "public_preview",
                    "channel": channel,
                    "message_id": post.get("message_id"),
                    "views": post.get("views_text"),
                    "media": post.get("media") or [],
                    "photos": post.get("photos") or [],
                    "reactions": post.get("reactions") or [],
                    "text": text,
                    "day": channel_day,
                },
            ))

    collected.sort(key=lambda x: str(x.get("published_at") or ""), reverse=True)
    if auto and collected:
        print(f"  telegram: auto-selected day {collected[0]['extra'].get('day')}", file=sys.stderr)
    return collected[:limit]


# --------------------------------------------------------------------------
# Post-processing: deterministic pre-dedupe + AI review queue
# --------------------------------------------------------------------------

def dedupe(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """Collapse exact/near duplicates by content hash. Returns (kept, groups)."""
    buckets: dict[str, list[dict]] = {}
    for entry in items:
        buckets.setdefault(entry.get("dedupe_key") or entry["content_hash"], []).append(entry)

    kept: list[dict] = []
    groups: list[dict] = []
    for key, bucket in buckets.items():
        bucket.sort(key=lambda x: str(x.get("published_at") or ""), reverse=True)
        kept.append(bucket[0])
        if len(bucket) > 1:
            groups.append({
                "key": key,
                "count": len(bucket),
                "primary": bucket[0]["url"],
                "duplicates": [{"channel": b["extra"].get("channel"), "url": b["url"],
                                "title": b["title"]} for b in bucket[1:]],
            })
    kept.sort(key=lambda x: str(x.get("published_at") or ""), reverse=True)
    return kept, groups


AI_QUEUE_PROMPT = """You are deduplicating and ranking a day of Telegram news posts
collected from several Chinese-language channels.

Input: a JSON array of items. Each item has `id`, `channel`, `title`, `summary`
(the post text), `published_at` (CST), `url`, and `content_hash`.

Task:
1. CLUSTER items that report the same underlying news event, even when the
   wording, channel, or framing differs.
2. For each cluster choose one `primary` item (the most complete and specific
   version) and list the other item ids as `duplicates`.
3. Merge the facts across the cluster into one neutral `summary` (2-3 sentences,
   no marketing language, no speculation). Keep numbers exact.
4. Assign `topics` from: technology, ai, business, policy, society, security,
   internet, other.
5. Score `importance` 0-100 based on impact and information density, not
   channel popularity.
6. Return JSON only, matching this schema:

{
  "clusters": [
    {
      "primary_id": "...",
      "duplicate_ids": ["..."],
      "title": "neutral headline",
      "summary": "merged neutral summary",
      "topics": ["ai"],
      "importance": 82,
      "sources": ["@zaihuapd", "@tnews365"]
    }
  ]
}

Rules: never invent facts that are not in the input; if two items conflict,
note the conflict in the summary instead of choosing silently; preserve the
original language of the source where possible.
"""


def build_ai_queue(telegram_items: list[dict]) -> dict:
    return {
        "purpose": "Reserved step: AI deduplication and ranking of Telegram messages.",
        "generated_at": datetime.now(CST).isoformat(),
        "model_instructions": AI_QUEUE_PROMPT,
        "count": len(telegram_items),
        "items": [
            {
                "id": f"{e['extra'].get('channel')}:{e['extra'].get('message_id')}",
                "channel": e["extra"].get("channel"),
                "title": e["title"],
                "summary": e["summary"],
                "published_at": e["published_at"],
                "url": e["url"],
                "content_hash": e["content_hash"],
            }
            for e in telegram_items
        ],
    }


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

SOURCES: dict[str, tuple] = {
    "ruanyifeng": (source_ruanyifeng, 10),
    "anywayfm": (source_anywayfm, 10),
    "sspai": (source_sspai, 20),
    "linuxdo": (source_linuxdo, 300),
    "latepost": (source_latepost, 15),
    "geekpark": (source_geekpark, 15),
    "hackernews": (source_hackernews, 15),
    "weibo": (source_weibo, 15),
    "zhihu": (source_zhihu, 15),
    "telegram": (source_telegram, 400),
}

ORDER = ["sspai", "telegram", "linuxdo", "latepost", "geekpark", "ruanyifeng",
         "anywayfm", "hackernews", "weibo", "zhihu"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", nargs="?", choices=sorted(SOURCES))
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--outdir", type=Path, default=Path("data/collected"))
    parser.add_argument("--date", default=None, help="linuxdo issue date")
    parser.add_argument("--day", default="auto", help="telegram day: today | yesterday | auto | YYYY-MM-DD")
    parser.add_argument("--pages", type=int, default=4, help="telegram pages per channel")
    parser.add_argument("--no-fulltext-latest", action="store_true",
                        help="only fetch full text for items published today")
    parser.add_argument("--download-images", action="store_true",
                        help="download telegram post images locally (first N per post)")
    parser.add_argument("--images-per-post", type=int, default=2)
    parser.add_argument("--image-cap", type=int, default=200)
    parser.add_argument("--dates", action="store_true")
    parser.add_argument("--list-sources", action="store_true")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    if args.list_sources:
        for name in ORDER:
            fn, default = SOURCES[name]
            print(f"{name:<12} limit={default:<4} {(fn.__doc__ or '').strip().splitlines()[0]}")
        return 0

    if args.dates:
        for value in linuxdo_available_dates():
            print(value)
        return 0

    if not args.source and not args.all:
        parser.error("choose a source, or pass --all")

    targets = ORDER if args.all else [args.source]
    results: dict[str, list[dict]] = {}
    failures: dict[str, str] = {}

    for name in targets:
        print(f"→ {name}", file=sys.stderr)
        started = time.time()
        try:
            kwargs: dict = {}
            if name == "linuxdo" and args.date:
                kwargs["date"] = args.date
            if name == "telegram":
                kwargs["day"] = args.day
                kwargs["pages"] = args.pages
            if name in ("ruanyifeng", "anywayfm") and args.no_fulltext_latest:
                kwargs["fulltext_latest"] = False
            items = SOURCES[name][0](args.limit if args.limit is not None else SOURCES[name][1], **kwargs)
            for entry in items:
                stamp_now(entry)
            if name == "telegram":
                items, groups = dedupe(items)
                results["telegram_duplicates"] = []
                (args.outdir).mkdir(parents=True, exist_ok=True)
                if args.download_images:
                    saved = download_telegram_images(
                        items, Path("data/telegram/media"), args.images_per_post, args.image_cap)
                    print(f"  telegram images saved: {saved}", file=sys.stderr)
                (args.outdir / "telegram_ai_queue.json").write_text(
                    json.dumps(build_ai_queue(items), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                (args.outdir / "telegram_duplicate_groups.json").write_text(
                    json.dumps({"generated_at": datetime.now(CST).isoformat(), "groups": groups},
                               ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            results[name] = items
            print(f"  {len(items)} items in {time.time() - started:.1f}s", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            failures[name] = f"{type(exc).__name__}: {exc}"
            print(f"  FAILED: {failures[name]}", file=sys.stderr)

    if args.list:
        for name in targets:
            if name not in results:
                continue
            print(f"\n=== {name} ({len(results[name])}) ===")
            for entry in results[name][:20]:
                when = (entry.get("published_at") or "")[:16].replace("T", " ")
                extra = entry.get("extra") or {}
                bits = [when, entry.get("date_source", "")]
                if extra.get("rank"):
                    bits.append(f"#{extra['rank']}")
                if extra.get("heat"):
                    bits.append(str(extra["heat"])[:12])
                if extra.get("score"):
                    bits.append(f"{extra['score']}pts")
                if entry.get("body_text"):
                    bits.append(f"{len(entry['body_text'])}字")
                print(f"  {' · '.join(bits)}")
                print(f"    {entry['title'][:88]}")
                print(f"    {entry['url']}")
        if failures:
            print("\nfailures:")
            for name, err in failures.items():
                print(f"  {name}: {err}")
        return 0

    args.outdir.mkdir(parents=True, exist_ok=True)
    index = {"collected_at": datetime.now(CST).isoformat(), "sources": {}}
    for name in targets:
        if name not in results:
            continue
        path = args.outdir / f"{name}.json"
        path.write_text(json.dumps({
            "source": name,
            "collected_at": datetime.now(CST).isoformat(),
            "count": len(results[name]),
            "items": results[name],
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        index["sources"][name] = {"file": path.name, "count": len(results[name]), "ok": True}
        print(f"  wrote {path}", file=sys.stderr)

    for name, err in failures.items():
        index["sources"][name] = {"file": None, "count": 0, "ok": False, "error": err}

    (args.outdir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.outdir / "index.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

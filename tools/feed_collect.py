#!/usr/bin/env python3
"""Unified feed/API collector for the newsletter pipeline.

Standard library only. No search tools, no external services, no installs.
Every source is a declared adapter with a fetch strategy, so the whole flow is
reproducible from one command.

Sources
-------
  ruanyifeng   Atom feed (3 entries) with archives.html fallback for 20 posts
  anywayfm     https://anyway.fm/now/ link list (JSON-LD / anchor scrape)
  sspai        official feed + JSON API, filtered to 派早报 (morning paper)
  linuxdo      linuxdo.club date-based issue pages, dates discovered from /issues/
  hackernews   official Firebase API (topstories + item detail)
  weibo        weibo.com/ajax/side/hotSearch hot search board
  zhihu        api.zhihu.com/topstory/hot-list hot list

Usage
-----
    python tools/feed_collect.py --all --outdir data/collected
    python tools/feed_collect.py ruanyifeng --limit 10
    python tools/feed_collect.py linuxdo --date 2026-10-11
    python tools/feed_collect.py hackernews --limit 10
    python tools/feed_collect.py --list-sources

Output: one JSON file per source plus an index.json summary.
"""
from __future__ import annotations

import argparse
import html as html_mod
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
CST = timezone(timedelta(hours=8))
NOW = lambda: datetime.now(timezone.utc)


class FetchError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def fetch(url: str, *, referer: str | None = None, timeout: int = 25, retries: int = 2,
          headers: dict | None = None) -> str:
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
            with urllib.request.urlopen(urllib.request.Request(url, headers=hdrs), timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            last = exc
            if attempt < retries:
                time.sleep(1.2 * (attempt + 1))
    raise FetchError(f"{url}: {last}")


def fetch_json(url: str, **kw):
    return json.loads(fetch(url, **kw))


def strip_tags(fragment: str) -> str:
    fragment = re.sub(r"<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"</p>", "\n\n", fragment)
    fragment = re.sub(r"<[^>]+>", "", fragment)
    text = html_mod.unescape(fragment)
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def iso(ts) -> str | None:
    if ts is None:
        return None
    try:
        value = float(ts)
    except (TypeError, ValueError):
        return str(ts)
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "spm", "from", "share_token"}


def canonical_key(url: str) -> str:
    """Scheme/www/tracking-insensitive key.

    The query string is preserved (it is meaningful for search URLs) minus
    common tracking parameters, so http/https and www duplicates still collapse.
    """
    if not url:
        return ""
    parsed = urllib.parse.urlparse(url)
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = (parsed.path or "").rstrip("/")
    query = ""
    if parsed.query:
        pairs = [
            (k, v)
            for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            if k.lower() not in TRACKING_PARAMS
        ]
        if pairs:
            query = "?" + urllib.parse.urlencode(pairs)
    return f"{host}{path}{query}"


def clean_title(text: str) -> str:
    """Strip stray external-link markers and whitespace from scraped titles."""
    return re.sub(r"\s*[\u2197\u2196\u2198\u2199\u2934\u2935\u27a1]+\s*$", "", (text or "").strip()).strip()


# --------------------------------------------------------------------------
# Minimal XML / Atom parsing (stdlib only)
# --------------------------------------------------------------------------

def xml_blocks(xml: str, tag: str) -> list[str]:
    return re.findall(rf"<{tag}[\s>].*?</{tag}>", xml, re.S)


def xml_first(block: str, tag: str) -> str | None:
    m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", block, re.S)
    if not m:
        return None
    value = m.group(1).strip()
    value = re.sub(r"^<!\[CDATA\[(.*?)\]\]>$", r"\1", value, flags=re.S)
    return value


def xml_attr(block: str, tag: str, attr: str) -> str | None:
    m = re.search(rf"<{tag}[^>]*\s{attr}=\"([^\"]*)\"", block, re.S)
    return m.group(1) if m else None


# --------------------------------------------------------------------------
# Source: 阮一峰的网络日志
# --------------------------------------------------------------------------

def source_ruanyifeng(limit: int) -> list[dict]:
    """Atom feed gives the newest few; archives.html gives the last 20."""
    items: list[dict] = []
    try:
        atom = fetch("https://www.ruanyifeng.com/blog/atom.xml")
        for entry in xml_blocks(atom, "entry"):
            title = xml_first(entry, "title")
            link = xml_attr(entry, "link", "href") or xml_first(entry, "link")
            if not title or not link:
                continue
            items.append({
                "title": strip_tags(title),
                "url": link.replace("http://www.ruanyifeng.com", "https://www.ruanyifeng.com"),
                "published_at": xml_first(entry, "updated") or xml_first(entry, "published"),
                "summary": strip_tags(xml_first(entry, "summary") or "")[:600],
                "source": "ruanyifeng",
                "source_name": "阮一峰的网络日志",
                "collector": "atom",
            })
    except FetchError as exc:
        print(f"  ! atom failed ({exc}); using archives only", file=sys.stderr)

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
                m = re.search(r"/blog/(\d{4})/(\d{2})/", url)
                published = f"{m.group(1)}-{m.group(2)}-01T00:00:00+08:00" if m else None
                items.append({
                    "title": label,
                    "url": url,
                    "published_at": published,
                    "summary": "",
                    "source": "ruanyifeng",
                    "source_name": "阮一峰的网络日志",
                    "collector": "archives_html",
                })
        except FetchError as exc:
            print(f"  ! archives failed: {exc}", file=sys.stderr)

    return items[:limit]


# --------------------------------------------------------------------------
# Source: Anyway.Now
# --------------------------------------------------------------------------

def source_anywayfm(limit: int) -> list[dict]:
    page = fetch("https://anyway.fm/now/")
    items: list[dict] = []
    seen: set[str] = set()
    for link_id, text in re.findall(r'<a[^>]+href="links/(\d+)"[^>]*>(.*?)</a>', page, re.S):
        if link_id in seen:
            continue
        seen.add(link_id)
        title = strip_tags(text)
        if not title:
            continue
        # external target, when present, is the real source
        external = re.search(rf'href="links/{link_id}"[^>]*>.*?</a>', page, re.S)
        items.append({
            "title": title,
            "url": f"https://anyway.fm/links/{link_id}",
            "external_url": None,
            "published_at": None,
            "summary": "",
            "source": "anywayfm",
            "source_name": "Anyway.Now 安妮薇看看",
            "collector": "now_page",
            "position": len(items) + 1,
        })
    return items[:limit]


# --------------------------------------------------------------------------
# Source: 少数派 派早报
# --------------------------------------------------------------------------

def source_sspai(limit: int, keyword: str = "派早报", fulltext: bool = False) -> list[dict]:
    """少数派 feed first (fast, dated), then the JSON API to widen the search."""
    items: list[dict] = []
    seen: set[str] = set()

    try:
        feed = fetch("https://sspai.com/feed")
        for entry in xml_blocks(feed, "item"):
            title = strip_tags(xml_first(entry, "title") or "")
            url = xml_first(entry, "link") or ""
            if not title or keyword not in title:
                continue
            if url in seen:
                continue
            seen.add(url)
            items.append({
                "title": title,
                "url": url,
                "published_at": xml_first(entry, "pubDate"),
                "summary": strip_tags(xml_first(entry, "description") or "")[:800],
                "source": "sspai",
                "source_name": "少数派 · 派早报",
                "collector": "feed",
            })
    except FetchError as exc:
        print(f"  ! sspai feed failed: {exc}", file=sys.stderr)

    if len(items) < limit:
        try:
            data = fetch_json("https://sspai.com/api/v1/articles?limit=50&offset=0")
            for article in data.get("list", []):
                title = article.get("title") or ""
                if keyword not in title:
                    continue
                url = f"https://sspai.com/post/{article['id']}"
                if url in seen:
                    continue
                seen.add(url)
                items.append({
                    "title": title,
                    "url": url,
                    "published_at": iso(article.get("released_at") or article.get("created_at")),
                    "summary": strip_tags(article.get("summary") or "")[:800],
                    "source": "sspai",
                    "source_name": "少数派 · 派早报",
                    "collector": "api_v1",
                    "views": article.get("views_count"),
                })
        except (FetchError, json.JSONDecodeError) as exc:
            print(f"  ! sspai api failed: {exc}", file=sys.stderr)

    items.sort(key=lambda x: str(x.get("published_at") or ""), reverse=True)
    items = items[:limit]

    if fulltext:
        for item in items:
            try:
                article_id = int(item["url"].rstrip("/").rsplit("/", 1)[-1])
                body = sspai_article_body(article_id)
            except (ValueError, FetchError, json.JSONDecodeError) as exc:
                print(f"  ! fulltext failed for {item['url']}: {exc}", file=sys.stderr)
                continue
            item["author"] = body.get("author")
            item["tags"] = body.get("tags")
            item["sections"] = body.get("sections")
            item["body_text"] = body.get("body_text")
            item["body_chars"] = len(body.get("body_text") or "")
            if body.get("released_at"):
                item["published_at"] = body["released_at"]
            item["collector"] = item.get("collector", "feed") + "+fulltext"
            time.sleep(0.5)

    return items


def balanced_div(html: str, start: int) -> str:
    """Return the full <div>...</div> substring starting at index `start`."""
    if start < 0 or not html.startswith("<div", start):
        return ""
    tag_re = re.compile(r"<(/?)div\b[^>]*>", re.I)
    depth = 0
    index = start
    while True:
        m = tag_re.search(html, index)
        if not m:
            return html[start:]
        if m.group(1) == "/":
            depth -= 1
            if depth == 0:
                return html[start:m.end()]
        else:
            depth += 1
        index = m.end()


def sspai_article_body(article_id: int) -> dict:
    """Full text of one 少数派 article.

    The JSON info endpoint returns metadata but an empty `body` for
    non-members, so the article HTML page is used for the text. Each 派早报
    news item is a `post__body__extend__item` block with a heading and a
    `wangEditor-txt` content div.
    """
    meta: dict = {}
    try:
        meta = (fetch_json(f"https://sspai.com/api/v1/article/info/get?id={article_id}").get("data") or {})
    except (FetchError, json.JSONDecodeError):
        meta = {}

    page = fetch(f"https://sspai.com/post/{article_id}")
    body_html = ""
    marker = page.find('class="article-body"')
    if marker > 0:
        body_html = balanced_div(page, page.rfind("<div", 0, marker))

    sections: list[dict] = []
    if body_html:
        headings = [strip_tags(t) for t in re.findall(
            r'<h2[^>]*class="[^"]*post__body__extend__item__title[^"]*"[^>]*>(.*?)</h2>',
            body_html, re.S)]
        content_positions = [m.start() for m in re.finditer(r'class="[^"]*wangEditor-txt[^"]*"', body_html)]
        for n, pos in enumerate(content_positions):
            block = balanced_div(body_html, body_html.rfind("<div", 0, pos))
            sections.append({
                "title": headings[n] if n < len(headings) else None,
                "text": strip_tags(block),
                "html": block,
            })

    return {
        "id": article_id,
        "title": meta.get("title") or (sections[0]["title"] if sections else None),
        "author": ((meta.get("author") or {}).get("nickname") if isinstance(meta.get("author"), dict) else None),
        "released_at": iso(meta.get("released_time") or meta.get("released_at")),
        "url": f"https://sspai.com/post/{article_id}",
        "tags": [t.get("title") for t in (meta.get("tags") or []) if isinstance(t, dict)],
        "summary": strip_tags(meta.get("summary") or ""),
        "sections": sections,
        "body_html": body_html,
        "body_text": "\n\n".join(
            (s["title"] + "\n" if s["title"] else "") + s["text"] for s in sections
        ).strip(),
    }


# --------------------------------------------------------------------------
# Source: linux.do 晚报 (date-based URLs)
# --------------------------------------------------------------------------

def linuxdo_available_dates() -> list[str]:
    """Read the archive index so we never hard-code a date."""
    page = fetch("https://linuxdo.club/issues/")
    dates = set(re.findall(r"(\d{4}-\d{2}-\d{2})", page))
    return sorted(dates, reverse=True)


def linuxdo_issue_url(date: str) -> str:
    return f"https://linuxdo.club/issues/{date}/"


def source_linuxdo(limit: int, date: str | None = None) -> list[dict]:
    if date is None:
        dates = linuxdo_available_dates()
        if not dates:
            raise FetchError("no issue dates found in the archive index")
        date = dates[0]

    page = fetch(linuxdo_issue_url(date))
    issue_title = None
    m = re.search(r"<title>(.*?)</title>", page, re.S)
    if m:
        issue_title = strip_tags(m.group(1))

    sections = re.findall(r'<h2[^>]*id="([^"]+)"[^>]*>(.*?)</h2>', page, re.S)
    current = "top"
    section_titles = {sid: strip_tags(txt) for sid, txt in sections}

    items: list[dict] = []
    # split the page by section headings, then pull <a class="title"> inside each
    parts = re.split(r'<h2[^>]*id="[^"]+"[^>]*>.*?</h2>', page, flags=re.S)
    for index, part in enumerate(parts):
        if index < len(sections):
            current = sections[index][0]
        for href, title_html in re.findall(
            r'<a class="title"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', part, re.S
        ):
            title = clean_title(strip_tags(title_html))
            if not title:
                continue
            desc = ""
            dm = re.search(r'<p class="desc">(.*?)</p>', part, re.S)
            if dm:
                desc = strip_tags(dm.group(1))[:600]
            items.append({
                "title": title,
                "url": href,
                "published_at": f"{date}T22:00:00+08:00",
                "summary": desc,
                "source": "linuxdo",
                "source_name": "linux.do 晚报",
                "collector": "issue_page",
                "issue_date": date,
                "issue_title": issue_title,
                "section": current,
                "section_name": section_titles.get(current, current),
            })
            if len(items) >= limit:
                return items
    return items


# --------------------------------------------------------------------------
# Source: Hacker News
# --------------------------------------------------------------------------

def source_hackernews(limit: int, feed: str = "topstories") -> list[dict]:
    ids = fetch_json(f"https://hacker-news.firebaseio.com/v0/{feed}.json")
    items: list[dict] = []
    for story_id in ids[:limit]:
        try:
            item = fetch_json(f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json")
        except (FetchError, json.JSONDecodeError):
            continue
        if not item or item.get("type") != "story":
            continue
        items.append({
            "title": item.get("title"),
            "url": item.get("url") or f"https://news.ycombinator.com/item?id={story_id}",
            "hn_url": f"https://news.ycombinator.com/item?id={story_id}",
            "published_at": iso(item.get("time")),
            "summary": strip_tags(item.get("text") or "")[:600],
            "source": "hackernews",
            "source_name": "Hacker News",
            "collector": "firebase_api",
            "score": item.get("score"),
            "comments": item.get("descendants"),
            "author": item.get("by"),
        })
    return items


# --------------------------------------------------------------------------
# Source: 微博热搜
# --------------------------------------------------------------------------

def source_weibo(limit: int) -> list[dict]:
    data = fetch_json("https://weibo.com/ajax/side/hotSearch", referer="https://weibo.com/")
    items: list[dict] = []
    for entry in (data.get("data", {}).get("realtime") or []):
        word = entry.get("word")
        if not word:
            continue
        rank = entry.get("realpos") or (len(items) + 1)
        items.append({
            "rank": rank,
            "title": word,
            "url": "https://s.weibo.com/weibo?q=" + urllib.parse.quote(f"#{word}#"),
            "published_at": None,
            "summary": entry.get("note") or "",
            "source": "weibo",
            "source_name": "微博热搜",
            "collector": "ajax_hotsearch",
            "heat": entry.get("num"),
            "label": entry.get("label_name") or entry.get("icon_desc") or "",
        })
        if len(items) >= limit:
            break
    return items


# --------------------------------------------------------------------------
# Source: 知乎热榜
# --------------------------------------------------------------------------

def source_zhihu(limit: int) -> list[dict]:
    data = fetch_json("https://api.zhihu.com/topstory/hot-list?limit=50")
    items: list[dict] = []
    for index, entry in enumerate(data.get("data", []), 1):
        target = entry.get("target") or {}
        title = target.get("title") or entry.get("title")
        if not title:
            continue
        url = target.get("url") or ""
        if "api.zhihu.com/questions/" in url:
            question_id = url.rstrip("/").rsplit("/", 1)[-1]
            url = f"https://www.zhihu.com/question/{question_id}"
        detail = entry.get("detail_text") or ""
        items.append({
            "rank": index,
            "title": title,
            "url": url,
            "published_at": iso(target.get("created")) if target.get("created") else None,
            "summary": detail,
            "source": "zhihu",
            "source_name": "知乎热榜",
            "collector": "topstory_api",
            "heat": detail,
            "excerpt": target.get("excerpt") or "",
        })
        if len(items) >= limit:
            break
    return items


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

SOURCES = {
    "ruanyifeng": (source_ruanyifeng, 10),
    "anywayfm": (source_anywayfm, 10),
    "sspai": (source_sspai, 1),
    "linuxdo": (source_linuxdo, 10),
    "hackernews": (source_hackernews, 10),
    "weibo": (source_weibo, 10),
    "zhihu": (source_zhihu, 10),
}


def run_source(name: str, limit: int | None, **kw) -> list[dict]:
    fn, default_limit = SOURCES[name]
    items = fn(limit if limit is not None else default_limit, **kw)
    deduped: list[dict] = []
    seen: set[str] = set()
    for item in items:
        key = canonical_key(item.get("url") or "") or str(item.get("title"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", nargs="?", choices=sorted(SOURCES), help="which source to collect")
    parser.add_argument("--all", action="store_true", help="collect every source")
    parser.add_argument("--limit", type=int, default=None, help="items per source")
    parser.add_argument("--date", default=None, help="linuxdo issue date, e.g. 2026-10-11")
    parser.add_argument("--fulltext", action="store_true", help="fetch article bodies where supported (sspai)")
    parser.add_argument("--dates", action="store_true", help="list available linuxdo issue dates and exit")
    parser.add_argument("--outdir", type=Path, default=Path("data/collected"))
    parser.add_argument("--list-sources", action="store_true")
    parser.add_argument("--list", action="store_true", help="print a readable table instead of JSON")
    args = parser.parse_args()

    if args.list_sources:
        for name, (fn, default) in SOURCES.items():
            print(f"{name:<12} default_limit={default:<3} {(fn.__doc__ or '').strip().splitlines()[0]}")
        return 0

    if args.dates:
        for d in linuxdo_available_dates():
            print(d)
        return 0

    if not args.source and not args.all:
        parser.error("choose a source, or pass --all")

    targets = sorted(SOURCES) if args.all else [args.source]
    results: dict[str, list[dict]] = {}
    failures: dict[str, str] = {}

    for name in targets:
        print(f"→ {name}", file=sys.stderr)
        started = time.time()
        try:
            kw: dict = {}
            if name == "linuxdo" and args.date:
                kw["date"] = args.date
            if name == "sspai" and args.fulltext:
                kw["fulltext"] = True
            items = run_source(name, args.limit, **kw)
            results[name] = items
            print(f"  {len(items)} items in {time.time() - started:.1f}s", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - one source must not kill the run
            failures[name] = f"{type(exc).__name__}: {exc}"
            print(f"  FAILED: {failures[name]}", file=sys.stderr)

    if args.list:
        for name, items in results.items():
            print(f"\n=== {name} ({len(items)}) ===")
            for i, item in enumerate(items, 1):
                meta = []
                if item.get("rank"):
                    meta.append(f"#{item['rank']}")
                if item.get("score"):
                    meta.append(f"{item['score']} pts")
                if item.get("comments"):
                    meta.append(f"{item['comments']} comments")
                if item.get("heat"):
                    meta.append(f"heat {item['heat']}")
                if item.get("published_at"):
                    meta.append(str(item["published_at"])[:19])
                suffix = ("  [" + " · ".join(map(str, meta)) + "]") if meta else ""
                print(f"  {i:>2}. {item.get('title')}{suffix}")
                print(f"      {item.get('url')}")
        if failures:
            print("\nfailures:")
            for name, err in failures.items():
                print(f"  {name}: {err}")
        return 0

    args.outdir.mkdir(parents=True, exist_ok=True)
    index = {
        "collected_at": NOW().isoformat(),
        "sources": {},
    }
    for name, items in results.items():
        path = args.outdir / f"{name}.json"
        payload = {
            "source": name,
            "collected_at": NOW().isoformat(),
            "count": len(items),
            "items": items,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        index["sources"][name] = {"file": path.name, "count": len(items), "ok": True}
        print(f"  wrote {path}", file=sys.stderr)

    for name, err in failures.items():
        index["sources"][name] = {"file": None, "count": 0, "ok": False, "error": err}

    (args.outdir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(args.outdir / "index.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

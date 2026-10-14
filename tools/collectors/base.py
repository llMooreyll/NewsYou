#!/usr/bin/env python3
"""Shared foundation for every collector.

This module holds everything that is **not** site-specific: HTTP with retries,
time normalisation, HTML text helpers, the item schema, the full-text policy,
freshness status and deduplication.

Each source lives in its own sibling module (``sspai.py``, ``telegram.py``, …)
and imports only from here. Adding a source therefore means adding one file and
one registry line, never touching this module.

Standard library only. The optional heavy dependencies (trafilatura,
feedparser) are imported lazily by ``extract.py`` and only when the modern
engine is selected.
"""
from __future__ import annotations

import hashlib
import html as html_mod
import json
import re
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")
CST = timezone(timedelta(hours=8))
UTC = timezone.utc

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Extra trust for sites whose chain ends at a root that system stores do not
# carry yet. latepost uses Let's Encrypt's "Generation Y" chain (leaf -> YR1 ->
# Root YR); Root YR is not in most trust stores or certifi yet. The bundled file
# is the official cross-sign of Root YR by ISRG Root X1, so verification stays
# fully on (hostname + chain) and is anchored at the already-trusted X1.
#   source:  https://letsencrypt.org/certs/gen-y/root-yr-by-x1.pem
#   sha256:  07:26:39:D0:B1:40:D5:BF:FA:E1:6A:D9:C3:F6:CC:60:86:04:06:21:F5:1E:E6:1A:6D:46:A8:91:5C:07:CF:76
#   expires: 2032-09-02
# Delete it once Root YR ships in system stores.
EXTRA_CA_FILES = [Path(__file__).resolve().parent / "certs" / "isrg-root-yr-by-x1.pem"]

def extra_trust_context() -> ssl.SSLContext:
    """Default verification plus EXTRA_CA_FILES. Never disables checks."""
    context = ssl.create_default_context()
    for path in EXTRA_CA_FILES:
        if path.exists():
            context.load_verify_locations(cafile=str(path))
    # the bundled cert is an intermediate (cross-signed), so it has to be
    # usable as a chain anchor; the default on Python 3.13+, explicit here
    context.verify_flags |= getattr(ssl, "VERIFY_X509_PARTIAL_CHAIN", 0)
    return context

EXTRA_TRUST = extra_trust_context()

class FetchError(RuntimeError):
    pass

# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def fetch(url: str, *, referer: str | None = None, timeout: int = 25, retries: int = 2,
          headers: dict | None = None, extra_trust: bool = False, data: bytes | None = None,
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
            context = EXTRA_TRUST if extra_trust else None
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
# Concurrency
# --------------------------------------------------------------------------

DEFAULT_WORKERS = 4

def parallel_map(fn, items, workers: int = DEFAULT_WORKERS) -> list:
    """Apply `fn` to each item with a bounded number of threads.

    These calls block on the network, and urllib releases the GIL while it
    waits, so threads give real concurrency here without pulling in asyncio.
    Most of a run's wall clock is spent waiting on other people's servers, and
    the number of requests is what it is either way.

    The worker count is deliberately small and configurable. These are other
    people's servers: a burst of connections is both rude and the quickest way
    to get rate-limited or blocked, which would cost far more than it saves.

    Order is preserved — results come back where their inputs were — so a run
    stays reproducible whatever order the requests happen to finish in. `fn` is
    expected to handle its own per-item errors; an exception here aborts the
    whole batch, which is the caller's choice to make.
    """
    items = list(items)
    if workers <= 1 or len(items) <= 1:
        return [fn(item) for item in items]
    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as pool:
        return list(pool.map(fn, items))

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

def balanced_element(html: str, tag: str, start: int) -> str:
    """Return the full <tag>...</tag> substring starting at `start`.

    Depth-counts nested opens of the same tag, so it works for elements that
    contain other elements of the same kind (and for <a>, which is not a <div>).
    """
    if start < 0:
        return ""
    if not re.match(rf"<{re.escape(tag)}\b", html[start:start + len(tag) + 2], re.I):
        return ""
    tag_re = re.compile(rf"<(/?){re.escape(tag)}\b[^>]*>", re.I)
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

def balanced_div(html: str, start: int) -> str:
    """Return the full <div>...</div> substring starting at `start`."""
    return balanced_element(html, "div", start)

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

def to_cst(value) -> datetime | None:
    dt = parse_dt(value)
    return dt.astimezone(CST) if dt else None

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

# --------------------------------------------------------------------------
# Item schema
# --------------------------------------------------------------------------

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
# Full-text policy
#
# Default is "today": only items published on the current CST date get their
# full text fetched. Older items keep title + link. Pass a different policy to
# widen it:
#     today   (default) items published today only
#     latest            today's items, plus the newest one (weekly/monthly sources)
#     all               every item that supports full text
#     none              no full text at all
#     3                 the newest 3 items regardless of date
# --------------------------------------------------------------------------

FULLTEXT_DEFAULT = "today"
FULLTEXT_MODES = ("today", "latest", "all", "none")

def mark_today(items: list[dict], today=None) -> None:
    """Annotate every item with whether it was published on the current CST day."""
    today = today or datetime.now(CST).date()
    for entry in items:
        published = to_cst(entry.get("published_at"))
        entry["is_today"] = bool(published and published.date() == today)

def fulltext_targets(items: list[dict], mode=FULLTEXT_DEFAULT, today=None) -> list[dict]:
    """Return the items that should receive full text under the given policy."""
    today = today or datetime.now(CST).date()
    mark_today(items, today)

    if mode is None:
        mode = FULLTEXT_DEFAULT
    mode = str(mode).strip().lower()

    if mode in ("none", "off", "0"):
        return []
    if mode == "all":
        return list(items)
    if mode == "latest":
        picked = [e for e in items if e.get("is_today")]
        if items and items[0] not in picked:
            picked.append(items[0])
        return picked
    if mode.isdigit():
        return list(items)[: int(mode)]
    # "today" and anything unrecognised
    return [e for e in items if e.get("is_today")]

def fulltext_scope_label(entry: dict) -> str:
    """Human label describing why this item received full text."""
    return "today" if entry.get("is_today") else "requested"

def source_status(items: list[dict], today=None) -> dict:
    """Freshness summary for one source, rendered as a badge in the digest."""
    today = today or datetime.now(CST).date()
    mark_today(items, today)
    dated = [to_cst(e.get("published_at")) for e in items]
    dated = [d for d in dated if d]
    latest = max(dated) if dated else None
    return {
        "day": today.isoformat(),
        "updated_today": any(e.get("is_today") for e in items),
        "latest_published_at": latest.isoformat() if latest else None,
        "staleness_days": (today - latest.date()).days if latest else None,
        "count": len(items),
        "fulltext_fetched": sum(1 for e in items if e.get("body_text")),
    }

# --------------------------------------------------------------------------
# Post-processing: deterministic pre-dedupe
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

# --------------------------------------------------------------------------
# Helpers used by several collectors
# --------------------------------------------------------------------------

# Sources whose date is inherently approximate: a precise date found on the
# article page should win over these.
APPROXIMATE_DATE_SOURCES = {"api_relative", "archive_month", "relative_balloon",
                           "collection_time", "unknown"}

def _has_time(iso: str | None) -> bool:
    return bool(iso) and len(iso) >= 19 and iso[11:19] != "00:00:00"

def _prefer_new_date(entry: dict, new_iso: str | None) -> bool:
    """Should an extracted date replace the one already on the item?

    Only when it adds information. htmldate frequently returns a date-only value;
    overwriting a feed timestamp that carried a time would lose precision.
    """
    if not new_iso:
        return False
    old = entry.get("published_at")
    if not old:
        return True
    if entry.get("date_source") in APPROXIMATE_DATE_SOURCES:
        return True
    if _has_time(new_iso) and not _has_time(old):
        return True
    return False

def write_body(entry: dict, body: dict, *, engine: str, scope: str) -> None:
    """Attach an extraction result to an item and record provenance."""
    if not body:
        return
    text = body.get("text") or ""
    entry["body_text"] = text or None
    if body.get("html"):
        entry["body_html"] = body["html"]
    if body.get("sections"):
        entry["sections"] = body["sections"]
    if body.get("title") and not entry.get("title"):
        entry["title"] = body["title"]
    if body.get("author"):
        entry["author"] = entry.get("author") or body["author"]
    if body.get("tags"):
        entry["tags"] = body.get("tags") or entry.get("tags") or []
    if _prefer_new_date(entry, body.get("published_at")):
        entry["published_at"] = body["published_at"]
        entry["date_source"] = "article_page"
    extra = entry.setdefault("extra", {})
    extra["fulltext"] = True
    extra["body_scope"] = scope
    extra["body_chars"] = len(text)
    extra["extract_engine"] = engine

def media_root(default: str = "data/telegram/media") -> Path:
    """Media lives under the project root, whatever the working directory."""
    return PROJECT_ROOT / default

def project_relative(path: Path) -> str:
    """Store paths relative to the project root so they survive a cd."""
    path = Path(path).resolve()
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return path.as_posix()

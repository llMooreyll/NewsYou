#!/usr/bin/env python3
"""Two interchangeable extraction engines: `legacy` and `modern`.

The collector can run either one, selected with ``--engine``. Both must return
the same shape so the rest of the pipeline is engine-agnostic:

    parse_feed(raw, engine)   -> list of {title, link, published, author,
                                          summary, summary_html}
    extract_article(html, url, engine, legacy_marker)
                              -> {text, html, sections, title, author,
                                  published_at, tags} or None

legacy
    The original standard-library implementation: hand-rolled regex for feeds
    and a per-site container marker for article bodies. Needs nothing installed.

modern
    trafilatura for article bodies, feedparser for feeds. One code path for
    every site, and it keeps inline links and headings. Requires the optional
    dependencies in requirements.txt.

Why keep both: the modern engine is a single point of failure and its benchmark
quality is measured mostly on European news sites. Running both against the same
day lets the outputs be compared before the legacy path is retired.
"""
from __future__ import annotations

import re
import sys

from . import base
from .base import FetchError, balanced_div, strip_tags  # noqa: F401  (re-export)

ENGINES = ("legacy", "modern")
DEFAULT_ENGINE = "modern"

def engine_available(engine: str) -> tuple[bool, str]:
    """Return (ok, reason). Checked before a run so failures are clear."""
    if engine != "modern":
        return True, ""
    missing = []
    for module in ("trafilatura", "feedparser"):
        try:
            __import__(module)
        except ImportError:
            missing.append(module)
    if missing:
        return False, (f"engine 'modern' needs {', '.join(missing)}. "
                       "Install with: ./.venv/bin/pip install -r requirements.txt")
    return True, ""

def resolve_engine(requested: str, *, allow_fallback: bool = True) -> tuple[str, str]:
    """Return (engine to use, warning).

    The default engine is `modern`, but a bare checkout without the optional
    dependencies must still run. Rather than fail, fall back to `legacy` with a
    loud warning, and record which engine actually ran in the output index.
    """
    ok, reason = engine_available(requested)
    if ok:
        return requested, ""
    if allow_fallback and requested == DEFAULT_ENGINE:
        return "legacy", (f"{reason}\n"
                          f"  falling back to --engine legacy for this run. "
                          f"Results will differ from a modern run.")
    return requested, reason

def _import_modern():
    import trafilatura
    import feedparser
    return trafilatura, feedparser

# --------------------------------------------------------------------------
# Feeds
# --------------------------------------------------------------------------

_FEED_ITEM = re.compile(r"<(item|entry)[\s>].*?</\1>", re.S)

def _tag(block: str, name: str) -> str | None:
    match = re.search(rf"<{name}[^>]*>(.*?)</{name}>", block, re.S)
    if not match:
        return None
    value = base.clean_title(match.group(1))
    return value or None

def parse_feed_legacy(raw: str) -> list[dict]:
    """Regex feed parsing: RSS <item> and Atom <entry> in one pass."""
    out: list[dict] = []
    for match in _FEED_ITEM.finditer(raw):
        chunk = match.group(0)
        title = _tag(chunk, "title")
        link = _tag(chunk, "link")
        if not link:
            href = re.search(r'<link[^>]*href="([^"]+)"', chunk)
            link = href.group(1) if href else None
        if not title or not link:
            continue
        published = (_tag(chunk, "pubDate") or _tag(chunk, "updated")
                     or _tag(chunk, "published") or _tag(chunk, "dc:date"))
        summary = (_tag(chunk, "description") or _tag(chunk, "summary")
                   or _tag(chunk, "content"))
        out.append({
            "title": title,
            "link": link,
            "published": published,
            "author": _tag(chunk, "dc:creator") or _tag(chunk, "author"),
            "summary": strip_tags(summary) if summary else "",
            "summary_html": summary or "",
        })
    return out

def parse_feed_modern(raw: str) -> list[dict]:
    _trafilatura, feedparser = _import_modern()
    parsed = feedparser.parse(raw)
    out: list[dict] = []
    for entry in parsed.entries:
        link = entry.get("link") or ""
        if not link:
            for candidate in entry.get("links") or []:
                if candidate.get("href"):
                    link = candidate["href"]
                    break
        title = entry.get("title") or ""
        if not title or not link:
            continue
        summary = entry.get("summary") or ""
        out.append({
            "title": title,
            "link": link,
            "published": entry.get("published") or entry.get("updated"),
            "author": entry.get("author"),
            "summary": strip_tags(summary) if summary else "",
            "summary_html": summary,
        })
    return out

def parse_feed(raw: str, engine: str = "legacy") -> list[dict]:
    if engine == "modern":
        return parse_feed_modern(raw)
    return parse_feed_legacy(raw)

# --------------------------------------------------------------------------
# Article bodies
# --------------------------------------------------------------------------

_H2 = re.compile(r"<h([23])[^>]*>(.*?)</h\1>", re.S)

def split_sections(body_html: str) -> list[dict]:
    """Split a body into {title, text, html} blocks on <h2>/<h3> headings.

    Used by both engines so the digest renders the same structure either way.
    Returns [] when the body has no usable headings.
    """
    if not body_html:
        return []
    matches = list(_H2.finditer(body_html))
    if not matches:
        return []
    sections: list[dict] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body_html)
        chunk = body_html[start:end]
        text = strip_tags(chunk)
        title = strip_tags(match.group(2))
        if not text and not title:
            continue
        sections.append({"title": title or None, "text": text, "html": chunk})
    return sections

def extract_article_legacy(html: str, url: str, legacy_marker: str | None) -> dict | None:
    if not legacy_marker:
        return None
    block = base.container_at(html, legacy_marker)
    if not block:
        return None
    text = strip_tags(block)
    if not text:
        return None
    return {
        "text": text,
        "html": block,
        "sections": split_sections(block),
        "title": None,
        "author": None,
        "published_at": None,
        "tags": [],
    }

def extract_article_modern(html: str, url: str, *, want_html: bool = True,
                           include_images: bool = False) -> dict | None:
    trafilatura, _feedparser = _import_modern()

    text = trafilatura.extract(
        html, url=url, output_format="markdown",
        include_links=True, include_formatting=True,
        include_images=include_images,
    )
    body_html = None
    if want_html:
        body_html = trafilatura.extract(
            html, url=url, output_format="html",
            include_links=True, include_formatting=True,
            include_images=include_images,
        )
        if body_html:
            # trafilatura wraps the body; unwrap so it can be embedded directly
            body_html = re.sub(r"^\s*<html>\s*<body>", "", body_html, flags=re.I)
            body_html = re.sub(r"</body>\s*</html>\s*$", "", body_html, flags=re.I).strip()
    if not text and not body_html:
        return None

    meta = trafilatura.extract_metadata(html) if hasattr(trafilatura, "extract_metadata") else None
    published = None
    if meta and getattr(meta, "date", None):
        published = base.iso_cst(meta.date)
    return {
        "text": (text or "").strip(),
        "html": body_html,
        "sections": split_sections(body_html or ""),
        "title": getattr(meta, "title", None) if meta else None,
        "author": getattr(meta, "author", None) if meta else None,
        "published_at": published,
        "tags": (getattr(meta, "tags", None) or []) if meta else [],
    }

def extract_article(html: str, url: str, engine: str = "legacy",
                    legacy_marker: str | None = None, **kw) -> dict | None:
    """Dispatch to the selected engine. Returns None when nothing was found."""
    if engine == "modern":
        try:
            return extract_article_modern(html, url, **kw)
        except Exception as exc:  # noqa: BLE001  a bad page must not kill a run
            print(f"  ! modern extract failed {url}: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            return None
    return extract_article_legacy(html, url, legacy_marker)

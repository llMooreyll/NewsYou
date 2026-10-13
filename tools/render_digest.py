#!/usr/bin/env python3
"""Stage 3: render processed data into one self-contained HTML file.

    python tools/render_digest.py --indir data/processed --output site/digest.html

One file, zero external requests: CSS, JS and images are inlined, so the output
can be shared as a single attachment. The stylesheet and script live in
tools/digest_assets/ so they can be edited as real CSS/JS; they are read and
inlined at render time.

Layout per source type
----------------------
- tabs        one scrollable row; edge fades and arrow buttons appear only on
              the side that hides tabs.
- boards      Hacker News, 微博, 知乎: fixed 36px rows, rank / title / bar /
              numbers in aligned columns, so the whole list fits one screen.
- telegram    masonry dealt in reading order to the shortest column (JS), with
              image sizes written at build time so nothing reflows. A side rail
              filters by channel.
- sectioned   linux.do, 少数派 and the digest get a sticky side rail with
              scroll-spy; on narrow screens it becomes a second scroll strip.
- articles    date column + title + two-line summary, full text in an
              animated expander.
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import mimetypes
import os
import re
import struct
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse, quote

CST = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parent.parent
ASSETS = Path(__file__).resolve().parent / "digest_assets"

# name -> (tab label, subtitle)
SOURCE_META = {
    "sspai": ("少数派", "派早报全文 · 近期文章"),
    "telegram": ("Telegram", "订阅频道当日消息"),
    "linuxdo": ("linux.do", "社区晚报"),
    "latepost": ("晚点", "LatePost 深度报道"),
    "geekpark": ("极客公园", "科技资讯"),
    "ruanyifeng": ("阮一峰", "科技爱好者周刊"),
    "anywayfm": ("Anyway.Now", "设计与工具链接"),
    "hackernews": ("Hacker News", "Top stories"),
    "weibo": ("微博热搜", "实时榜"),
    "zhihu": ("知乎热榜", "全站榜"),
}
ORDER = list(SOURCE_META)

# Panel headings where the full name does not fit a tab.
PANEL_TITLE = {"linuxdo": "linux.do 晚报", "latepost": "晚点 LatePost", "ruanyifeng": "阮一峰的网络日志"}

TOPIC_LABEL = {
    "technology": "科技", "ai": "AI", "business": "商业", "policy": "政策",
    "society": "社会", "security": "安全", "internet": "互联网", "other": "其他",
}

# Dates from these sources carry no real time of day; show the date only.
DATE_ONLY_SOURCES = {"api_relative", "relative_balloon", "issue_date", "archive_month"}

WEEKDAYS = "一二三四五六日"

# Small inline icons (stroke = currentColor).
ICON = {
    "l": '<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M7.5 2.5 4 6l3.5 3.5"/></svg>',
    "r": '<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M4.5 2.5 8 6 4.5 9.5"/></svg>',
    "x": '<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M2.5 2.5l7 7M9.5 2.5l-7 7"/></svg>',
    "out": '<svg viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.3"><path d="M3 1.5h5.5V7M8.5 1.5 1.5 8.5"/></svg>',
    "eye": '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3"><path d="M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8Z"/><circle cx="8" cy="8" r="2"/></svg>',
    "play": '<svg viewBox="0 0 10 10" fill="currentColor"><path d="M2.5 1.5v7l6-3.5z"/></svg>',
}

# Render context: the day being rendered, so "today" is the data's day and not
# the wall clock when the file is opened.
_CTX = {"day": None}


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def parse_iso(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CST)
    return dt.astimezone(CST)


def fmt_time(value) -> str:
    dt = parse_iso(value)
    return dt.strftime("%H:%M") if dt else ""


def fmt_md(value) -> str:
    dt = parse_iso(value)
    return dt.strftime("%m-%d") if dt else ""


def fmt_day(value) -> str:
    dt = parse_iso(value) if value and "T" in str(value) else None
    if dt is None:
        try:
            dt = datetime.strptime(str(value)[:10], "%Y-%m-%d")
        except (ValueError, TypeError):
            return str(value or "")
    return f"{dt.year}.{dt.month:02d}.{dt.day:02d} 周{WEEKDAYS[dt.weekday()]}"


def is_today(value) -> bool:
    dt = parse_iso(value)
    return bool(dt and _CTX["day"] and dt.date().isoformat() == _CTX["day"])


def stamp(entry: dict) -> tuple[str, str]:
    """(date label, time label) honest about how precise the source is."""
    dt = parse_iso(entry.get("published_at"))
    if not dt:
        return "", ""
    src = entry.get("date_source") or ""
    if src == "archive_month":
        return dt.strftime("%Y-%m"), ""
    date = "今天" if is_today(entry.get("published_at")) else dt.strftime("%m-%d")
    if src in DATE_ONLY_SOURCES or src == "collection_time":
        return date, ""
    return date, dt.strftime("%H:%M")


def domain(url: str) -> str:
    host = (urlparse(url or "").netloc or "").lower()
    return host[4:] if host.startswith("www.") else host


def fmt_heat(value) -> str:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return str(value or "")
    if n >= 1e8:
        return f"{n / 1e8:.1f}亿"
    if n >= 1e4:
        return f"{n / 1e4:.1f}万" if n < 1e6 else f"{n / 1e4:.0f}万"
    return f"{n:.0f}"


def heat_number(value) -> float:
    """'585 万热度' -> 5850000.0; plain numbers pass through."""
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"([\d.]+)\s*(亿|万)?", str(value or ""))
    if not match:
        return 0.0
    n = float(match.group(1))
    return n * {"亿": 1e8, "万": 1e4}.get(match.group(2) or "", 1)


def linkify(text: str) -> str:
    out = esc(text)
    out = re.sub(r"(https?://[^\s<]+)",
                 r'<a class="lnk" href="\1" target="_blank" rel="noopener noreferrer">\1</a>', out)
    return out.replace("\n", "<br>")


def ext(url: str, label: str, cls: str = "") -> str:
    c = f' class="{cls}"' if cls else ""
    return f'<a{c} href="{esc(url)}" target="_blank" rel="noopener noreferrer">{label}</a>'


# --------------------------------------------------------------------------
# HTML sanitising
#
# Bodies come from extractors and from Telegram's own markup. The output is a
# shared file, so this is the last line of defence: allowlisted tags only, no
# attributes except http(s) hrefs, and tags are balanced on the way out so a
# fragment cut mid-element cannot leak formatting into the rest of the page.
# --------------------------------------------------------------------------

_ALLOWED_TAGS = {"p", "br", "h2", "h3", "h4", "strong", "b", "em", "i",
                 "code", "pre", "blockquote", "ul", "ol", "li", "a"}
_DROP_CONTENT_TAGS = {"script", "style", "iframe", "noscript", "svg", "form",
                      "button", "input", "select", "textarea", "img", "figure", "video"}
_VOID_TAGS = {"img", "input", "br", "hr", "source", "wbr"}
_TAG_RE = re.compile(r"<\s*(/?)\s*([a-zA-Z][a-zA-Z0-9-]*)([^>]*?)(/?)\s*>")
_HREF_RE = re.compile(r"href\s*=\s*(\"([^\"]*)\"|'([^']*)')", re.I)


def _safe_href(attrs: str) -> str | None:
    match = _HREF_RE.search(attrs or "")
    if not match:
        return None
    url = html.unescape(match.group(2) if match.group(2) is not None else match.group(3) or "").strip()
    return url if url.startswith(("http://", "https://")) else None


def sanitize_html(fragment: str) -> str:
    if not fragment:
        return ""
    out: list[str] = []
    open_stack: list[str] = []
    pos = 0
    skip_until: str | None = None
    a_kept: list[bool] = []

    for match in _TAG_RE.finditer(fragment):
        text = fragment[pos:match.start()]
        if skip_until is None and text:
            out.append(esc(html.unescape(text)))
        pos = match.end()

        closing, name, attrs, selfclose = (match.group(1), match.group(2).lower(),
                                           match.group(3), match.group(4))
        if skip_until is not None:
            if closing and name == skip_until:
                skip_until = None
            continue
        if name in _DROP_CONTENT_TAGS:
            # void elements (<img>, <input>) have no content to skip; waiting
            # for their closing tag would swallow the rest of the fragment
            if not closing and not selfclose and name not in _VOID_TAGS:
                skip_until = name
            continue
        if name not in _ALLOWED_TAGS:
            continue
        if name == "a":
            if closing:
                if a_kept and a_kept.pop():
                    out.append("</a>")
                    if "a" in open_stack:
                        open_stack.remove("a")
            else:
                href = _safe_href(attrs)
                if href:
                    a_kept.append(True)
                    open_stack.append("a")
                    out.append(f'<a href="{esc(href)}" target="_blank" rel="noopener noreferrer">')
                else:
                    a_kept.append(False)
            continue
        if name == "br":
            out.append("<br>")
        elif closing:
            if name in open_stack:
                # close anything opened after it so nesting stays valid
                while open_stack:
                    top = open_stack.pop()
                    out.append(f"</{top}>")
                    if top == name:
                        break
        else:
            open_stack.append(name)
            out.append(f"<{name}>")

    tail = fragment[pos:]
    if skip_until is None and tail:
        out.append(esc(html.unescape(tail)))
    for name in reversed(open_stack):
        out.append(f"</{name}>")
    return "".join(out)


_EMPTY_EL = re.compile(r"<(p|strong|b|em|i|h[234]|li|blockquote)>(?:\s|&nbsp;|<br>|\u200b)*</\1>")


def tidy(fragment: str) -> str:
    """Remove the empty <p><strong><br></strong></p> scaffolding CMSs leave."""
    out = fragment.replace("\u200b", "").replace("\ufeff", "")
    for _ in range(4):
        new = _EMPTY_EL.sub("", out)
        if new == out:
            break
        out = new
    out = re.sub(r"<p>\s*(?:<br>\s*)+", "<p>", out)
    out = re.sub(r"(?:<br>\s*)+</p>", "</p>", out)
    # a <br> next to a block element is an empty line, not spacing
    blocks = r"(?:p|blockquote|ul|ol|li|h[234]|pre)"
    out = re.sub(rf"(</{blocks}>)\s*(?:<br>\s*)+", r"\1", out)
    out = re.sub(rf"(?:<br>\s*)+(<{blocks}>)", r"\1", out)
    out = re.sub(r"(?:<br>\s*){3,}", "<br><br>", out)
    out = re.sub(r"^(?:\s*<br>)+|(?:<br>\s*)+$", "", out.strip())
    return out.strip()


_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")


def _inline_md(text: str) -> str:
    out = esc(text)
    out = _MD_LINK.sub(lambda m: f'<a href="{m.group(2)}" target="_blank" rel="noopener noreferrer">{m.group(1)}</a>', out)
    out = _MD_BOLD.sub(r"<strong>\1</strong>", out)
    return out.replace("****", "")


def paragraphs(text: str) -> str:
    """Plain/markdown-ish text -> paragraphs, headings and lists."""
    if not text:
        return ""
    out = []
    for chunk in [c.strip() for c in re.split(r"\n{2,}", text) if c.strip()]:
        heading = re.match(r"^(#{1,4})\s+(.*)", chunk)
        if heading:
            level = min(4, max(2, len(heading.group(1)) + 1))
            out.append(f"<h{level}>{_inline_md(heading.group(2).strip())}</h{level}>")
            continue
        lines = chunk.splitlines()
        if all(re.match(r"^\s*[-*•]\s+", l) for l in lines):
            items = "".join(f"<li>{_inline_md(re.sub(r'^\s*[-*•]\s+', '', l))}</li>" for l in lines)
            out.append(f"<ul>{items}</ul>")
            continue
        body = "<br>".join(_inline_md(l) for l in lines)
        if re.sub(r"<[^>]+>|\s", "", body):
            out.append(f"<p>{body}</p>")
    return "".join(out)


def _use_rich(entry: dict) -> bool:
    """Link-preserving HTML bodies exist for the modern engine only."""
    return (entry.get("extra") or {}).get("extract_engine") == "modern"


def body_fragment(block: dict, rich: bool) -> str:
    """Render a section ({text, html}) or a whole item ({body_text, body_html})."""
    text = block.get("text")
    if text is None:
        text = block.get("body_text")
    html_body = block.get("html")
    if html_body is None:
        html_body = block.get("body_html")
    if rich and html_body:
        cleaned = tidy(sanitize_html(html_body))
        if cleaned:
            return cleaned
    return paragraphs(text or "")


# --------------------------------------------------------------------------
# images
# --------------------------------------------------------------------------

def image_size(raw: bytes) -> tuple[int, int] | None:
    """Width/height from the header bytes of JPEG, PNG, GIF or WebP."""
    try:
        if raw[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", raw[16:24])
        if raw[:6] in (b"GIF87a", b"GIF89a"):
            return struct.unpack("<HH", raw[6:10])
        if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
            kind = raw[12:16]
            if kind == b"VP8 ":
                w, h = struct.unpack("<HH", raw[26:30])
                return w & 0x3FFF, h & 0x3FFF
            if kind == b"VP8L":
                b = raw[21:25]
                w = 1 + (((b[1] & 0x3F) << 8) | b[0])
                h = 1 + (((b[3] & 0x0F) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6))
                return w, h
            if kind == b"VP8X":
                w = 1 + int.from_bytes(raw[24:27], "little")
                h = 1 + int.from_bytes(raw[27:30], "little")
                return w, h
        if raw[:2] == b"\xff\xd8":
            i = 2
            while i < len(raw) - 9:
                if raw[i] != 0xFF:
                    i += 1
                    continue
                marker = raw[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                length = struct.unpack(">H", raw[i + 2:i + 4])[0]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h, w = struct.unpack(">HH", raw[i + 5:i + 9])
                    return w, h
                i += 2 + length
    except (struct.error, IndexError):
        return None
    return None


def resolve_local(value: str) -> Path | None:
    for candidate in (Path(value), ROOT / value):
        if candidate.is_file():
            return candidate
    return None


def image_tag(path: Path, embed: bool, out_dir: Path) -> str:
    raw = path.read_bytes()
    size = image_size(raw)
    if embed:
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        src = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
    else:
        try:
            src = Path(os.path.relpath(path.resolve(), out_dir.resolve())).as_posix()
        except ValueError:          # different drive on Windows
            src = path.resolve().as_uri()
    dims = f' width="{size[0]}" height="{size[1]}"' if size else ""
    return f'<img src="{esc(src)}"{dims} alt="" loading="lazy" decoding="async">'


# --------------------------------------------------------------------------
# shared fragments
# --------------------------------------------------------------------------

def panel_head(title: str, sub: str, status: dict | None, extra_meta: str = "") -> str:
    meta = [extra_meta] if extra_meta else []
    if status:
        if status.get("updated_today"):
            meta.append('<span class="fresh-tag ok">今日更新</span>')
        elif status.get("staleness_days") is not None:
            meta.append(f'<span class="fresh-tag">{esc(status["staleness_days"])} 天未更新</span>')
        latest = status.get("latest_published_at")
        if latest:
            meta.append(f'<span><span class="k">最新 </span><span class="v">{esc(fmt_md(latest))} {esc(fmt_time(latest))}</span></span>')
    return (f'<header class="ph"><div class="ph-t"><h1>{esc(title)}</h1>'
            + (f'<p class="ph-sub">{esc(sub)}</p>' if sub else "")
            + f'</div><div class="ph-meta">{"".join(meta)}</div></header>')


def scroller(inner: str, cls: str = "", label: str = "") -> str:
    aria = f' aria-label="{esc(label)}"' if label else ""
    return (f'<div class="scroller {cls}">'
            f'<button class="sc-btn l" type="button" tabindex="-1" aria-hidden="true">{ICON["l"]}</button>'
            f'<div class="scroller-track"{aria}>{inner}</div>'
            f'<button class="sc-btn r" type="button" tabindex="-1" aria-hidden="true">{ICON["r"]}</button>'
            "</div>")


def rail(links: list[tuple[str, str, int | None]], mode: str, heading: str) -> str:
    """links: (target-or-filter key, label, count)."""
    attr = "data-target" if mode == "spy" else "data-filter"
    items = "".join(
        f'<button class="rail-a{" on" if i == 0 else ""}" type="button" {attr}="{esc(key)}" title="{esc(label)}">'
        f'<span class="lbl">{esc(label)}</span>'
        + (f'<span class="n">{count}</span>' if count is not None else "")
        + "</button>"
        for i, (key, label, count) in enumerate(links)
    )
    inner = f'<nav class="rail-list" aria-label="{esc(heading)}">{items}<span class="rail-ink"></span></nav>'
    return (f'<aside class="rail" data-mode="{mode}"><div class="rail-h">{esc(heading)}</div>'
            f'{scroller(inner)}</aside>')


def with_rail(rail_html: str, content: str) -> str:
    return f'<div class="lay">{rail_html}<div class="content">{content}</div></div>'


def expander(label: str, count: str, inner: str) -> str:
    chev = '<svg class="chev" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3.5 2 6.5 5l-3 3"/></svg>'
    return (f'<div class="exp"><button class="exp-btn" type="button" aria-expanded="false">{chev}'
            f'{esc(label)}' + (f'<span class="c">{esc(count)}</span>' if count else "") + "</button>"
            f'<div class="exp-body"><div class="exp-inner"><div class="exp-pad">'
            f'<div class="prose">{inner}</div>'
            f'<button class="exp-close" type="button">收起 ↑</button>'
            "</div></div></div></div>")


# --------------------------------------------------------------------------
# digest panel
# --------------------------------------------------------------------------

def _ref_name(ref: dict) -> str:
    if ref.get("channel"):
        return "@" + ref["channel"]
    return ref.get("source_name") or SOURCE_META.get(ref.get("source") or "", (ref.get("source") or "",))[0]


def render_digest_panel(digest: dict) -> str:
    cards = digest.get("cards") or []
    if not cards:
        return '<p class="empty">今日无汇总条目</p>'

    def topics(items) -> str:
        return "".join(f'<span class="tp-c">{esc(TOPIC_LABEL.get(t, t))}</span>' for t in items or [])

    def refs(items) -> str:
        seen, out = set(), []
        for ref in items or []:
            name = _ref_name(ref)
            if not name or name in seen:
                continue
            seen.add(name)
            out.append(ext(ref.get("url"), esc(name) + " ↗"))
        return "".join(out)

    lead, rest = cards[0], cards[1:]
    secs = []
    lead_html = (f'<article class="dg-lead rv" style="--i:1"><div class="no">01 · 头条</div>'
                 f'<h2>{esc(lead.get("title"))}</h2>'
                 + (f'<p>{esc(lead.get("summary"))}</p>' if lead.get("summary") and lead.get("summary") != lead.get("title") else "")
                 + f'<div class="dg-m">{topics(lead.get("topics"))}'
                 + (f'<span>{esc(lead.get("why"))}</span>' if lead.get("why") else "")
                 + f'<span>重要度 {esc(lead.get("importance"))}</span>{refs(lead.get("refs"))}</div></article>')
    head = f'<p class="dg-hl rv">{esc(digest.get("headline"))}</p>' if digest.get("headline") else ""
    secs.append(f'<section class="sec" id="dg-lead">{head}{lead_html}</section>')

    if rest:
        rows = "".join(
            f'<article class="dg-it rv" style="--i:{i + 2}"><div class="no">{int(c.get("rank") or i + 2):02d}</div>'
            f'<div><h3>{esc(c.get("title"))}</h3>'
            + (f'<p>{esc(c.get("summary"))}</p>' if c.get("summary") and c.get("summary") != c.get("title") else "")
            + f'<div class="dg-m">{topics(c.get("topics"))}'
            + (f'<span>{esc(c.get("why"))}</span>' if c.get("why") else "")
            + f'{refs(c.get("refs"))}</div></div>'
            f'<div class="sc">{esc(c.get("importance"))}</div></article>'
            for i, c in enumerate(rest)
        )
        secs.append(f'<section class="sec" id="dg-items"><h2 class="sec-h">要点<span class="n">{len(rest)}</span></h2>{rows}</section>')

    themes = digest.get("themes") or []
    if themes:
        cells = "".join(
            f'<div class="th"><div class="th-n">{esc(t.get("name"))}</div>'
            f'<p class="th-s">{esc(t.get("summary"))}</p>'
            f'<div class="th-r">{refs(t.get("refs"))}</div></div>' for t in themes
        )
        secs.append(f'<section class="sec" id="dg-themes"><h2 class="sec-h">脉络<span class="n">{len(themes)}</span></h2>'
                    f'<div class="dg-th">{cells}</div></section>')

    notes = []
    if digest.get("stats_note"):
        notes.append(esc(digest["stats_note"]))
    if digest.get("model"):
        notes.append("AI · " + esc(digest["model"]))
    if notes:
        secs.append(f'<p class="dg-note">{"<br>".join(notes)}</p>')

    links = [("dg-lead", "头条", None)]
    if rest:
        links.append(("dg-items", "要点", len(rest)))
    if themes:
        links.append(("dg-themes", "脉络", len(themes)))
    return with_rail(rail(links, "spy", "本页"), f'<div class="dg">{"".join(secs)}</div>')


# --------------------------------------------------------------------------
# telegram
# --------------------------------------------------------------------------

_BR = re.compile(r"<br\s*/?>", re.I)
_TAGS_ONLY = re.compile(r"^(?:\s*(?:🗒\s*)?(?:标签[:：])?\s*(?:#[^\s#]+\s*)+)$")
_BOILER = re.compile(
    r"^\s*(?:📢\s*)?(?:频道|投稿|订阅|关注|Channel|Subscribe)\s*[:：]|"
    r"^\s*(?:🤖\s*)?投稿\s*[:：]|"
    r"^\s*🌸?\s*科技圈\s*·|"
    r"^\s*(?:📮|📬|👉)\s*(?:投稿|订阅|频道)"
)
_VIA = re.compile(r"^\s*(?:——|—|来源[:：]|via\s)", re.I)


def _plain(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", "", fragment)
    return html.unescape(text).replace("\u200e", "").replace("\u200f", "").strip()


def _tg_hashtags(text: str) -> list[str]:
    return re.findall(r"#([^\s#]+)", text)


def telegram_parts(entry: dict) -> dict:
    """Split one post into title / body paragraphs / hashtags.

    Channel signatures ("频道: @x", "投稿: @bot") and hashtag lines are not
    content; they are lifted out so cards read as news, not as boilerplate.
    """
    extra = entry.get("extra") or {}
    merged = bool(entry.get("ai_merged"))
    src_html = "" if merged else (extra.get("text_html") or "")
    plain = entry.get("summary") if merged else (extra.get("text") or entry.get("summary") or "")

    if src_html:
        # <tg-emoji>/<i class="emoji"> wrappers carry a <b> with the emoji;
        # unwrap them so the emoji stays plain text and is not bolded.
        src_html = re.sub(r'<i class="emoji"[^>]*><b>(.*?)</b></i>', r"\1", src_html, flags=re.S)
        lines = _BR.split(src_html)
    else:
        lines = [esc(l) for l in (plain or "").split("\n")]

    tags: list[str] = []
    kept: list[str] = []
    for line in lines:
        text = _plain(line)
        if text and _TAGS_ONLY.match(text):
            tags += _tg_hashtags(text)
            continue
        if text and _BOILER.search(text):
            continue
        kept.append(line)

    # title: first non-empty line when it is short, or fully bold
    title = ""
    first = next((i for i, l in enumerate(kept) if _plain(l)), None)
    if first is not None:
        line = kept[first]
        text = _plain(line)
        bold = bool(re.fullmatch(r"\s*<b>.*</b>\s*", line, re.S))
        more = any(_plain(l) for l in kept[first + 1:])
        if more and (bold or len(text) <= 60):
            title = text
            kept = kept[first + 1:]

    if merged:
        title = entry.get("title") or title
        body = (plain or "").strip()
        if title and body.startswith(title.rstrip("…")):
            body = body[len(title.rstrip("…")):].lstrip(" ，。,.:：\n")
        kept = [esc(body)] if body else []

    # group lines into paragraphs on blank lines
    paras: list[list[str]] = [[]]
    for line in kept:
        if _plain(line):
            paras[-1].append(line)
        elif paras[-1]:
            paras.append([])
    out = []
    for group in paras:
        if not group:
            continue
        if src_html:
            frag = tidy(sanitize_html("<br>".join(group)))
        else:
            frag = linkify("\n".join(html.unescape(l) for l in group))
        if not frag:
            continue
        cls = ' class="via"' if _VIA.match(_plain(group[0])) and len(group) == 1 else ""
        tag = "div" if re.search(r"<(?:blockquote|ul|ol|pre|h[234])>", frag) else "p"
        out.append(f"<{tag}{cls}>{frag}</{tag}>")
    body_html = "".join(out)
    chars = sum(len(_plain(l)) for l in kept)
    return {"title": title, "body": body_html, "tags": list(dict.fromkeys(tags)), "chars": chars}


def _reply_quote(text: str) -> str:
    text = (text or "").replace("\u200e", "").replace("\u200f", "").strip()
    if not text:
        return ""
    parts = [p.strip() for p in text.split("\n") if p.strip()]
    who, rest = (parts[0], " ".join(parts[1:])) if len(parts) > 1 else ("", parts[0])
    return f'<div class="tg-q">{f"<b>{esc(who)}</b>" if who else ""}{esc(rest[:220])}</div>'


def _tg_images(values: list[str], embed: bool, out_dir: Path) -> str:
    paths = [p for p in (resolve_local(v) for v in values or []) if p][:2]
    if not paths:
        return ""
    cells = "".join(image_tag(p, embed, out_dir) for p in paths)
    return f'<div class="shots n{len(paths)}">{cells}</div>'


def _tg_card(entry: dict, index: int, embed: bool, out_dir: Path) -> tuple[str, str]:
    extra = entry.get("extra") or {}
    channel = extra.get("channel") or ""
    merged = bool(entry.get("ai_merged"))
    parts = telegram_parts(entry)

    shots = _tg_images(extra.get("local_images") or [], embed, out_dir)
    media = set(extra.get("media") or [])
    pill = ""
    if not shots and (media & {"video", "video_message", "photo", "document", "audio", "voice", "poll"}):
        kind = ("视频" if media & {"video", "video_message"} else "图片" if "photo" in media
                else "文件" if "document" in media else "音频" if media & {"audio", "voice"} else "投票")
        pill = ext(entry.get("url"), f'{ICON["play"]}{kind} · 在 Telegram 查看', "tg-media")

    title = f'<h3 class="tg-t">{esc(parts["title"])}</h3>' if parts["title"] else ""
    body = ""
    if parts["body"]:
        long = parts["chars"] > 260
        body = f'<div class="tg-b{" clamp" if long else ""}">{parts["body"]}</div>'
        if long:
            body += '<button class="more" type="button" aria-expanded="false">展开全文</button>'

    tags = ""
    if parts["tags"]:
        tags = '<div class="tg-tags">' + "".join(
            ext(f"https://t.me/s/{quote(channel)}?q=%23{quote(t)}", "#" + esc(t)) for t in parts["tags"][:6]
        ) + "</div>"

    quote_html = "" if merged else _reply_quote(extra.get("reply_text") or "")

    rx = ""
    if extra.get("reactions"):
        rx = '<span class="rx">' + "".join(
            f'<span>{esc(r.get("emoji"))} {esc(r.get("count")) if r.get("count") is not None else ""}</span>'
            for r in extra["reactions"][:3]
        ) + "</span>"
    views = f'<span class="vw">{ICON["eye"]}{esc(extra["views"])}</span>' if extra.get("views") else ""

    cmp_btn, tpl = "", ""
    if merged and entry.get("originals"):
        n = len(entry["originals"])
        cmp_btn = (f'<button class="cmp" type="button" data-ov="og-{index}" data-ov-title="原始消息 · {n} 条">'
                   f'对照 {n} 条原始消息</button>')
        blocks = []
        for orig in entry["originals"]:
            op = telegram_parts({"extra": {"text_html": orig.get("text_html") or "",
                                           "text": orig.get("text") or ""}})
            body_o = (f'<p><b>{esc(op["title"])}</b></p>' if op["title"] else "") + op["body"]
            blocks.append(
                f'<div class="og"><div class="og-h"><span class="ch">@{esc(orig.get("channel") or "")}</span>'
                f'<time>{esc(fmt_time(orig.get("published_at")))}</time>'
                f'{ext(orig.get("url"), "原文 ↗")}</div><div class="tg-b">{body_o}</div></div>')
        tpl = (f'<template id="og-{index}"><p class="ov-note">{esc(entry.get("title"))}</p>'
               f'{"".join(blocks)}</template>')

    ai = f'<span class="tg-ai">AI 合并 {len(entry.get("merged_from") or [])}</span>' if merged else ""
    card = (f'<article class="tg" data-ch="{esc(channel)}">'
            f'<div class="tg-h">{ext("https://t.me/" + channel, "@" + esc(channel), "tg-ch")}{ai}'
            f'<time>{esc(fmt_time(entry.get("published_at")))}</time></div>'
            f'{shots}{quote_html}{title}{body}{pill}{tags}{cmp_btn}'
            f'<div class="tg-f">{views}{rx}{ext(entry.get("url"), "原帖" + ICON["out"], "go")}</div>'
            "</article>")
    return card, tpl


def render_telegram_panel(items: list[dict], embed: bool, out_dir: Path) -> str:
    cards, tpls = [], []
    counts: dict[str, int] = {}
    for index, entry in enumerate(items, 1):
        card, tpl = _tg_card(entry, index, embed, out_dir)
        cards.append(card)
        tpls.append(tpl)
        ch = (entry.get("extra") or {}).get("channel") or ""
        counts[ch] = counts.get(ch, 0) + 1
    links = [("*", "全部频道", len(items))] + [
        (ch, "@" + ch, n) for ch, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    ]
    grid = f'<div class="mas">{"".join(cards)}</div>{"".join(tpls)}'
    return with_rail(rail(links, "filter", "频道"), grid)


# --------------------------------------------------------------------------
# boards: Hacker News / 微博 / 知乎
# --------------------------------------------------------------------------

def _bar(value: float, top: float) -> str:
    ratio = 0 if top <= 0 else max(0.02, min(1.0, value / top))
    return f'<span class="br-bar"><i style="--w:{ratio:.3f}"></i></span>'


def render_hn_board(items: list[dict]) -> str:
    top = max([float((e.get("extra") or {}).get("score") or 0) for e in items] or [0])
    rows = []
    for i, e in enumerate(items, 1):
        x = e.get("extra") or {}
        score = float(x.get("score") or 0)
        hn = x.get("hn_url") or e.get("url")
        dom = domain(e.get("url"))
        dom = "" if dom == "news.ycombinator.com" else dom
        date, tm = stamp(e)
        when = tm if date == "今天" else date
        rows.append(
            f'<li class="br rv{" top" if i <= 3 else ""}" style="--i:{i}">'
            f'<span class="br-rk">{i}</span>'
            f'<span class="br-t">{ext(e.get("url"), esc(e.get("title")))}'
            + (f'<span class="br-dom">{esc(dom)}</span>' if dom else "")
            + f'</span>{_bar(score, top)}'
            f'<span class="br-v">{score:.0f}</span>'
            f'<span class="br-v dim">{ext(hn, esc(x.get("comments") or 0))}</span>'
            f'<span class="br-tm">{esc(when)}</span></li>')
    legend = ('<div class="bd-legend"><span></span><span>标题</span><span class="h-bar"></span>'
              '<span style="text-align:right">分</span><span style="text-align:right">评论</span>'
              '<span class="h-tm" style="text-align:right">时间</span></div>')
    return f'<div class="b-hn">{legend}<ol class="board">{"".join(rows)}</ol></div>'


def render_weibo_board(items: list[dict]) -> str:
    vals = [heat_number((e.get("extra") or {}).get("heat")) for e in items]
    top = max(vals or [0])
    rows = []
    for i, (e, v) in enumerate(zip(items, vals), 1):
        x = e.get("extra") or {}
        rank = x.get("rank") or i
        label = (x.get("label") or "").strip()
        cls = "hot" if label in ("沸", "爆") else "new" if label == "新" else ""
        badge = f'<span class="lbl-t {cls}">{esc(label[:1])}</span>' if label else "<span></span>"
        rows.append(
            f'<li class="br rv{" top" if i <= 3 else ""}" style="--i:{i}">'
            f'<span class="br-rk">{esc(rank)}</span>'
            f'<span class="br-t">{ext(e.get("url"), esc(e.get("title")))}</span>'
            f'{badge}{_bar(v, top)}<span class="br-v">{esc(fmt_heat(v))}</span></li>')
    legend = ('<div class="bd-legend"><span></span><span>话题</span><span></span><span class="h-bar"></span>'
              '<span style="text-align:right">热度</span></div>')
    return f'<div class="b-wb">{legend}<ol class="board">{"".join(rows)}</ol></div>'


def render_zhihu_board(items: list[dict]) -> str:
    vals = [heat_number((e.get("extra") or {}).get("heat")) for e in items]
    top = max(vals or [0])
    rows = []
    for i, (e, v) in enumerate(zip(items, vals), 1):
        x = e.get("extra") or {}
        excerpt = re.sub(r"\s+", " ", e.get("summary") or "").strip()
        if re.fullmatch(r"https?://\S+", excerpt):
            excerpt = ""
        rows.append(
            f'<li class="br rv{" top" if i <= 3 else ""}" style="--i:{i}">'
            f'<span class="br-rk">{esc(x.get("rank") or i)}</span>'
            f'<span class="br-t">{ext(e.get("url"), esc(e.get("title")))}'
            + (f'<span class="br-x" title="{esc(excerpt[:400])}">{esc(excerpt[:160])}</span>' if len(excerpt) > 8 else "")
            + f'</span>{_bar(v, top)}<span class="br-v">{esc(fmt_heat(v))}</span></li>')
    legend = ('<div class="bd-legend"><span></span><span>问题</span><span class="h-bar"></span>'
              '<span style="text-align:right">热度</span></div>')
    return f'<div class="b-zh">{legend}<ol class="board">{"".join(rows)}</ol></div>'


# --------------------------------------------------------------------------
# article lists
# --------------------------------------------------------------------------

def article_rows(items: list[dict], *, start: int = 0) -> str:
    rows, last = [], None
    for k, e in enumerate(items):
        x = e.get("extra") or {}
        date, tm = stamp(e)
        rep = " rep" if date and date == last else ""
        last = date
        today = " today" if date == "今天" else ""
        dcol = (f'<span class="d{today}{rep}">{esc(date)}</span>' if date else "") + (f"<time>{esc(tm)}</time>" if tm else "")

        summary = re.sub(r"\s+", " ", e.get("summary") or "").strip()
        if summary.rstrip(".。… ") == (e.get("title") or "").strip():
            summary = ""
        meta = []
        if e.get("author"):
            meta.append(f'<span>{esc(e["author"])}</span>')
        if x.get("is_exclusive"):
            meta.append('<span class="x">独家</span>')
        tags = []
        for t in e.get("tags") or []:
            tags += [s.strip() for s in str(t).split(",") if s.strip()]
        meta += [f'<span class="tag">#{esc(t)}</span>' for t in tags[:4]]

        detail = ""
        body = e.get("body_text") or e.get("body_html")
        if body:
            scope = {"today": "今日", "latest": "最新一期", "requested": "按需"}.get(x.get("body_scope"), "")
            chars = x.get("body_chars") or len(e.get("body_text") or "")
            detail = expander("阅读全文" + (f" · {scope}" if scope else ""), f"{chars} 字",
                              body_fragment(e, _use_rich(e)))
        rows.append(
            f'<li class="ar rv" style="--i:{start + k + 1}"><div class="ar-d">{dcol}</div><div>'
            f'<h3 class="ar-t">{ext(e.get("url"), esc(e.get("title")))}</h3>'
            + (f'<p class="ar-s">{esc(summary[:400])}</p>' if summary and not detail else
               f'<p class="ar-s">{esc(summary[:220])}</p>' if summary else "")
            + (f'<div class="ar-m">{"".join(meta)}</div>' if meta else "")
            + f"{detail}</div></li>")
    return "".join(rows)


def render_article_panel(items: list[dict]) -> str:
    return f'<ol class="alist">{article_rows(items)}</ol>'


# --------------------------------------------------------------------------
# linux.do
# --------------------------------------------------------------------------

LINUXDO_ORDER = ["今日要闻", "头条", "AI 前线", "开源开发", "福利羊毛", "社区闲话", "站外速览", "读者评论"]


def render_linuxdo_panel(items: list[dict]) -> str:
    groups: dict[str, list[dict]] = {}
    for e in items:
        groups.setdefault((e.get("extra") or {}).get("section_name") or "其他", []).append(e)
    names = [n for n in LINUXDO_ORDER if n in groups] + [n for n in groups if n not in LINUXDO_ORDER]

    secs, links, k = [], [], 0
    for idx, name in enumerate(names, 1):
        sid = f"ld-{idx}"
        rows = []
        for e in groups[name]:
            k += 1
            s = (e.get("summary") or "").strip()
            rows.append(f'<li class="tp rv" style="--i:{k}"><h3 class="tp-t">{ext(e.get("url"), esc(e.get("title")))}</h3>'
                        + (f'<p class="tp-s">{esc(s)}</p>' if s else "") + "</li>")
        secs.append(f'<section class="sec" id="{sid}"><h2 class="sec-h">{esc(name)}<span class="n">{len(groups[name])}</span></h2>'
                    f'<ol class="topics">{"".join(rows)}</ol></section>')
        links.append((sid, name, len(groups[name])))
    return with_rail(rail(links, "spy", "板块"), "".join(secs))


# --------------------------------------------------------------------------
# 少数派
# --------------------------------------------------------------------------

def render_sspai_panel(items: list[dict]) -> str:
    features = [e for e in items if "派早报" in (e.get("title") or "") and (e.get("sections") or e.get("body_text"))]
    others = [e for e in items if e not in features]
    secs, links = [], []

    for fi, e in enumerate(features, 1):
        rich = _use_rich(e)
        date, tm = stamp(e)
        sections = [s for s in (e.get("sections") or []) if (s.get("text") or s.get("html"))]
        head = (f'<header class="feat-h rv"><span class="kicker">派早报</span>'
                f'<h2>{ext(e.get("url"), esc(e.get("title")))}</h2>'
                f'<div class="ar-m"><span>{esc(date)} {esc(tm)}</span>'
                + (f'<span>{esc(e["author"])}</span>' if e.get("author") else "")
                + f'<span>{len(sections) or 1} 条</span></div></header>')
        secs.append(f'<section class="sec" id="sp-f{fi}">{head}</section>')
        links.append((f"sp-f{fi}", "派早报" if len(features) == 1 else f"派早报 {fi}", None))
        if sections:
            for si, s in enumerate(sections, 1):
                sid = f"sp-f{fi}-{si}"
                title = s.get("title") or f"第 {si} 条"
                secs.append(f'<section class="sec blk rv" style="--i:{si}" id="{sid}">'
                            f'<h3 class="blk-h"><span class="no">{si:02d}</span>{esc(title)}</h3>'
                            f'<div class="prose">{body_fragment(s, rich)}</div></section>')
                links.append((sid, title, None))
        else:
            secs.append(f'<section class="sec blk"><div class="prose">{body_fragment(e, rich)}</div></section>')

    if others:
        secs.append(f'<section class="sec" id="sp-others"><h2 class="sec-h">近期文章<span class="n">{len(others)}</span></h2>'
                    f'<ol class="alist">{article_rows(others)}</ol></section>')
        links.append(("sp-others", "近期文章", len(others)))

    content = f'<div class="feat">{"".join(secs)}</div>'
    return with_rail(rail(links, "spy", "目录"), content) if len(links) > 1 else content


# --------------------------------------------------------------------------
# page
# --------------------------------------------------------------------------

PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="dark">
<meta name="theme-color" content="#0b0b0c">
<title>__TITLE__</title>
<script>document.documentElement.classList.add("js")</script>
<style>__CSS__</style>
</head>
<body>
<header class="hd">
  <div class="hd-in">
    <div class="hd-top">
      <span class="brand">Daily Brief</span>
      <span class="hd-date">__DATE__</span>
      <span class="hd-stats">__STATS__</span>
      <span class="hd-keys"><kbd>1</kbd>–<kbd>9</kbd> 切换 <kbd>←</kbd><kbd>→</kbd> 翻页</span>
    </div>
    __TABS__
  </div>
</header>
<main>__PANELS__</main>
<footer class="ft">
  <span>本地生成 <code>__GENERATED__</code></span>
  <span>每条保留原始链接与时间戳；「AI 合并」条目可对照原始消息</span>
</footer>
<div class="ov" id="ov" aria-hidden="true" role="dialog" aria-modal="true" aria-label="详情">
  <div class="ov-p">
    <div class="ov-h"><h2></h2><button class="ov-x" type="button" aria-label="关闭">__X__</button></div>
    <div class="ov-b"></div>
  </div>
</div>
<script>__JS__</script>
</body>
</html>
"""


def _asset(name: str) -> str:
    return (ASSETS / name).read_text(encoding="utf-8")


def render(sources: dict[str, dict], digest: dict | None, output: Path, embed: bool) -> str:
    names = [n for n in ORDER if n in sources] + sorted(n for n in sources if n not in ORDER)
    day = next((sources[n].get("day") for n in names if sources[n].get("day")), "") or \
        datetime.now(CST).date().isoformat()
    _CTX["day"] = str(day)[:10]
    total = sum(len(sources[n].get("items") or []) for n in names)
    updated = sum(1 for n in names if (sources[n].get("status") or {}).get("updated_today"))

    tabs, panels = [], []

    def add(key: str, label: str, count: int, fresh: bool, body: str):
        tabs.append(f'<button class="tab" role="tab" type="button" id="t-{esc(key)}" data-tab="{esc(key)}" '
                    f'aria-controls="p-{esc(key)}" aria-selected="false" tabindex="-1">{esc(label)}'
                    f'<span class="n">{count}</span>' + ('<span class="fresh" aria-hidden="true"></span>' if fresh else "")
                    + "</button>")
        panels.append(f'<section class="panel" role="tabpanel" id="p-{esc(key)}" aria-labelledby="t-{esc(key)}">{body}</section>')

    if digest and digest.get("cards"):
        model = digest.get("model") or ""
        add("digest", "今日汇总", len(digest["cards"]), True,
            panel_head("今日汇总", "跨源合并与排序" + (f" · {model}" if model else ""), None)
            + render_digest_panel(digest))

    for name in names:
        payload = sources[name]
        items = payload.get("items") or []
        label, sub = SOURCE_META.get(name, (payload.get("source_name") or name, ""))
        status = payload.get("status") or {}
        extra_meta = ""

        if name == "telegram":
            body = render_telegram_panel(items, embed, output.parent)
            sub = f"{sub} · {len({(e.get('extra') or {}).get('channel') for e in items})} 个频道"
        elif name == "linuxdo":
            issue = next(((e.get("extra") or {}).get("issue_title") for e in items
                          if (e.get("extra") or {}).get("issue_title")), "")
            sub = re.sub(r"\s*·\s*linux\.do 晚报\s*$", "", issue) or sub
            body = render_linuxdo_panel(items)
        elif name == "sspai":
            body = render_sspai_panel(items)
        elif name == "hackernews":
            body = render_hn_board(items)
        elif name == "weibo":
            body = render_weibo_board(items)
            if items:
                extra_meta = f'<span><span class="k">抓取 </span><span class="v">{esc(fmt_time(items[0].get("published_at")))}</span></span>'
                status = {k: v for k, v in status.items() if k != "latest_published_at"}
        elif name == "zhihu":
            body = render_zhihu_board(items)
        else:
            body = render_article_panel(items)

        add(name, label, len(items), bool(status.get("updated_today")),
            panel_head(PANEL_TITLE.get(name, label), sub, status, extra_meta)
            + (body if items else '<p class="empty">今日无数据</p>'))

    tab_html = scroller("".join(tabs) + '<span class="tab-ink" aria-hidden="true"></span>', "tabs", "信源")
    tab_html = tab_html.replace('<div class="scroller-track" aria-label="信源">',
                                '<div class="scroller-track" role="tablist" aria-label="信源">')
    stats = f'<b>{total}</b> 条 · <b>{updated}</b>/{len(names)} 源今日更新'
    return (PAGE
            .replace("__TITLE__", esc(f"Daily Brief · {fmt_day(day)}"))
            .replace("__CSS__", _asset("digest.css"))
            .replace("__JS__", _asset("digest.js"))
            .replace("__DATE__", esc(fmt_day(day)))
            .replace("__STATS__", stats)
            .replace("__TABS__", tab_html)
            .replace("__X__", ICON["x"])
            .replace("__GENERATED__", esc(datetime.now(CST).strftime("%Y-%m-%d %H:%M")))
            .replace("__PANELS__", "".join(panels)))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--indir", type=Path, default=Path("data/processed"))
    parser.add_argument("--fallback", type=Path, default=Path("data/collected"))
    parser.add_argument("--output", type=Path, default=Path("site/digest.html"))
    parser.add_argument("--no-embed", action="store_true",
                        help="link images instead of inlining them (smaller file, not portable)")
    args = parser.parse_args()

    indir = args.indir if args.indir.exists() else args.fallback
    sources: dict[str, dict] = {}
    for path in sorted(indir.glob("*.json")):
        if path.name in ("index.json", "digest.json", "telegram_duplicate_groups.json"):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "items" in payload:
            sources[path.stem] = payload

    digest = None
    digest_path = indir / "digest.json"
    if digest_path.exists():
        try:
            digest = json.loads(digest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            digest = None

    if not sources:
        print(f"no data in {indir}; run tools/collect.py first")
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(sources, digest, args.output, not args.no_embed), encoding="utf-8")
    size = args.output.stat().st_size / 1024
    print(f"{args.output}  ({size:.0f} KiB, {len(sources)} sources"
          + (", digest panel" if digest and digest.get("cards") else "") + ")")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

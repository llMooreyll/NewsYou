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
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse, quote

CST = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parent.parent
ASSETS = Path(__file__).resolve().parent / "digest_assets"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ai_stage  # noqa: E402
import config  # noqa: E402
import console  # noqa: E402

# name -> (tab label, subtitle)
SOURCE_META = {
    "sspai": ("少数派", "派早报全文 · 近期文章"),
    "telegram": ("Telegram", "订阅频道当日消息"),
    "linuxdo": ("linux.do", "社区晚报"),
    "latepost": ("晚点", "LatePost 深度报道"),
    "geekpark": ("极客公园", "科技资讯"),
    "solidot": ("奇客 Solidot", "传递最新科技情报"),
    "ruanyifeng": ("阮一峰", "科技爱好者周刊"),
    "anywayfm": ("Anyway.Now", "设计与工具链接"),
    "hackernews": ("Hacker News", "Top stories"),
    "v2ex": ("V2EX", "热帖榜 · 按回复数"),
    "reddit": ("Reddit", "订阅板块热帖"),
    "weibo": ("微博热搜", "实时榜"),
    "zhihu": ("知乎热榜", "全站榜"),
}
ORDER = list(SOURCE_META)


def apply_config(cfg) -> None:
    """Overlay configured panel order and labels onto the built-in defaults.

    Subtitles stay in code — they describe what a panel contains, which is not
    a user preference — while the tab label and the order come from config, so
    renaming or reordering a panel needs no code change.
    """
    global ORDER, SOURCE_META
    labels = dict(cfg.get("sources.labels") or {})
    order = [name for name in (cfg.get("sources.order") or [])]
    merged: dict[str, tuple[str, str]] = {}
    for name in order:
        builtin_label, builtin_sub = SOURCE_META.get(name, (None, ""))
        merged[name] = (labels.get(name) or builtin_label or name, builtin_sub)
    for name, (label, sub) in SOURCE_META.items():      # anything not in order
        merged.setdefault(name, (labels.get(name) or label, sub))
    SOURCE_META = merged
    ORDER = list(merged)

# Panel headings where the full name does not fit a tab.
PANEL_TITLE = {"linuxdo": "linux.do 晚报", "latepost": "晚点 LatePost", "ruanyifeng": "阮一峰的网络日志"}


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
    "sun": '<svg class="sun" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"><circle cx="8" cy="8" r="3"/><path d="M8 1.5v1.6M8 12.9v1.6M1.5 8h1.6M12.9 8h1.6M3.4 3.4l1.1 1.1M11.5 11.5l1.1 1.1M12.6 3.4l-1.1 1.1M4.5 11.5l-1.1 1.1"/></svg>',
    "moon": '<svg class="moon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"><path d="M13.5 9.6A5.8 5.8 0 0 1 6.4 2.5a5.8 5.8 0 1 0 7.1 7.1Z"/></svg>',
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

def item_anchor(entry: dict) -> str:
    """The DOM id a citation points at.

    Stage 1 stamps `id` on every collected item. Data collected before that
    field existed has none, so the derivation is repeated here rather than
    requiring a re-collect just to render a page; both paths agree.

    The id can contain colons (`telegram:zaihuapd:44363`), which is legal in an
    HTML id but not in a CSS selector — look the element up with
    getElementById, never with querySelector.
    """
    return "it-" + str(entry.get("id") or ai_stage.item_id(entry))


# --------------------------------------------------------------------------
# HTML sanitising
#
# Bodies come from extractors and from Telegram's own markup. The output is a
# shared file, so this is the last line of defence: allowlisted tags only, no
# attributes except http(s) hrefs, and tags are balanced on the way out so a
# fragment cut mid-element cannot leak formatting into the rest of the page.
# --------------------------------------------------------------------------

_ALLOWED_TAGS = {"p", "br", "h2", "h3", "h4", "strong", "b", "em", "i",
                 "code", "pre", "blockquote", "ul", "ol", "li", "a", "img"}
_DROP_CONTENT_TAGS = {"script", "style", "iframe", "noscript", "svg", "form",
                      "button", "input", "select", "textarea", "figure", "video"}
_VOID_TAGS = {"img", "input", "br", "hr", "source", "wbr"}
_TAG_RE = re.compile(r"<\s*(/?)\s*([a-zA-Z][a-zA-Z0-9-]*)([^>]*?)(/?)\s*>")
_HREF_RE = re.compile(r"href\s*=\s*(\"([^\"]*)\"|'([^']*)')", re.I)
_SRC_RE = re.compile(r"src\s*=\s*(\"([^\"]*)\"|'([^']*)')", re.I)
_ALT_RE = re.compile(r"alt\s*=\s*(\"([^\"]*)\"|'([^']*)')", re.I)


def _attr(pattern, attrs: str) -> str | None:
    match = pattern.search(attrs or "")
    if not match:
        return None
    value = match.group(2) if match.group(2) is not None else match.group(3) or ""
    return html.unescape(value).strip()

def _safe_url(url) -> str | None:
    """A URL that is safe to put in an href, or None."""
    text = str(url or "").strip()
    return text if text.startswith(("http://", "https://")) else None

def _safe_href(attrs: str) -> str | None:
    url = _attr(_HREF_RE, attrs)
    return url if url and url.startswith(("http://", "https://")) else None

def _safe_src(attrs: str) -> str | None:
    """Only remote http(s). A data: URL here would be an inline payload of
    unknown size arriving from a page we do not control."""
    url = _attr(_SRC_RE, attrs)
    return url if url and url.startswith(("http://", "https://")) else None


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
        if name == "img":
            # Body images stay as remote URLs: inlining them would add tens of
            # megabytes (measured ~248 KiB each, 6-11 per article), and the
            # digest is meant to be one shareable file.
            src = _safe_src(attrs)
            if src:
                alt = _attr(_ALT_RE, attrs) or ""
                out.append(f'<img src="{esc(src)}" alt="{esc(alt)}" loading="lazy" decoding="async">')
        elif name == "br":
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
        if status.get("absent"):
            meta.append('<span class="fresh-tag miss-tag">未采集</span>')
        elif status.get("updated_today"):
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


def _rail_btn(key: str, label: str, count, attr: str, cls: str = "") -> str:
    return (f'<button class="rail-a{cls}" type="button" {attr}="{esc(key)}" title="{esc(label)}">'
            f'<span class="lbl">{esc(label)}</span>'
            + (f'<span class="n">{count}</span>' if count is not None else "")
            + "</button>")


def rail(links: list[tuple], mode: str, heading: str) -> str:
    """links: (key, label, count) or (key, label, count, children).

    Children are rendered one level down and indented, so the rail mirrors the
    page outline (e.g. 派早报 > its news items, next to 近期文章).
    """
    attr = "data-target" if mode == "spy" else "data-filter"
    out = []
    for i, link in enumerate(links):
        key, label, count = link[:3]
        children = link[3] if len(link) > 3 else []
        cls = " on" if i == 0 else ""
        if children:
            subs = "".join(_rail_btn(k, l, c, attr, " sub") for k, l, c in children)
            out.append(f'<div class="rail-g">{_rail_btn(key, label, count, attr, cls)}'
                       f'<div class="rail-sub">{subs}</div></div>')
        else:
            out.append(_rail_btn(key, label, count, attr, cls))
    inner = f'<nav class="rail-list" aria-label="{esc(heading)}">{"".join(out)}<span class="rail-ink"></span></nav>'
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

def _channel_name(ref: dict) -> str:
    """What to show for a channel: its display name, else @<id>.

    The display name comes from the t.me page header ("在花", not "zaihuapd").
    A few channels are named in English or mix scripts; whatever the page says
    is shown, and the id remains the fallback so a missing title never renders
    as blank.
    """
    channel = ref.get("channel")
    if not channel:
        return ""
    return ref.get("channel_title") or ("@" + str(channel))

def _ref_name(ref: dict) -> str:
    if ref.get("channel"):
        return _channel_name(ref)
    return ref.get("source_name") or SOURCE_META.get(ref.get("source") or "", (ref.get("source") or "",))[0]


def _cite(ref: dict) -> str:
    """A citation chip that moves the page rather than opening a tab.

    The digest panel is a view over items that live in other panels, so the
    useful action is a jump, not a link out. One chip per item: a source cited
    twice shows twice, which is the point when each chip lands somewhere else.

    `href` keeps it usable with no JS; the script intercepts the click, because
    boot reads location.hash as a tab key and an item id in there would send a
    reload to the wrong place.
    """
    key = ref.get("id")
    name = _ref_name(ref)
    if not key or not name:
        return ""
    key = str(key)
    # "去…看原文" rather than the bare source name: the chip is an action, and
    # a lone "少数派" next to a summary reads like a label, not a link. The
    # source name is set apart so a long one ("LoopDNS资讯播报") still parses
    # at a glance, and the arrow marks it as something that leaves this line.
    label = f'去<span class="src">{esc(name)}</span>看原文{ICON["out"]}'
    return (f'<a class="cite" href="#it-{esc(key)}" data-jump="{esc(key)}" '
            f'title="{esc(ref.get("title") or name)}">{label}</a>')

def _cites(refs) -> str:
    """One chip per distinct item, in the order given."""
    out, seen = [], set()
    for ref in refs or []:
        key = str(ref.get("id") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        chip = _cite(ref)
        if chip:
            out.append(chip)
    return "".join(out)

def _category_sections(categories: list[dict]) -> str:
    """One section per category: a heading, its summary line, then its items.

    These rows carry no `it-` anchor of their own. Those ids belong to the
    source panels, and an id may appear only once in a document — the chip here
    is what points at the item, not the row it sits in.
    """
    out = []
    for category in categories or []:
        refs = category.get("refs") or []
        rows = "".join(
            f'<li class="tp rv" style="--i:{i}">'
            f'<h3 class="tp-t">{ext(ref.get("url"), esc(ref.get("title")))}</h3>'
            f'<div class="cat-r">{_cite(ref)}</div></li>'
            for i, ref in enumerate(refs, 1))
        out.append(
            f'<section class="sec" id="{esc(category.get("anchor"))}">'
            f'<h2 class="sec-h">{esc(category.get("name"))}'
            f'<span class="n">{len(refs)}</span></h2>'
            + (f'<p class="cat-s">{esc(category.get("summary"))}</p>'
               if category.get("summary") else "")
            + f'<ol class="topics">{rows}</ol></section>')
    return "".join(out)

def _side_section(anchor: str, heading: str, side: dict) -> str:
    """The 分类 or 讨论 block: one heading, then one section per category.

    The coverage line is deliberate: a category list that quietly accounts for
    half the day looks like one that accounts for all of it, and the difference
    is what tells the reader whether the grouping was any good.
    """
    categories = (side or {}).get("categories") or []
    if not categories:
        return ""
    total = (side or {}).get("item_count") or 0
    classified = (side or {}).get("classified") or 0
    aside = f'<span class="aside">已归类 {classified}/{total}</span>' if total else ""
    return (f'<section class="sec" id="{esc(anchor)}">'
            f'<h2 class="sec-h">{esc(heading)}'
            f'<span class="n">{len(categories)}</span>{aside}</h2>'
            + _category_sections(categories) + "</section>")

def _keyword_cloud(keywords: list[dict]) -> str:
    """Keywords as plain labels whose size follows the model's own weight.

    The weight is the model's judgement of how much each word matters to the
    day, so it drives the type size directly: `--w` is 0–100 and the stylesheet
    maps it to a size. No fixed ladder of classes, because the ranking is the
    model's to make, not the stylesheet's.
    """
    cells = "".join(
        f'<span style="--w:{int(word.get("weight") or 0)}">{esc(word.get("text"))}</span>'
        for word in keywords or [] if word.get("text"))
    return f'<div class="dg-kw">{cells}</div>' if cells else ""

def _mark_keywords(text: str, words) -> str:
    """Wrap the first occurrence of each keyword inside its own paragraph.

    The model names the words a paragraph is about; marking them in place is
    what ties the word cloud to the prose — otherwise the two read as unrelated
    blocks. Matching happens on the escaped text so the result is always valid
    HTML, spans are found on the untouched string and applied in one pass, and
    a word the model paraphrased away is simply skipped rather than treated as
    an error: the marking is a reading aid, not a contract.
    """
    escaped = esc(text)
    spans: list[tuple[int, int]] = []
    for word in sorted({str(w) for w in words or [] if w}, key=len, reverse=True):
        needle = esc(word)
        start = 0
        while True:
            at = escaped.find(needle, start)
            if at < 0:
                break
            end = at + len(needle)
            # Longest first, so "AI 应用" wins over "AI"; overlapping matches
            # are skipped rather than nested.
            if not any(at < b and a < end for a, b in spans):
                spans.append((at, end))
                break
            start = at + 1
    if not spans:
        return escaped
    spans.sort()
    out, pos = [], 0
    for a, b in spans:
        out.append(escaped[pos:a])
        out.append(f'<mark class="kw">{escaped[a:b]}</mark>')
        pos = b
    out.append(escaped[pos:])
    return "".join(out)

def _summary_sources(refs) -> str:
    """The source labels drawn beside one paragraph.

    A source name plus the out-arrow, and nothing else: the paragraph already
    says what happened, so the link only has to say where to check it. The title
    is carried in `title=` and revealed on hover, which keeps the resting state
    to a couple of short labels instead of four article headlines.

    The wrapper is part of the paragraph's own block rather than a floating
    overlay — see the CSS: a tooltip that disappears the moment the pointer
    leaves the text cannot be clicked, which defeats the point of showing it.
    """
    out = []
    for ref in refs or []:
        name = _ref_name(ref)
        if not name:
            continue
        # Same guard the other external links use: only http(s) becomes an
        # href, so a javascript: or data: URL in the data cannot become one.
        target = _safe_url(ref.get("url"))
        title = ref.get("title") or name
        if target:
            out.append(f'<a class="dg-src" href="{esc(target)}" '
                       f'target="_blank" rel="noopener noreferrer" '
                       f'title="{esc(title)}">{esc(name)}{ICON["out"]}</a>')
        else:
            out.append(f'<span class="dg-src" title="{esc(title)}">'
                       f'{esc(name)}{ICON["out"]}</span>')
    return f'<span class="dg-srcs">{"".join(out)}</span>' if out else ""


def _summary_block(summary: list[dict]) -> str:
    """The day's summary: one paragraph per topic, keywords marked in place.

    Each paragraph is its own block holding both the prose and the sources it
    was written from. Keeping them in one element is what makes the labels
    reachable: moving the pointer from the text onto a label never leaves the
    block, so nothing vanishes under the cursor.
    """
    paragraphs = []
    for index, para in enumerate(summary or [], 1):
        text = (para.get("text") or "").strip()
        if not text:
            continue
        paragraphs.append(
            f'<div class="dg-hl rv" style="--i:{index}">'
            f'<p class="dg-p">{_mark_keywords(text, para.get("keywords"))}</p>'
            f'{_summary_sources(para.get("refs"))}</div>')
    return f'<div class="dg-sum">{"".join(paragraphs)}</div>' if paragraphs else ""

def render_digest_panel(digest: dict | None, categories: dict | None = None) -> str:
    """今日汇总: the day's summary, then the news categories, then discussion.

    The two inputs come from different files and either may be missing. A
    classification that failed must not cost the summary, and a summary that
    failed must not cost the categories, so neither is a precondition for the
    other.
    """
    digest = digest or {}
    summary = digest.get("summary") or []
    if isinstance(summary, str):                  # a hand-written panel file
        summary = [{"text": summary}] if summary.strip() else []
    keywords = digest.get("keywords") or []
    sides = (categories or {}).get("sides") or {}
    news = sides.get("news") or {}
    discussion = sides.get("discussion") or {}
    news_cats = news.get("categories") or []
    disc_cats = discussion.get("categories") or []
    if not summary and not keywords and not news_cats and not disc_cats:
        return '<p class="empty">今日无汇总条目</p>'

    secs: list[str] = []
    if summary or keywords:
        secs.append(f'<section class="sec" id="dg-summary">'
                    f'<h2 class="sec-h">汇总</h2>'
                    f'{_summary_block(summary)}{_keyword_cloud(keywords)}</section>')

    for anchor, heading, side in (("dg-cats", "新闻", news), ("dg-disc", "讨论", discussion)):
        block = _side_section(anchor, heading, side)
        if block:
            secs.append(block)

    notes = []
    if digest.get("stats_note"):
        notes.append(esc(digest["stats_note"]))
    if digest.get("model"):
        notes.append("AI · " + esc(digest["model"]))
    if notes:
        secs.append(f'<p class="dg-note">{"<br>".join(notes)}</p>')

    links: list[tuple] = []
    if summary or keywords:
        links.append(("dg-summary", "汇总", len(keywords) or None))
    if news_cats:
        links.append(("dg-cats", "新闻", news.get("classified"),
                      [(c.get("anchor"), c.get("name"), len(c.get("refs") or []))
                       for c in news_cats]))
    if disc_cats:
        links.append(("dg-disc", "讨论", discussion.get("classified"),
                      [(c.get("anchor"), c.get("name"), len(c.get("refs") or []))
                       for c in disc_cats]))
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
        kept = []
        for para in re.split(r"\n\s*\n", body):
            kept += [esc(l) for l in para.split("\n") if l.strip()] + [""]

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
            body += f'<button class="more" type="button" aria-haspopup="dialog">展开全文{ICON["out"]}</button>'

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
                f'<div class="og"><div class="og-h"><span class="ch">{esc(_channel_name(orig))}</span>'
                f'<time>{esc(fmt_time(orig.get("published_at")))}</time>'
                f'{ext(orig.get("url"), "原文 ↗")}</div><div class="tg-b">{body_o}</div></div>')
        tpl = (f'<template id="og-{index}"><p class="ov-note">{esc(entry.get("title"))}</p>'
               f'{"".join(blocks)}</template>')

    ai = f'<span class="tg-ai">AI 合并 {len(entry.get("merged_from") or [])}</span>' if merged else ""
    srcs = ""
    if merged and extra.get("merged_sources"):
        srcs = '<div class="tg-src"><span>来源</span>' + "".join(
            ext(m.get("url"), esc(_channel_name(m))) for m in extra["merged_sources"]) + "</div>"
    card = (f'<article class="tg" id="{item_anchor(entry)}" data-ch="{esc(channel)}">'
            f'<div class="tg-h">{ext("https://t.me/" + channel, esc(extra.get("channel_title") or ("@" + channel)), "tg-ch")}{ai}'
            f'<time>{esc(fmt_time(entry.get("published_at")))}</time></div>'
            f'{shots}{quote_html}{title}{body}{pill}{tags}{srcs}{cmp_btn}'
            f'<div class="tg-f">{views}{rx}{ext(entry.get("url"), "原帖" + ICON["out"], "go")}</div>'
            "</article>")
    return card, tpl


def _channel_note(status: dict | None) -> str:
    """Failed or empty channels, from the collector's per-channel report."""
    report = ((status or {}).get("collector_report") or {}).get("channels") or {}
    failed = [c for c, r in report.items() if not r.get("ok")]
    empty = [c for c, r in report.items() if r.get("ok") and not r.get("kept")]

    def label(channel: str) -> str:
        return esc(report[channel].get("title") or ("@" + channel))

    rows = []
    if failed:
        rows.append('<p class="rail-note bad"><b>抓取失败</b>' + "".join(
            f'<span title="{esc(report[c].get("error"))}">{label(c)}</span>' for c in failed) + "</p>")
    if empty:
        rows.append('<p class="rail-note"><b>当日无消息</b>' + "".join(
            f'<span title="最新 {esc(report[c].get("newest_day") or "无")}">{label(c)}</span>' for c in empty) + "</p>")
    return "".join(rows)


def render_telegram_panel(items: list[dict], embed: bool, out_dir: Path, status: dict | None = None) -> str:
    cards, tpls = [], []
    counts: dict[str, int] = {}
    # channel -> display name, so the filter rail reads "在花" rather than
    # "@zaihuapd". The rail's data-filter value stays the raw id: filtering
    # matches on the id, only the visible text changes.
    titles: dict[str, str] = {}
    for index, entry in enumerate(items, 1):
        card, tpl = _tg_card(entry, index, embed, out_dir)
        cards.append(card)
        tpls.append(tpl)
        extra = entry.get("extra") or {}
        ch = extra.get("channel") or ""
        counts[ch] = counts.get(ch, 0) + 1
        if extra.get("channel_title"):
            titles.setdefault(ch, extra["channel_title"])
    links = [("*", "全部频道", len(items))] + [
        (ch, titles.get(ch) or ("@" + ch), n)
        for ch, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    ]
    grid = f'<div class="mas">{"".join(cards)}</div>{"".join(tpls)}'
    rail_html = rail(links, "filter", "频道")
    note = _channel_note(status)
    if note:
        rail_html = rail_html.replace("</aside>", f"{note}</aside>")
    return with_rail(rail_html, grid)


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
            f'<li class="br rv{" top" if i <= 3 else ""}" id="{item_anchor(e)}" style="--i:{i}">'
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
            f'<li class="br rv{" top" if i <= 3 else ""}" id="{item_anchor(e)}" style="--i:{i}">'
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
            f'<li class="br rv{" top" if i <= 3 else ""}" id="{item_anchor(e)}" style="--i:{i}">'
            f'<span class="br-rk">{esc(x.get("rank") or i)}</span>'
            f'<span class="br-t">{ext(e.get("url"), esc(e.get("title")))}'
            + (f'<span class="br-x" title="{esc(excerpt[:400])}">{esc(excerpt[:160])}</span>' if len(excerpt) > 8 else "")
            + f'</span>{_bar(v, top)}<span class="br-v">{esc(fmt_heat(v))}</span></li>')
    legend = ('<div class="bd-legend"><span></span><span>问题</span><span class="h-bar"></span>'
              '<span style="text-align:right">热度</span></div>')
    return f'<div class="b-zh">{legend}<ol class="board">{"".join(rows)}</ol></div>'


def render_v2ex_board(items: list[dict]) -> str:
    """V2EX's hot board: reply count is the ranking, so it drives the bar.

    The node (板块) is shown because it is half of what the row means on V2EX —
    "147 回复" in 问与答 is a different thing from 147 in 程序员.
    """
    vals = [int((e.get("extra") or {}).get("replies") or 0) for e in items]
    top = max(vals or [0])
    rows = []
    for i, (e, v) in enumerate(zip(items, vals), 1):
        x = e.get("extra") or {}
        node = (x.get("node_title") or "").strip()
        date, tm = stamp(e)
        rows.append(
            f'<li class="br rv{" top" if i <= 3 else ""}" id="{item_anchor(e)}" style="--i:{i}">'
            f'<span class="br-rk">{i}</span>'
            f'<span class="br-t">{ext(e.get("url"), esc(e.get("title")))}'
            + (f'<a class="br-dom" href="https://www.v2ex.com/go/{esc(x.get("node_name"))}" '
               f'target="_blank" rel="noopener noreferrer">{esc(node)}</a>' if node else "")
            + f'</span>{_bar(v, top)}'
            f'<span class="br-v">{v}</span>'
            f'<span class="br-tm">{esc(tm or date)}</span></li>')
    legend = ('<div class="bd-legend"><span></span><span>话题</span><span class="h-bar"></span>'
              '<span style="text-align:right">回复</span>'
              '<span class="h-tm" style="text-align:right">最后回复</span></div>')
    return f'<div class="b-vx">{legend}<ol class="board">{"".join(rows)}</ol></div>'

def render_reddit_board(items: list[dict]) -> str:
    """Reddit's feeds: no score or comment count exists, so nothing is ranked.

    The order is Reddit's own `hot`, which is why the rank column is just the
    feed position. A row shows where the post points (its domain, or what kind
    of media it is) and which subreddit it came from.
    """
    KIND_LABEL = {"video": "视频", "image": "图片", "gallery": "图集", "self": "自帖"}
    rows = []
    for i, e in enumerate(items, 1):
        x = e.get("extra") or {}
        domain = (x.get("domain") or "").strip()
        kind = KIND_LABEL.get((x.get("media_kind") or "").strip(), "")
        date, tm = stamp(e)
        rows.append(
            f'<li class="br rv{" top" if i <= 3 else ""}" id="{item_anchor(e)}" style="--i:{i}">'
            f'<span class="br-rk">{i}</span>'
            f'<span class="br-t">{ext(e.get("url"), esc(e.get("title")))}'
            + (f'<span class="br-dom">{esc(domain)}</span>' if domain
               else (f'<span class="br-dom">{esc(kind)}</span>' if kind else ""))
            + f'</span>'
            f'<span class="br-sub">r/{esc(x.get("subreddit"))}</span>'
            f'<span class="br-tm">{esc(tm or date)}</span></li>')
    legend = ('<div class="bd-legend"><span></span><span>帖子</span><span></span>'
              '<span>板块</span><span class="h-tm" style="text-align:right">发布</span></div>')
    return f'<div class="b-rd">{legend}<ol class="board">{"".join(rows)}</ol></div>'

# --------------------------------------------------------------------------
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
            # A collector may hand back {"label": …, "label_url": …}; taking the
            # label is what the reader wants, and str(dict) would print the
            # whole record as the tag text.
            if isinstance(t, dict):
                t = t.get("label") or t.get("title") or t.get("name") or ""
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
            f'<li class="ar rv" id="{item_anchor(e)}" style="--i:{start + k + 1}"><div class="ar-d">{dcol}</div><div>'
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
            rows.append(f'<li class="tp rv" id="{item_anchor(e)}" style="--i:{k}"><h3 class="tp-t">{ext(e.get("url"), esc(e.get("title")))}</h3>'
                        + (f'<p class="tp-s">{esc(s)}</p>' if s else "") + "</li>")
        secs.append(f'<section class="sec" id="{sid}"><h2 class="sec-h">{esc(name)}<span class="n">{len(groups[name])}</span></h2>'
                    f'<ol class="topics">{"".join(rows)}</ol></section>')
        links.append((sid, name, len(groups[name])))
    return with_rail(rail(links, "spy", "板块"), "".join(secs))


# --------------------------------------------------------------------------
# 少数派
# --------------------------------------------------------------------------

def render_sspai_panel(items: list[dict], status: dict | None = None) -> str:
    """Two sibling sections: 派早报 and 近期文章.

    派早报's stories were split out at collection time, so each one is its own
    item carrying `issue_id` and `issue_index`. The panel regroups them by
    issue, which keeps the page reading as "one issue, its stories" while the
    classification and the citation chips see individual stories.
    """
    stories = [e for e in items if (e.get("extra") or {}).get("issue_id")]
    others = [e for e in items if not (e.get("extra") or {}).get("issue_id")]
    secs, links = [], []

    state = (status or {}).get("brief_state") or "unknown"
    if stories:
        issues: dict[str, list[dict]] = {}
        for entry in stories:
            issues.setdefault(entry["extra"]["issue_id"], []).append(entry)
        children: list[tuple] = []
        rendered = []
        for issue_id, group in issues.items():
            group.sort(key=lambda e: e["extra"].get("issue_index") or 0)
            first = group[0]["extra"]
            rich = _use_rich(group[0])
            date, tm = stamp(group[0])
            head = (f'<header class="feat-h rv"><h3>'
                    f'{ext(first.get("issue_url"), esc(first.get("issue_title")))}</h3>'
                    f'<div class="ar-m"><span>{esc(date)} {esc(tm)}</span>'
                    + (f'<span>{esc(group[0]["author"])}</span>' if group[0].get("author") else "")
                    + f'<span>{len(group)} 条</span></div></header>')
            blocks = []
            digest: list[dict] = []

            def flush_digest() -> None:
                """Draw the 简讯 roundup as one block rather than one per notice.

                Classification still sees each notice on its own — this is only
                how the panel draws them. Each keeps its `it-` anchor, so a
                citation from the summary still lands on the right notice.
                """
                if not digest:
                    return
                first = digest[0]["extra"].get("issue_index") or 0
                anchor = f"sp-digest-{first}"
                items = "".join(
                    f'<div class="digest-item" id="{item_anchor(e)}">'
                    f'<div class="prose">{body_fragment(e, rich)}</div></div>'
                    for e in digest)
                blocks.append(
                    f'<section class="sec blk rv" style="--i:{first}" id="{anchor}">'
                    f'<h4 class="blk-h"><span class="no">{first:02d}</span>简讯'
                    f'<span class="n">{len(digest)}</span></h4>'
                    f'<div class="digest-items">{items}</div></section>')
                children.append((anchor, "简讯", len(digest)))
                digest.clear()

            for entry in group:
                if (entry.get("extra") or {}).get("is_digest"):
                    digest.append(entry)
                    continue
                flush_digest()
                index = entry["extra"].get("issue_index") or 0
                blocks.append(
                    f'<section class="sec blk rv" style="--i:{index}" id="{item_anchor(entry)}">'
                    f'<h4 class="blk-h"><span class="no">{index:02d}</span>{esc(entry.get("title"))}</h4>'
                    f'<div class="prose">{body_fragment(entry, rich)}</div></section>')
                children.append((item_anchor(entry), entry.get("title"), None))
            flush_digest()
            rendered.append(f'<div class="sec feat-issue">{head}'
                            f'<div class="feat-items">{"".join(blocks)}</div></div>')
        secs.append(f'<section class="sec" id="sp-brief">'
                    f'<h2 class="sec-h">派早报<span class="n">{len(stories)}</span></h2>'
                    f'{"".join(rendered)}</section>')
        links.append(("sp-brief", "派早报", len(stories), children))
    elif state in ("suspended", "pending"):
        # No brief today. Saying which of the two it is matters: on a weekend
        # there is none by design, on a weekday morning it simply is not out.
        note = ("周末与法定假期不更新，下一期在下一个工作日 06:00–09:00 发布。"
                if state == "suspended" else "今日尚未发布，通常在 06:00–09:00 之间。")
        label = "今日停稿" if state == "suspended" else "尚未发布"
        secs.append(f'<section class="sec" id="sp-brief">'
                    f'<h2 class="sec-h">派早报<span class="aside">{label}</span></h2>'
                    f'<p class="empty">{note}</p></section>')

    if others:
        secs.append(f'<section class="sec" id="sp-others"><h2 class="sec-h">近期文章<span class="n">{len(others)}</span></h2>'
                    f'<ol class="alist">{article_rows(others)}</ol></section>')
        links.append(("sp-others", "近期文章", len(others)))

    content = f'<div class="feat">{"".join(secs)}</div>'
    return with_rail(rail(links, "spy", "目录"), content) if len(links) > 1 or (links and links[0][3:]) else content


# --------------------------------------------------------------------------
# page
# --------------------------------------------------------------------------

PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<meta name="theme-color" content="#0c0b0b">
<title>__TITLE__</title>
<script>
(function(d){var r=d.documentElement,t=null,f=null;r.classList.add("js");
try{t=localStorage.getItem("digest-theme");f=localStorage.getItem("digest-cjk-serif")}catch(e){}
if(t!=="light"&&t!=="dark")t=matchMedia("(prefers-color-scheme: light)").matches?"light":"dark";
r.dataset.theme=t;if(f==="0")r.classList.add("no-cjk-serif")})(document);
</script>
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
      <button class="theme-btn" type="button" aria-label="切换主题">__SUN____MOON__</button>
    </div>
    __TABS__
  </div>
</header>
<main>__PANELS__</main>
<footer class="ft">
  <span>本地生成 <code>__GENERATED__</code></span>
  <span>每条保留原始链接与时间戳；「AI 合并」条目可对照原始消息</span>
</footer>
<div class="ov" id="ov" aria-hidden="true"></div>
<template id="ov-panel">
  <div class="ov-p" role="dialog" aria-modal="true">
    <div class="ov-h"><button class="ov-back" type="button" aria-label="返回">__BACK__<span>返回</span></button><h2></h2>
      <button class="ov-x" type="button" aria-label="关闭">__X__</button></div>
    <div class="ov-b"></div>
  </div>
</template>
<script>__JS__</script>
</body>
</html>
"""


def _asset(name: str) -> str:
    return (ASSETS / name).read_text(encoding="utf-8")


def absent_panel(info: dict) -> str:
    """What a source's tab shows when this run produced nothing for it.

    A failed source used to leave no trace in the page: its tab simply vanished,
    and the header still counted only the sources that arrived, so a run where
    three sources broke looked like a complete run of seven. Saying so is the
    point — the reader should be able to tell "nothing was published" from
    "we could not fetch it".
    """
    reason = info.get("reason") or "missing"
    headline, hint = {
        "failed": ("抓取失败", "本次未能取得数据，下方为空白而非旧内容"),
        "stale": ("今日无新数据", "上一次采集早于今天，已跳过以免旧内容混入"),
    }.get(reason, ("今日无数据", "本次未采集到该源的内容"))
    detail = info.get("detail") or ""
    return (
        '<div class="absent">'
        f'<p class="absent-h">{esc(headline)}</p>'
        f'<p class="absent-s">{esc(hint)}</p>'
        + (f'<p class="absent-d">{esc(detail)}</p>' if detail else "")
        + "</div>"
    )


def _digest_count(digest: dict | None, categories: dict | None) -> int:
    """What the 今日汇总 tab counts: the keywords when there are any, else the
    number of categories the classification produced."""
    keywords = (digest or {}).get("keywords") or []
    if keywords:
        return len(keywords)
    return sum(len((side or {}).get("categories") or [])
               for side in ((categories or {}).get("sides") or {}).values())

def render(sources: dict[str, dict], digest: dict | None, output: Path, embed: bool,
           absent: dict | None = None, categories: dict | None = None) -> str:
    absent = absent or {}
    names = [n for n in ORDER if n in sources] + sorted(n for n in sources if n not in ORDER)
    # A source with no data still gets a tab, so its absence is visible rather
    # than inferred from a count the reader cannot see.
    for name in absent:
        if name not in names:
            names.append(name)
    day = next((sources[n].get("day") for n in names
                if n in sources and sources[n].get("day")), "") or \
        datetime.now(CST).date().isoformat()
    _CTX["day"] = str(day)[:10]
    total = sum(len(sources[n].get("items") or []) for n in names if n in sources)
    updated = sum(1 for n in names if n in sources
                  and (sources[n].get("status") or {}).get("updated_today"))

    tabs, panels = [], []

    def add(key: str, label: str, count, fresh: bool, body: str, broken: bool = False):
        tabs.append(f'<button class="tab" role="tab" type="button" id="t-{esc(key)}" data-tab="{esc(key)}" '
                    f'aria-controls="p-{esc(key)}" aria-selected="false" tabindex="-1">{esc(label)}'
                    f'<span class="n">{count}</span>'
                    + ('<span class="fresh" aria-hidden="true"></span>' if fresh else "")
                    + ('<span class="miss" aria-hidden="true"></span>' if broken else "")
                    + "</button>")
        panels.append(f'<section class="panel" role="tabpanel" id="p-{esc(key)}" aria-labelledby="t-{esc(key)}">{body}</section>')

    if (digest and (digest.get("summary") or digest.get("keywords"))) \
            or ai_stage.has_categories(categories):
        model = (digest or {}).get("model") or ""
        add("digest", "今日汇总", _digest_count(digest, categories), True,
            panel_head("今日汇总", "跨源合并与排序" + (f" · {model}" if model else ""), None)
            + render_digest_panel(digest, categories))

    for name in names:
        if name in absent and name not in sources:
            info = absent[name]
            add(name, info.get("label") or name, 0, False,
                panel_head(PANEL_TITLE.get(name, info.get("label") or name),
                           "本次未采集", {"absent": True}) + absent_panel(info),
                broken=True)
            continue
        payload = sources[name]
        items = payload.get("items") or []
        label, sub = SOURCE_META.get(name, (payload.get("source_name") or name, ""))
        status = payload.get("status") or {}
        extra_meta = ""

        if name == "telegram":
            body = render_telegram_panel(items, embed, output.parent, status)
            sub = f"{sub} · {len({(e.get('extra') or {}).get('channel') for e in items})} 个频道"
        elif name == "linuxdo":
            issue = next(((e.get("extra") or {}).get("issue_title") for e in items
                          if (e.get("extra") or {}).get("issue_title")), "")
            sub = re.sub(r"\s*·\s*linux\.do 晚报\s*$", "", issue) or sub
            body = render_linuxdo_panel(items)
        elif name == "sspai":
            body = render_sspai_panel(items, status)
        elif name == "hackernews":
            body = render_hn_board(items)
        elif name == "weibo":
            body = render_weibo_board(items)
            if items:
                extra_meta = f'<span><span class="k">抓取 </span><span class="v">{esc(fmt_time(items[0].get("published_at")))}</span></span>'
                status = {k: v for k, v in status.items() if k != "latest_published_at"}
        elif name == "zhihu":
            body = render_zhihu_board(items)
        elif name == "v2ex":
            body = render_v2ex_board(items)
        elif name == "reddit":
            body = render_reddit_board(items)
        else:
            body = render_article_panel(items)

        add(name, label, len(items), bool(status.get("updated_today")),
            panel_head(PANEL_TITLE.get(name, label), sub, status, extra_meta)
            + (body if items else '<p class="empty">今日无数据</p>'))

    tab_html = scroller("".join(tabs) + '<span class="tab-ink" aria-hidden="true"></span>', "tabs", "信源")
    tab_html = tab_html.replace('<div class="scroller-track" aria-label="信源">',
                                '<div class="scroller-track" role="tablist" aria-label="信源">')
    stats = f'<b>{total}</b> 条 · <b>{updated}</b>/{len(names)} 源今日更新'
    if absent:
        stats += f' · <b class="miss-n">{len(absent)}</b> 源未取得'
    return (PAGE
            .replace("__TITLE__", esc(f"Daily Brief · {fmt_day(day)}"))
            .replace("__CSS__", _asset("digest.css"))
            .replace("__JS__", _asset("digest.js"))
            .replace("__DATE__", esc(fmt_day(day)))
            .replace("__STATS__", stats)
            .replace("__TABS__", tab_html)
            .replace("__X__", ICON["x"])
            .replace("__BACK__", ICON["l"])
            .replace("__SUN__", ICON["sun"])
            .replace("__MOON__", ICON["moon"])
            .replace("__GENERATED__", esc(datetime.now(CST).strftime("%Y-%m-%d %H:%M")))
            .replace("__PANELS__", "".join(panels)))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--indir", type=Path, default=None)
    parser.add_argument("--fallback", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--no-embed", action="store_true",
                        help="link images instead of inlining them (smaller file, not portable)")
    config.add_arguments(parser)
    args = parser.parse_args()

    cfg = config.resolve_or_exit(args, cli={
        "paths.processed": args.indir,
        "paths.collected": args.fallback,
        "output.digest": args.output,
    })
    if config.emit_show_config(cfg, args):
        return 0
    args.indir = Path(cfg.get("paths.processed"))
    args.fallback = Path(cfg.get("paths.collected"))
    args.output = Path(cfg.get("output.digest"))
    apply_config(cfg)

    indir = args.indir if args.indir.exists() else args.fallback
    sources: dict[str, dict] = {}
    for path in sorted(indir.glob("*.json")):
        if path.name in ("index.json", "digest.json", "categories.json",
                         "telegram_duplicate_groups.json"):
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

    # Stage 2c/2d output, kept out of digest.json so a failed classification
    # cannot cost the briefing and the other way round. An empty file reads as
    # "nothing was classified", not as an empty panel.
    categories = None
    categories_path = indir / "categories.json"
    if categories_path.exists():
        try:
            categories = json.loads(categories_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            categories = None
    if not ai_stage.has_categories(categories):
        categories = None

    # Sources stage 2 recorded as having produced nothing today. Kept so their
    # tabs can say why instead of silently disappearing.
    absent: dict[str, dict] = {}
    index_path = indir / "index.json"
    if index_path.exists():
        try:
            absent = (json.loads(index_path.read_text(encoding="utf-8")) or {}).get("absent") or {}
        except json.JSONDecodeError:
            absent = {}
    # A source that produced data is present regardless of what the index says.
    absent = {n: v for n, v in absent.items() if n not in sources}

    output = console.reporter()
    if not sources:
        output.banner("渲染")
        output.error(f"{indir} 里没有数据；先运行 make collect")
        output.close()
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(sources, digest, args.output, not args.no_embed, absent,
                                  categories), encoding="utf-8")
    size = args.output.stat().st_size / 1024
    has_panel = bool(digest and (digest.get("summary") or digest.get("keywords"))) \
        or ai_stage.has_categories(categories)
    output.banner("渲染", f"{len(sources)} 个源", f"{size:.0f} KiB",
                  f"{len(absent)} 个源无数据" if absent else "",
                  "含汇总面板" if has_panel else "无汇总面板")
    # The path stays on stdout on its own line: it is what a caller wants from
    # this tool, and everything a person wants is on stderr.
    output.close()
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

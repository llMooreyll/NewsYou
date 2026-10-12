#!/usr/bin/env python3
"""Stage 3: render processed data into one self-contained HTML file.

    python tools/render_digest.py --indir data/processed --output site/digest.html

Design
------
One file, zero external requests: CSS, JS and images are inlined, so the output
can be shared as a single attachment.

Layout rules that come from actual usability problems:

- Tabs wrap instead of scrolling, so no source is ever clipped.
- Telegram cards use CSS Grid, not multi-column. Expanding content inside a
  multi-column layout reflows the whole column, which makes cards jump; a grid
  keeps every card in place.
- The original messages behind an AI merge open in an overlay, not inline, so
  nothing on the page moves at all.
- Full-text disclosure animates with grid-template-rows 0fr -> 1fr, which is a
  real height transition rather than a display flip.

Theme: light and dark, following the system preference, with a manual toggle
persisted in localStorage.
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import mimetypes
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

CST = timezone(timedelta(hours=8))

SOURCE_META = {
    "sspai": ("少数派", "派早报全文 · 其余标题"),
    "telegram": ("Telegram", "当日全部消息"),
    "linuxdo": ("linux.do 晚报", "六个板块"),
    "latepost": ("晚点 LatePost", "深度报道"),
    "geekpark": ("极客公园", "科技资讯"),
    "ruanyifeng": ("阮一峰", "科技周刊"),
    "anywayfm": ("Anyway.Now", "设计链接集"),
    "hackernews": ("Hacker News", "top stories"),
    "weibo": ("微博热搜", "实时榜"),
    "zhihu": ("知乎热榜", "全站榜"),
}
ORDER = list(SOURCE_META)

KIND_LABEL = {
    "newsletter": "周刊", "article": "文章", "link": "链接",
    "topic": "帖子", "hotsearch": "热搜", "message": "消息",
}

DATE_SOURCE_LABEL = {
    "collection_time": "抓取时间", "relative_balloon": "页面相对时间",
    "archive_month": "归档月份", "api_relative": "接口相对时间",
    "issue_date": "出刊日", "atom": "RSS", "rss": "RSS", "api": "接口",
    "page": "页面", "post": "帖文", "article_page": "文章页",
}

TOPIC_LABEL = {
    "technology": "科技", "ai": "AI", "business": "商业", "policy": "政策",
    "society": "社会", "security": "安全", "internet": "互联网", "other": "其他",
}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def fmt_time(value) -> str:
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)[:16]
    return dt.astimezone(CST).strftime("%H:%M")


def fmt_date(value) -> str:
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)[:10]
    return dt.astimezone(CST).strftime("%m-%d")


def fmt_day(value) -> str:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(CST)
        return f"{dt.year} 年 {dt.month} 月 {dt.day} 日"
    except (ValueError, TypeError):
        return str(value or "")


def linkify(text: str) -> str:
    out = esc(text)
    out = re.sub(r"(https?://[^\s<]+)", r'<a class="lnk" href="\1" rel="noopener noreferrer">\1</a>', out)
    return out.replace("\n", "<br>")


def paragraphs(text: str) -> str:
    if not text:
        return ""
    out = []
    for chunk in [c.strip() for c in re.split(r"\n{2,}", text) if c.strip()]:
        if re.match(r"^#{1,4}\s", chunk):
            out.append(f'<h4 class="h">{esc(chunk.lstrip("# ").strip())}</h4>')
        else:
            out.append(f"<p>{esc(chunk)}</p>")
    return "".join(out)


def data_uri(path: Path) -> str:
    raw = path.read_bytes()
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def chips(entry: dict) -> str:
    extra = entry.get("extra") or {}
    out = []
    if extra.get("section_name"):
        out.append(f'<span class="chip accent">{esc(extra["section_name"])}</span>')
    if extra.get("rank"):
        out.append(f'<span class="chip accent">#{esc(extra["rank"])}</span>')
    if extra.get("heat"):
        out.append(f'<span class="chip">{esc(str(extra["heat"])[:14])}</span>')
    if extra.get("score"):
        out.append(f'<span class="chip">{esc(extra["score"])} 分</span>')
    if extra.get("comments"):
        out.append(f'<span class="chip">{esc(extra["comments"])} 评论</span>')
    if entry.get("author"):
        out.append(f'<span class="chip">{esc(entry["author"])}</span>')
    return "".join(out)


def meta_row(entry: dict, *, with_date: bool = False) -> str:
    when = fmt_time(entry.get("published_at"))
    if with_date and entry.get("published_at"):
        when = f'{fmt_date(entry["published_at"])} {when}'
    src = DATE_SOURCE_LABEL.get(entry.get("date_source", ""), "")
    parts = []
    if when:
        parts.append(f"<time>{esc(when)}</time>")
    if src:
        parts.append(f'<span class="src">{esc(src)}</span>')
    parts.append(chips(entry))
    return f'<div class="meta">{"".join(parts)}</div>'


def freshness(status: dict) -> str:
    if not status:
        return ""
    if status.get("updated_today"):
        return '<span class="badge ok">今日已更新</span>'
    days = status.get("staleness_days")
    if days is None:
        return '<span class="badge">无时间信息</span>'
    return f'<span class="badge">{days} 天未更新</span>'


def expander(label: str, inner: str, count: str = "") -> str:
    """Smooth height disclosure driven by grid-template-rows."""
    return (
        f'<div class="exp">'
        f'<button class="exp-btn" type="button" aria-expanded="false">'
        f'<span class="exp-icon"></span>{esc(label)}'
        + (f'<span class="exp-count">{esc(count)}</span>' if count else "")
        + "</button>"
        f'<div class="exp-body"><div class="exp-inner">{inner}</div></div>'
        "</div>"
    )


# --------------------------------------------------------------------------
# digest panel — the editorial briefing
# --------------------------------------------------------------------------

def render_digest_panel(digest: dict) -> str:
    cards = digest.get("cards") or []
    if not cards:
        return '<p class="empty">今日无汇总条目</p>'

    lead = cards[0]
    rest = cards[1:]

    def sources_line(refs) -> str:
        seen, out = set(), []
        for ref in refs:
            name = ref.get("channel") and f'@{ref["channel"]}' or (ref.get("source_name") or "")
            if not name or name in seen:
                continue
            seen.add(name)
            out.append(f'<a href="{esc(ref.get("url"))}" rel="noopener noreferrer">{esc(name)}</a>')
        return " · ".join(out)

    def topics(items) -> str:
        return "".join(
            f'<span class="topic">{esc(TOPIC_LABEL.get(t, t))}</span>' for t in items or []
        )

    lead_refs = "".join(
        f'''<a class="lead-src" href="{esc(r.get("url"))}" rel="noopener noreferrer">
      <span class="lead-src-name">{esc(r.get("channel") and "@" + r["channel"] or r.get("source_name") or "")}</span>
      <span class="lead-src-title">{esc((r.get("title") or "")[:70])}</span>
    </a>''' for r in lead.get("refs") or []
    )

    lead_html = f'''<article class="lead">
  <div class="lead-flag"><span class="lead-rank">01</span><span class="lead-bar"></span></div>
  <h2 class="lead-title">{esc(lead.get("title"))}</h2>
  <p class="lead-summary">{esc(lead.get("summary"))}</p>
  <div class="lead-meta">
    {topics(lead.get("topics"))}
    {f'<span class="why">{esc(lead.get("why"))}</span>' if lead.get("why") else ""}
    <span class="score" title="重要度">{esc(lead.get("importance"))}</span>
  </div>
  <div class="lead-refs">{lead_refs}</div>
</article>'''

    rows = []
    for card in rest:
        rows.append(f'''<li class="item">
  <div class="item-rank">{card.get("rank"):02d}</div>
  <div class="item-main">
    <h3 class="item-title">{esc(card.get("title"))}</h3>
    <p class="item-summary">{esc(card.get("summary"))}</p>
    <div class="item-meta">
      {topics(card.get("topics"))}
      {f'<span class="why">{esc(card.get("why"))}</span>' if card.get("why") else ""}
      <span class="item-score">{esc(card.get("importance"))}</span>
      <span class="item-srcs">{sources_line(card.get("refs") or [])}</span>
    </div>
  </div>
</li>''')

    themes = ""
    if digest.get("themes"):
        cards_html = "".join(
            f'''<div class="theme">
  <div class="theme-name">{esc(t.get("name"))}</div>
  <p class="theme-sum">{esc(t.get("summary"))}</p>
  <div class="theme-refs">{"".join(f'<a href="{esc(r.get("url"))}" rel="noopener noreferrer">{esc(r.get("channel") and "@" + r["channel"] or r.get("source") or "")}</a>' for r in t.get("refs") or [])}</div>
</div>''' for t in digest["themes"]
        )
        themes = f'<section class="themes"><h3 class="section-h">今日脉络</h3><div class="theme-grid">{cards_html}</div></section>'

    foot = []
    if digest.get("stats_note"):
        foot.append(f'<p class="note">{esc(digest["stats_note"])}</p>')
    if digest.get("model"):
        foot.append(f'<p class="byline">AI 处理 · {esc(digest["model"])}</p>')

    return f'''<header class="digest-head">
  <p class="headline">{esc(digest.get("headline"))}</p>
</header>
{lead_html}
<ol class="items">{"".join(rows)}</ol>
{themes}
{"".join(foot)}'''


# --------------------------------------------------------------------------
# telegram panel
# --------------------------------------------------------------------------

def render_telegram_panel(items: list[dict], embed: bool, base_dir: Path) -> str:
    cards = []
    for index, entry in enumerate(items, 1):
        extra = entry.get("extra") or {}
        channel = extra.get("channel", "")
        merged = bool(entry.get("ai_merged"))
        text = entry.get("summary") if merged else (extra.get("text") or entry.get("summary") or "")

        # first line is often the headline of the post
        lines = [l for l in (text or "").splitlines() if l.strip()]
        head, rest = "", lines
        if not merged and lines and len(lines[0]) <= 64 and len(lines) > 1:
            head, rest = lines[0], lines[1:]

        # images first, above the text
        local = [p for p in (extra.get("local_images") or []) if Path(p).exists()]
        remote = extra.get("photos") or []
        shots = ""
        if local or remote:
            count = min(len(local or remote), 3)
            cells = []
            for i, value in enumerate((local or remote)[:count]):
                if local and not embed:
                    try:
                        src = Path(value).relative_to(base_dir.parent).as_posix()
                    except ValueError:
                        src = Path(value).as_posix()
                elif local:
                    src = data_uri(Path(value))
                else:
                    src = value
                cells.append(f'<img loading="lazy" decoding="async" src="{esc(src)}" alt="">')
            shots = f'<div class="shots n{count}">{"".join(cells)}</div>'

        head_html = f'<p class="tg-lead">{esc(head)}</p>' if head else ""
        body_html = ""
        if rest:
            body_html = '<div class="tg-text">' + "<br>".join(linkify(l) for l in rest if l.strip()) + "</div>"

        badges = []
        if merged:
            badges.append(f'<span class="badge accent">AI 合并 · {len(entry.get("merged_from") or [])} 条</span>')
        if extra.get("views"):
            badges.append(f'<span class="badge">{esc(extra["views"])}</span>')

        reactions = ""
        if extra.get("reactions"):
            reactions = '<div class="rx">' + "".join(
                f'<span>{esc(r.get("emoji"))}{esc(r.get("count")) if r.get("count") is not None else ""}</span>'
                for r in extra["reactions"][:5]
            ) + "</div>"

        overlay_btn = ""
        if merged and entry.get("originals"):
            overlay_btn = (
                f'<button class="ghost-btn" type="button" data-overlay="orig-{index}">'
                f'查看原始消息 · {len(entry["originals"])}</button>'
            )

        cards.append(f'''<article class="tg" style="--i:{min(index, 18)}">
  <div class="tg-head">
    <a class="tg-ch" href="https://t.me/{esc(channel)}" rel="noopener noreferrer">@{esc(channel)}</a>
    <time>{esc(fmt_time(entry.get("published_at")))}</time>
    <a class="tg-id" href="{esc(entry.get("url"))}" rel="noopener noreferrer">#{esc(extra.get("message_id"))}</a>
  </div>
  {shots}
  {head_html}
  {body_html}
  <div class="tg-foot">{"".join(badges)}{reactions}</div>
  {overlay_btn}
</article>''')

    # overlay payloads
    overlays = []
    for index, entry in enumerate(items, 1):
        if not (entry.get("ai_merged") and entry.get("originals")):
            continue
        blocks = []
        for orig in entry["originals"]:
            blocks.append(f'''<div class="orig">
  <div class="orig-head">
    <span class="orig-ch">@{esc(orig.get("channel") or orig.get("source_name") or "")}</span>
    <time>{esc(fmt_time(orig.get("published_at")))}</time>
    <a href="{esc(orig.get("url"))}" rel="noopener noreferrer">原文 ↗</a>
  </div>
  <div class="orig-text">{linkify(orig.get("text") or "")}</div>
</div>''')
        overlays.append(
            f'<template id="orig-{index}"><h3 class="ov-title">原始消息 · {len(entry["originals"])} 条</h3>'
            f'<p class="ov-note">{esc(entry.get("title"))}</p>{"".join(blocks)}</template>'
        )

    return f'<div class="tg-grid">{"".join(cards)}</div>{"".join(overlays)}'


# --------------------------------------------------------------------------
# other panels
# --------------------------------------------------------------------------

def render_linuxdo_panel(items: list[dict]) -> str:
    order = ["今日要闻", "AI 前线", "开源开发", "福利羊毛", "社区闲话", "站外速览", "读者评论"]
    groups: dict[str, list[dict]] = {}
    for entry in items:
        groups.setdefault((entry.get("extra") or {}).get("section_name") or "其他", []).append(entry)

    issue = next(((e.get("extra") or {}).get("issue_title") for e in items
                  if (e.get("extra") or {}).get("issue_title")), "")
    out = [f'<p class="issue">{esc(issue)}</p>'] if issue else []
    for name in [n for n in order if n in groups] + [n for n in groups if n not in order]:
        rows = "".join(
            f'''<li class="row">
  <a class="row-title" href="{esc(e.get("url"))}" rel="noopener noreferrer">{esc(e.get("title"))}</a>
  {f'<p class="row-sum">{esc((e.get("summary") or "")[:280])}</p>' if (e.get("summary") or "").strip() else ""}
</li>''' for e in groups[name]
        )
        out.append(f'<section class="group"><h3 class="section-h">{esc(name)}<span class="count">{len(groups[name])}</span></h3>'
                   f'<ol class="rows">{rows}</ol></section>')
    return "".join(out)


def render_sspai_panel(items: list[dict]) -> str:
    morning = [i for i in items if "派早报" in i["title"]]
    others = [i for i in items if "派早报" not in i["title"]]
    out = []

    for entry in morning:
        sections = entry.get("sections") or []
        if sections:
            blocks = "".join(
                f'<section class="blk"><h4>{esc(s.get("title") or "")}</h4>{paragraphs(s.get("text") or "")}</section>'
                for s in sections
            )
        else:
            blocks = paragraphs(entry.get("body_text") or "")
        out.append(f'''<article class="feature">
  <header class="feature-head">
    <span class="kicker">派早报</span>
    <h3><a href="{esc(entry.get("url"))}" rel="noopener noreferrer">{esc(entry.get("title"))}</a></h3>
    {meta_row(entry)}
  </header>
  <div class="feature-body">{blocks or '<p class="empty">本期未抓取全文</p>'}</div>
</article>''')

    if others:
        rows = "".join(article_row(e) for e in others)
        out.append(f'<section class="group"><h3 class="section-h">其他文章<span class="count">{len(others)}</span></h3>'
                   f'<ol class="rows">{rows}</ol></section>')
    return "".join(out)


def article_row(entry: dict) -> str:
    body = entry.get("body_text")
    if body:
        extra = entry.get("extra") or {}
        scope = {"today": "今日更新", "latest": "最新一期", "requested": "按需抓取"}.get(extra.get("body_scope"), "")
        label = f'全文{(" · " + scope) if scope else ""}'
        detail = expander(label, f'<div class="article">{paragraphs(body)}</div>', f"{len(body)} 字")
    elif entry.get("summary"):
        detail = f'<p class="row-sum">{esc(entry["summary"][:280])}</p>'
    else:
        detail = ""
    return f'''<li class="row">
  <a class="row-title" href="{esc(entry.get("url"))}" rel="noopener noreferrer">{esc(entry.get("title"))}</a>
  {meta_row(entry)}
  {detail}
</li>'''


def render_article_panel(items: list[dict]) -> str:
    return f'<ol class="rows">{"".join(article_row(e) for e in items)}</ol>'


def render_list_panel(items: list[dict]) -> str:
    rows = "".join(
        f'''<li class="row">
  <a class="row-title" href="{esc(e.get("url"))}" rel="noopener noreferrer">{esc(e.get("title"))}</a>
  {meta_row(e)}
  {f'<p class="row-sum">{linkify((e.get("summary") or "")[:240])}</p>' if (e.get("summary") or "").strip() else ""}
</li>''' for e in items
    )
    return f'<ol class="rows">{rows}</ol>'


# --------------------------------------------------------------------------
# CSS
# --------------------------------------------------------------------------

CSS = r"""
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}

:root{
  --maxw:1200px;
  --font:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans SC","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
  --r-sm:4px; --r-md:8px; --r-lg:14px;
  --ease:cubic-bezier(.22,.68,.32,1);
}

:root,[data-theme="light"]{
  --bg:#fbfaf8; --elev:#ffffff; --sunken:#f5f2ec;
  --ink:#17171a; --ink-2:#41414a; --ink-3:#71717c; --ink-4:#a2a2ab;
  --rule:#e8e4dd; --rule-2:#f1eee8;
  --accent:#b8362a; --accent-fg:#ffffff; --accent-soft:#fbeeea; --accent-ink:#8a2419;
  --shadow-1:0 1px 2px rgba(24,20,16,.05);
  --shadow-2:0 1px 3px rgba(24,20,16,.06),0 14px 34px -22px rgba(24,20,16,.34);
  --shadow-3:0 24px 70px -30px rgba(24,20,16,.42);
  color-scheme:light;
}

[data-theme="dark"]{
  --bg:#121214; --elev:#1a1a1d; --sunken:#161618;
  --ink:#ededee; --ink-2:#c4c4c9; --ink-3:#8f8f97; --ink-4:#6a6a73;
  --rule:#2a2a2f; --rule-2:#212125;
  --accent:#e2614f; --accent-fg:#180f0d; --accent-soft:#2b1a16; --accent-ink:#ffa495;
  --shadow-1:0 1px 2px rgba(0,0,0,.4);
  --shadow-2:0 1px 3px rgba(0,0,0,.5),0 14px 34px -22px rgba(0,0,0,.8);
  --shadow-3:0 24px 70px -30px rgba(0,0,0,.9);
  color-scheme:dark;
}

html{-webkit-text-size-adjust:100%;scroll-behavior:smooth}
body{
  background:var(--bg);color:var(--ink);font-family:var(--font);
  font-size:16px;line-height:1.75;-webkit-font-smoothing:antialiased;
  text-rendering:optimizeLegibility;
  transition:background-color .25s var(--ease),color .25s var(--ease);
}
a{color:inherit;text-decoration:none}
a.lnk{color:var(--accent);word-break:break-all}
time,.mono{font-family:var(--mono);font-variant-numeric:tabular-nums}
::selection{background:var(--accent-soft);color:var(--accent-ink)}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:var(--r-sm)}

/* ---------------- shell ---------------- */
.top{
  position:sticky;top:0;z-index:50;
  background:color-mix(in srgb,var(--bg) 86%,transparent);
  backdrop-filter:saturate(150%) blur(14px);
  border-bottom:1px solid var(--rule);
}
.top-in{max-width:var(--maxw);margin:0 auto;padding:14px 28px 0}
.top-row{display:flex;align-items:center;gap:18px;flex-wrap:wrap}
.brand{display:flex;align-items:baseline;gap:12px;min-width:0}
.brand b{font-size:15px;font-weight:700;letter-spacing:-.01em;display:flex;align-items:center;gap:9px;white-space:nowrap}
.brand b::before{content:"";width:8px;height:8px;border-radius:50%;background:var(--accent);flex:none}
.brand span{font-family:var(--mono);font-size:11.5px;color:var(--ink-4);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.spacer{flex:1}
.tools{display:flex;align-items:center;gap:8px}
.tool{
  display:inline-flex;align-items:center;justify-content:center;gap:7px;
  height:32px;padding:0 11px;border:1px solid var(--rule);background:var(--elev);
  color:var(--ink-3);font:inherit;font-size:12.5px;border-radius:var(--r-md);cursor:pointer;
  transition:color .16s var(--ease),border-color .16s var(--ease),background-color .16s var(--ease),transform .16s var(--ease);
}
.tool:hover{color:var(--ink);border-color:var(--ink-4)}
.tool:active{transform:scale(.97)}
.tool svg{width:15px;height:15px;display:block}
[data-theme="light"] .icon-moon,[data-theme="dark"] .icon-sun{display:none}
.hint{font-family:var(--mono);font-size:11px;color:var(--ink-4);white-space:nowrap}

/* tabs wrap — never clipped */
.tabs{display:flex;flex-wrap:wrap;gap:5px;padding:14px 0 12px}
.tab{
  position:relative;display:inline-flex;align-items:center;gap:7px;
  padding:6px 13px;border:1px solid transparent;background:transparent;
  color:var(--ink-3);font:inherit;font-size:13.5px;border-radius:var(--r-md);cursor:pointer;
  white-space:nowrap;transition:color .16s var(--ease),background-color .16s var(--ease),border-color .16s var(--ease);
}
.tab:hover{color:var(--ink);background:var(--sunken)}
.tab .n{font-family:var(--mono);font-size:11px;color:var(--ink-4);transition:color .16s var(--ease)}
.tab.active{background:var(--accent);color:var(--accent-fg);border-color:var(--accent);font-weight:600}
.tab.active .n{color:color-mix(in srgb,var(--accent-fg) 72%,transparent)}

/* ---------------- layout ---------------- */
main{max-width:var(--maxw);margin:0 auto;padding:32px 28px 120px}
.panel{display:none}
.panel.active{display:block}
.panel.active>*{animation:rise .38s var(--ease) both}
@keyframes rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
.panel-head{display:flex;align-items:flex-end;gap:14px;flex-wrap:wrap;padding-bottom:16px;border-bottom:1px solid var(--rule);margin-bottom:30px}
.panel-head h2{font-size:22px;font-weight:700;letter-spacing:-.02em;line-height:1.2}
.panel-head .sub{font-size:12.5px;color:var(--ink-4);padding-bottom:3px}
.panel-head .right{margin-left:auto;display:flex;gap:8px;align-items:center;padding-bottom:3px}
.badge{display:inline-flex;align-items:center;font-family:var(--mono);font-size:10.5px;
  padding:2px 8px;border:1px solid var(--rule);border-radius:999px;color:var(--ink-3);white-space:nowrap}
.badge.ok{color:var(--accent);border-color:color-mix(in srgb,var(--accent) 34%,transparent);background:var(--accent-soft)}
.badge.accent{color:var(--accent-ink);border-color:transparent;background:var(--accent-soft)}
.empty{color:var(--ink-4);padding:16px 0}
.section-h{display:flex;align-items:center;gap:10px;font-size:11.5px;font-weight:700;
  letter-spacing:.16em;text-transform:uppercase;color:var(--accent);
  padding-bottom:10px;border-bottom:1px solid var(--rule);margin:38px 0 4px}
.section-h:first-child{margin-top:0}
.count{font-family:var(--mono);font-size:11px;color:var(--ink-4);letter-spacing:0;font-weight:400}
.issue{font-size:13.5px;color:var(--ink-3);padding:14px 18px;background:var(--sunken);
  border-left:2px solid var(--accent);border-radius:0 var(--r-md) var(--r-md) 0;margin-bottom:30px}

/* ---------------- digest ---------------- */
.digest-head{padding-bottom:28px;margin-bottom:34px;border-bottom:1px solid var(--rule)}
.headline{font-size:clamp(20px,2.7vw,28px);line-height:1.52;font-weight:600;
  letter-spacing:-.018em;max-width:62ch;color:var(--ink)}
.lead{position:relative;padding:0 0 34px 0;margin-bottom:6px;border-bottom:1px solid var(--rule)}
.lead-flag{display:flex;align-items:center;gap:12px;margin-bottom:14px}
.lead-rank{font-family:var(--mono);font-size:12px;font-weight:700;color:var(--accent)}
.lead-bar{flex:1;height:1px;background:linear-gradient(to right,var(--accent),transparent)}
.lead-title{font-size:clamp(19px,2.3vw,24px);line-height:1.44;font-weight:700;letter-spacing:-.02em;margin-bottom:14px;max-width:56ch}
.lead-summary{font-size:16px;line-height:1.85;color:var(--ink-2);max-width:64ch;margin-bottom:18px}
.lead-meta{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:18px}
.topic{font-size:11px;padding:2px 9px;border-radius:999px;background:var(--sunken);
  color:var(--ink-3);font-family:var(--mono)}
.why{font-size:12.5px;color:var(--ink-4)}
.score{font-family:var(--mono);font-size:11px;font-weight:700;color:var(--accent);
  border:1px solid color-mix(in srgb,var(--accent) 34%,transparent);border-radius:999px;padding:1px 8px}
.lead-refs{display:flex;flex-direction:column;gap:2px}
.lead-src{display:flex;gap:14px;align-items:baseline;padding:8px 12px;border-radius:var(--r-md);
  transition:background-color .16s var(--ease)}
.lead-src:hover{background:var(--sunken)}
.lead-src-name{font-family:var(--mono);font-size:11px;color:var(--accent);min-width:104px;flex:none}
.lead-src-title{font-size:13px;color:var(--ink-3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

.items{list-style:none}
.item{display:flex;gap:20px;padding:24px 0;border-bottom:1px solid var(--rule-2)}
.item:last-child{border-bottom:0}
.item-rank{font-family:var(--mono);font-size:12px;color:var(--ink-4);padding-top:4px;flex:none;min-width:24px}
.item-main{flex:1;min-width:0}
.item-title{font-size:17px;font-weight:600;line-height:1.5;letter-spacing:-.01em;margin-bottom:8px}
.item-summary{font-size:14.5px;line-height:1.8;color:var(--ink-2);max-width:66ch;margin-bottom:10px}
.item-meta{display:flex;flex-wrap:wrap;gap:10px;align-items:center}
.item-score{font-family:var(--mono);font-size:11px;color:var(--ink-4)}
.item-srcs{display:flex;gap:8px;flex-wrap:wrap;font-family:var(--mono);font-size:11px}
.item-srcs a{color:var(--accent);opacity:.85}
.item-srcs a:hover{opacity:1;text-decoration:underline}
.themes{margin-top:48px;padding-top:8px;border-top:1px solid var(--rule)}
.theme-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;margin-top:20px}
.theme{padding:18px 20px;background:var(--elev);border:1px solid var(--rule);border-radius:var(--r-lg);
  box-shadow:var(--shadow-1);transition:transform .2s var(--ease),box-shadow .2s var(--ease),border-color .2s var(--ease)}
.theme:hover{transform:translateY(-2px);box-shadow:var(--shadow-2);border-color:var(--ink-4)}
.theme-name{font-size:14px;font-weight:700;margin-bottom:7px;color:var(--accent-ink)}
.theme-sum{font-size:13.5px;line-height:1.75;color:var(--ink-3)}
.theme-refs{display:flex;gap:8px;flex-wrap:wrap;margin-top:11px;font-family:var(--mono);font-size:10.5px}
.theme-refs a{color:var(--ink-4)}
.theme-refs a:hover{color:var(--accent)}
.note{margin-top:34px;font-size:13px;color:var(--ink-4);font-style:italic}
.byline{margin-top:8px;font-family:var(--mono);font-size:11px;color:var(--ink-4)}

/* ---------------- telegram ---------------- */
.tg-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(308px,1fr));gap:16px;align-items:start}
.tg{
  background:var(--elev);border:1px solid var(--rule);border-radius:var(--r-lg);
  padding:15px;box-shadow:var(--shadow-1);display:flex;flex-direction:column;gap:11px;
  animation:cardIn .42s var(--ease) both;animation-delay:calc(var(--i) * 14ms);
  transition:border-color .2s var(--ease),box-shadow .2s var(--ease),transform .2s var(--ease);
}
@keyframes cardIn{from{opacity:0;transform:translateY(12px) scale(.985)}to{opacity:1;transform:none}}
@media(hover:hover){.tg:hover{border-color:var(--ink-4);box-shadow:var(--shadow-2);transform:translateY(-2px)}}
.tg-head{display:flex;align-items:center;gap:9px;font-family:var(--mono);font-size:11px;color:var(--ink-4)}
.tg-ch{color:var(--accent);font-weight:700}
.tg-ch:hover{text-decoration:underline}
.tg-head time{color:var(--ink-4)}
.tg-id{margin-left:auto;color:var(--ink-4)}
.tg-id:hover{color:var(--accent)}
.shots{display:grid;gap:5px;border-radius:var(--r-md);overflow:hidden;background:var(--sunken)}
.shots.n1{grid-template-columns:1fr}
.shots.n2{grid-template-columns:1fr 1fr}
.shots.n3{grid-template-columns:1fr 1fr}
.shots.n3 img:first-child{grid-row:span 2}
.shots img{width:100%;height:100%;object-fit:cover;display:block;min-height:0;max-height:340px}
.shots.n2 img,.shots.n3 img{max-height:150px}
.tg-lead{font-size:15px;font-weight:700;line-height:1.5;color:var(--ink);letter-spacing:-.005em}
.tg-text{font-size:14px;line-height:1.78;color:var(--ink-2);overflow-wrap:anywhere}
.tg-foot{display:flex;flex-wrap:wrap;gap:7px;align-items:center;margin-top:auto}
.rx{display:flex;gap:5px;flex-wrap:wrap;margin-left:auto}
.rx span{font-family:var(--mono);font-size:10.5px;padding:1px 6px;border:1px solid var(--rule);
  border-radius:999px;color:var(--ink-3)}
.ghost-btn{
  align-self:flex-start;font:inherit;font-size:12px;color:var(--accent);
  background:transparent;border:1px dashed color-mix(in srgb,var(--accent) 40%,transparent);
  border-radius:var(--r-md);padding:6px 12px;cursor:pointer;
  transition:background-color .16s var(--ease),border-color .16s var(--ease);
}
.ghost-btn:hover{background:var(--accent-soft);border-color:var(--accent)}

/* ---------------- rows / articles ---------------- */
.rows{list-style:none}
.row{padding:17px 0;border-bottom:1px solid var(--rule-2)}
.row:last-child{border-bottom:0}
.row-title{font-size:15.5px;font-weight:600;line-height:1.5;display:block;transition:color .16s var(--ease)}
.row-title:hover{color:var(--accent)}
.row-sum{font-size:13.5px;line-height:1.75;color:var(--ink-3);margin-top:7px;max-width:76ch}
.meta{display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-family:var(--mono);
  font-size:11px;color:var(--ink-4);margin-top:7px}
.meta time{color:var(--ink-4)}
.meta .src{color:var(--ink-4);opacity:.75}
.chip{font-size:10.5px;padding:1px 7px;border:1px solid var(--rule);border-radius:999px;color:var(--ink-3)}
.chip.accent{color:var(--accent);border-color:color-mix(in srgb,var(--accent) 30%,transparent)}
.group{margin-top:34px}

.feature{background:var(--elev);border:1px solid var(--rule);border-radius:var(--r-lg);
  padding:32px;margin-bottom:34px;box-shadow:var(--shadow-1)}
.feature-head{padding-bottom:20px;margin-bottom:24px;border-bottom:1px solid var(--rule)}
.kicker{font-family:var(--mono);font-size:10.5px;letter-spacing:.18em;text-transform:uppercase;
  color:var(--accent);display:block;margin-bottom:11px}
.feature-head h3{font-size:22px;line-height:1.42;font-weight:700;letter-spacing:-.02em;margin-bottom:12px;max-width:52ch}
.feature-head h3 a:hover{color:var(--accent)}
.feature-body{max-width:70ch}
.blk{margin-bottom:26px}
.blk h4,.h{font-size:14.5px;font-weight:700;color:var(--accent-ink);margin-bottom:10px;line-height:1.5}
.blk p,.article p{font-size:15.5px;line-height:1.9;color:var(--ink-2);margin-bottom:14px}
.blk p:last-child,.article p:last-child{margin-bottom:0}

/* smooth disclosure: real height animation, no display flip */
.exp{margin-top:11px}
.exp-btn{
  display:inline-flex;align-items:center;gap:8px;font:inherit;font-family:var(--mono);font-size:11.5px;
  color:var(--accent);background:transparent;border:0;padding:0;cursor:pointer;
  transition:opacity .16s var(--ease);
}
.exp-btn:hover{opacity:.7}
.exp-icon{width:0;height:0;border-left:5px solid currentColor;border-top:4px solid transparent;
  border-bottom:4px solid transparent;transition:transform .26s var(--ease);flex:none}
.exp.open .exp-icon{transform:rotate(90deg)}
.exp-count{color:var(--ink-4)}
.exp-body{display:grid;grid-template-rows:0fr;transition:grid-template-rows .34s var(--ease)}
.exp.open .exp-body{grid-template-rows:1fr}
.exp-inner{overflow:hidden;min-height:0}
.exp-inner>.article,.exp-inner>.blk{margin-top:18px;padding-left:18px;border-left:2px solid var(--rule)}

/* ---------------- overlay ---------------- */
.overlay{position:fixed;inset:0;z-index:100;display:flex;align-items:center;justify-content:center;
  padding:24px;background:color-mix(in srgb,#000 52%,transparent);
  backdrop-filter:blur(6px);opacity:0;pointer-events:none;transition:opacity .22s var(--ease)}
.overlay.open{opacity:1;pointer-events:auto}
.ov-panel{background:var(--elev);border:1px solid var(--rule);border-radius:var(--r-lg);
  box-shadow:var(--shadow-3);width:min(760px,100%);max-height:min(84vh,900px);
  display:flex;flex-direction:column;transform:translateY(14px) scale(.985);
  transition:transform .26s var(--ease)}
.overlay.open .ov-panel{transform:none}
.ov-head{display:flex;align-items:center;gap:14px;padding:18px 22px;border-bottom:1px solid var(--rule);flex:none}
.ov-head h3{font-size:15px;font-weight:700}
.ov-close{margin-left:auto;width:30px;height:30px;display:grid;place-items:center;border:1px solid var(--rule);
  background:transparent;color:var(--ink-3);border-radius:var(--r-md);cursor:pointer;font-size:16px;line-height:1;
  transition:color .16s var(--ease),border-color .16s var(--ease),transform .16s var(--ease)}
.ov-close:hover{color:var(--ink);border-color:var(--ink-4)}
.ov-close:active{transform:scale(.94)}
.ov-body{padding:22px;overflow-y:auto;overscroll-behavior:contain}
.ov-title{font-size:15px;font-weight:700;margin-bottom:6px}
.ov-note{font-size:12.5px;color:var(--ink-4);margin-bottom:20px;padding-bottom:16px;border-bottom:1px solid var(--rule-2)}
.orig{padding:15px 0;border-bottom:1px solid var(--rule-2)}
.orig:last-child{border-bottom:0}
.orig-head{display:flex;align-items:center;gap:11px;font-family:var(--mono);font-size:11px;
  color:var(--ink-4);margin-bottom:9px;flex-wrap:wrap}
.orig-ch{color:var(--accent);font-weight:700}
.orig-head a{margin-left:auto;color:var(--ink-4)}
.orig-head a:hover{color:var(--accent)}
.orig-text{font-size:14px;line-height:1.8;color:var(--ink-2);overflow-wrap:anywhere}

footer{max-width:var(--maxw);margin:0 auto;padding:0 28px 90px;color:var(--ink-4);font-size:12.5px;line-height:2}
footer code{font-family:var(--mono);color:var(--ink-3)}

@media(max-width:700px){
  .top-in,main,footer{padding-left:16px;padding-right:16px}
  /* the wrapped tab strip would be tall on a phone; let it scroll away instead
     of pinning a large header over the content */
  .top{position:static}
  .top-in{padding-top:16px}
  .hint{display:none}
  .feature{padding:22px}
  .tg-grid{grid-template-columns:1fr}
  .item{gap:14px}
  .lead-src-name{min-width:0}
  .lead-src{flex-direction:column;gap:3px}
  .theme{padding:16px}
}
@media(prefers-reduced-motion:reduce){
  *,*::before,*::after{animation-duration:.001ms!important;animation-iteration-count:1!important;
    transition-duration:.001ms!important;scroll-behavior:auto!important}
}
"""


# --------------------------------------------------------------------------
# JS
# --------------------------------------------------------------------------

JS = r"""
(function(){
  var root=document.documentElement;

  /* ---- theme: system default, manual override persisted ---- */
  var KEY='digest-theme';
  function stored(){ try{ return localStorage.getItem(KEY); }catch(e){ return null; } }
  function save(v){ try{ localStorage.setItem(KEY,v); }catch(e){} }
  var saved=stored();
  if(saved==='light'||saved==='dark'){
    root.dataset.theme=saved;
  }else{
    root.dataset.theme=window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';
  }
  var toggle=document.querySelector('.theme-toggle');
  if(toggle){
    toggle.addEventListener('click',function(){
      var next=root.dataset.theme==='dark'?'light':'dark';
      root.dataset.theme=next; save(next);
      toggle.setAttribute('aria-label',next==='dark'?'切换到浅色':'切换到深色');
    });
  }

  /* ---- tabs ---- */
  var tabs=[].slice.call(document.querySelectorAll('.tab'));
  var panels=[].slice.call(document.querySelectorAll('.panel'));
  function show(id,push){
    tabs.forEach(function(t){ t.classList.toggle('active',t.dataset.tab===id); });
    panels.forEach(function(p){ p.classList.toggle('active',p.id==='panel-'+id); });
    if(push!==false && history.replaceState) history.replaceState(null,'','#'+id);
  }
  tabs.forEach(function(t){
    t.addEventListener('click',function(){
      show(t.dataset.tab);
      window.scrollTo({top:0,behavior:'smooth'});
    });
  });
  document.addEventListener('keydown',function(e){
    var tag=(e.target.tagName||'').toUpperCase();
    if(tag==='INPUT'||tag==='TEXTAREA'||e.metaKey||e.ctrlKey||e.altKey) return;
    if(e.key==='ArrowRight'||e.key==='ArrowLeft'){
      var cur=tabs.findIndex(function(t){return t.classList.contains('active');});
      var next=cur+(e.key==='ArrowRight'?1:-1);
      if(next>=0&&next<tabs.length){ show(tabs[next].dataset.tab); e.preventDefault(); }
      return;
    }
    var n=parseInt(e.key,10);
    if(n>=1&&n<=9&&tabs[n-1]){ show(tabs[n-1].dataset.tab); return; }
    if(n===0&&tabs[9]){ show(tabs[9].dataset.tab); }
  });
  var h=location.hash.slice(1);
  if(h&&document.getElementById('panel-'+h)) show(h,false);

  /* ---- smooth disclosure ---- */
  document.addEventListener('click',function(e){
    var btn=e.target.closest('.exp-btn');
    if(!btn) return;
    var wrap=btn.closest('.exp');
    var open=wrap.classList.toggle('open');
    btn.setAttribute('aria-expanded',open?'true':'false');
  });

  /* ---- overlay for AI-merge originals ---- */
  var overlay=document.getElementById('overlay');
  var ovBody=overlay&&overlay.querySelector('.ov-body');
  var lastFocus=null;
  function openOverlay(id){
    var tpl=document.getElementById(id);
    if(!tpl||!overlay) return;
    lastFocus=document.activeElement;
    ovBody.innerHTML='';
    ovBody.appendChild(tpl.content.cloneNode(true));
    overlay.classList.add('open');
    overlay.setAttribute('aria-hidden','false');
    document.body.style.overflow='hidden';
    var close=overlay.querySelector('.ov-close');
    if(close) close.focus();
  }
  function closeOverlay(){
    if(!overlay) return;
    overlay.classList.remove('open');
    overlay.setAttribute('aria-hidden','true');
    document.body.style.overflow='';
    if(lastFocus&&lastFocus.focus) lastFocus.focus();
  }
  document.addEventListener('click',function(e){
    var trigger=e.target.closest('[data-overlay]');
    if(trigger){ openOverlay(trigger.dataset.overlay); return; }
    if(e.target.closest('.ov-close')){ closeOverlay(); return; }
    if(e.target===overlay){ closeOverlay(); }
  });
  document.addEventListener('keydown',function(e){
    if(e.key==='Escape') closeOverlay();
  });
})();
"""


# --------------------------------------------------------------------------
# page skeleton
# --------------------------------------------------------------------------

PAGE = """<!doctype html>
<html lang="zh-CN" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>__TITLE__</title>
<style>__CSS__</style>
</head>
<body>
<header class="top">
  <div class="top-in">
    <div class="top-row">
      <div class="brand"><b>每日摘要</b><span>__STATS__</span></div>
      <div class="spacer"></div>
      <div class="tools">
        <span class="hint">1–9 切换 · ←→ 翻页</span>
        <button class="tool theme-toggle" type="button" aria-label="切换主题">
          <svg class="icon-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
            <circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4"/>
          </svg>
          <svg class="icon-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z"/>
          </svg>
        </button>
      </div>
    </div>
    <nav class="tabs" role="tablist">__TABS__</nav>
  </div>
</header>
<main>__PANELS__</main>
<div class="overlay" id="overlay" aria-hidden="true" role="dialog" aria-modal="true">
  <div class="ov-panel">
    <div class="ov-head"><h3>原始消息</h3><button class="ov-close" type="button" aria-label="关闭">✕</button></div>
    <div class="ov-body"></div>
  </div>
</div>
<footer>
  本地生成 · <code>tools/collect.py</code> → <code>tools/ai_apply.py</code> → <code>tools/render_digest.py</code><br>
  每条保留原始链接与时间戳。摘要为原文摘录；标注「AI 合并」的条目可查看原始消息核对。
</footer>
<script>__JS__</script>
</body>
</html>
"""


def render(sources: dict[str, dict], digest: dict | None, output: Path, embed: bool) -> str:
    base_dir = output.parent
    names = [n for n in ORDER if n in sources]
    total = sum(len(sources[n].get("items") or []) for n in names)
    day = (sources[names[0]].get("day") if names else "") or ""
    updated = sum(1 for n in names if (sources[n].get("status") or {}).get("updated_today"))

    tabs, panels = [], []
    first = True

    if digest and digest.get("cards"):
        tabs.append(f'<button class="tab active" role="tab" data-tab="digest">今日汇总'
                    f'<span class="n">{len(digest["cards"])}</span></button>')
        panels.append('<section class="panel active" id="panel-digest">'
                      '<div class="panel-head"><h2>今日汇总</h2>'
                      '<span class="sub">AI 跨源合并与排序</span>'
                      '<div class="right"><span class="badge accent">AI 处理</span></div></div>'
                      f'{render_digest_panel(digest)}</section>')
        first = False

    for name in names:
        payload = sources[name]
        items = payload.get("items") or []
        label, sub = SOURCE_META.get(name, (name, ""))
        status = payload.get("status") or {}

        if name == "telegram":
            body = render_telegram_panel(items, embed, base_dir)
        elif name == "linuxdo":
            body = render_linuxdo_panel(items)
        elif name == "sspai":
            body = render_sspai_panel(items)
        elif name in ("latepost", "geekpark", "ruanyifeng", "anywayfm"):
            body = render_article_panel(items)
        else:
            body = render_list_panel(items)

        active = " active" if first else ""
        first = False
        tabs.append(f'<button class="tab{active}" role="tab" data-tab="{esc(name)}">{esc(label)}'
                    f'<span class="n">{len(items)}</span></button>')
        panels.append(f'''<section class="panel{active}" id="panel-{esc(name)}">
  <div class="panel-head"><h2>{esc(label)}</h2><span class="sub">{esc(sub)}</span>
    <div class="right">{freshness(status)}</div></div>
  {body or '<p class="empty">无数据</p>'}
</section>''')

    stats = f'{fmt_day(day)} · {total} 条 · {updated}/{len(names)} 源今日更新'
    return (PAGE
            .replace("__TITLE__", esc(f"每日摘要 · {fmt_day(day)}"))
            .replace("__CSS__", CSS)
            .replace("__JS__", JS)
            .replace("__STATS__", esc(stats))
            .replace("__TABS__", "".join(tabs))
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
        if path.name in ("index.json", "digest.json"):
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

#!/usr/bin/env python3
"""Stage 3: render processed data into one self-contained HTML file.

Reads data/processed/ (falling back to data/collected/ when stage 2 has not
run) and writes a single HTML file with all CSS, JS, and images inlined, so the
result can be shared as one file.

    python tools/render_digest.py --indir data/processed --output site/digest.html

Design notes
------------
- Light, warm, minimal: paper-toned background, near-black text, one red accent.
  Long Chinese text is far more readable dark-on-light than light-on-dark.
- No external requests: images become data URIs, fonts are the system stack.
- Subtle motion only: panel cross-fade, staggered card entry, smooth disclosure.
- Telegram messages get a real hierarchy: channel/time header, an emphasised
  first line when the post has one, then body text.
- AI-merged messages show a badge and an "original messages" disclosure, so the
  reader can always verify what the model combined.
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
    return dt.astimezone(CST).strftime("%m-%d %H:%M")


def fmt_day(value) -> str:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(CST).strftime("%Y 年 %m 月 %d 日")
    except (ValueError, TypeError):
        return str(value or "")


def paragraphs(text: str, limit: int | None = None) -> str:
    if not text:
        return ""
    chunks = [c.strip() for c in re.split(r"\n{2,}", text) if c.strip()]
    if limit:
        chunks = chunks[:limit]
    out = []
    for chunk in chunks:
        if re.match(r"^#{1,4}\s", chunk):
            out.append(f'<h4>{esc(chunk.lstrip("# ").strip())}</h4>')
        else:
            out.append(f"<p>{esc(chunk)}</p>")
    return "".join(out)


def linkify(text: str) -> str:
    out = esc(text)
    out = re.sub(r"(https?://[^\s<]+)", r'<a class="lnk" href="\1" rel="noopener noreferrer">\1</a>', out)
    return out.replace("\n", "<br>")


def data_uri(path: Path) -> str:
    raw = path.read_bytes()
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def meta_chips(entry: dict) -> str:
    extra = entry.get("extra") or {}
    chips = []
    if extra.get("section_name"):
        chips.append(f'<span class="chip chip-sec">{esc(extra["section_name"])}</span>')
    if extra.get("rank"):
        chips.append(f'<span class="chip chip-rank">#{esc(extra["rank"])}</span>')
    if extra.get("heat"):
        chips.append(f'<span class="chip">{esc(str(extra["heat"])[:16])}</span>')
    if extra.get("score"):
        chips.append(f'<span class="chip">{esc(extra["score"])} pts</span>')
    if extra.get("comments"):
        chips.append(f'<span class="chip">{esc(extra["comments"])} 评论</span>')
    if entry.get("author"):
        chips.append(f'<span class="chip">{esc(entry["author"])}</span>')
    return "".join(chips)


def freshness_badge(status: dict) -> str:
    if not status:
        return ""
    if status.get("updated_today"):
        return '<span class="fresh fresh-yes">今日已更新</span>'
    days = status.get("staleness_days")
    if days is None:
        return '<span class="fresh fresh-no">无时间信息</span>'
    return f'<span class="fresh fresh-no">今日未更新 · 最新 {days} 天前</span>'


def date_source_note(entry: dict) -> str:
    label = DATE_SOURCE_LABEL.get(entry.get("date_source", ""), entry.get("date_source") or "")
    return f'<span class="dsrc">{esc(label)}</span>' if label else ""


# --------------------------------------------------------------------------
# panels
# --------------------------------------------------------------------------

def render_digest_panel(digest: dict) -> str:
    """The AI briefing: ranked highlights plus themes. Always the first tab."""
    if not digest:
        return ""
    cards = []
    for card in digest.get("cards") or []:
        refs = "".join(
            f'<a class="ref" href="{esc(r.get("url"))}" rel="noopener noreferrer">'
            f'<span class="ref-src">{esc(r.get("source_name") or r.get("channel") or "")}</span>'
            f'<span class="ref-title">{esc((r.get("title") or "")[:64])}</span></a>'
            for r in card.get("refs") or []
        )
        topics = "".join(f'<span class="topic">{esc(t)}</span>' for t in card.get("topics") or [])
        why = f'<span class="why">{esc(card.get("why"))}</span>' if card.get("why") else ""
        cards.append(f'''<article class="hl" style="--imp:{card.get("importance") or 0}">
  <div class="hl-rank">{card.get("rank"):02d}</div>
  <div class="hl-main">
    <h3 class="hl-title">{esc(card.get("title"))}</h3>
    <p class="hl-summary">{esc(card.get("summary"))}</p>
    <div class="hl-meta">{topics}{why}<span class="imp">{card.get("importance")}</span></div>
    <div class="hl-refs">{refs}</div>
  </div>
</article>''')

    themes = ""
    if digest.get("themes"):
        items = "".join(
            f'<li><b>{esc(t.get("name"))}</b><span>{esc(t.get("summary"))}</span></li>'
            for t in digest["themes"]
        )
        themes = f'<div class="themes"><h4>今日脉络</h4><ul>{items}</ul></div>'

    note = f'<p class="note">{esc(digest.get("stats_note"))}</p>' if digest.get("stats_note") else ""
    model = f'<span class="byline">AI 处理 · {esc(digest.get("model") or "unknown")}</span>' if digest.get("model") else ""

    return f'''<div class="digest-head">
  <p class="headline">{esc(digest.get("headline"))}</p>
  {model}
</div>
<div class="highlights">{"".join(cards) or '<p class="empty">今日无汇总条目</p>'}</div>
{themes}
{note}'''


def render_telegram_panel(items: list[dict], base_dir: Path, embed: bool) -> str:
    cards = []
    for index, entry in enumerate(items, 1):
        extra = entry.get("extra") or {}
        channel = extra.get("channel", "")
        text = extra.get("text") or entry.get("summary") or ""
        merged = bool(entry.get("ai_merged"))

        if merged:
            body_lines = (entry.get("summary") or "").splitlines()
            head, rest = "", body_lines
        else:
            lines = [l for l in text.splitlines() if l.strip()]
            if lines and len(lines[0]) <= 60 and len(lines) > 1:
                head, rest = lines[0], lines[1:]
            else:
                head, rest = "", lines

        head_html = f'<p class="tg-head">{esc(head)}</p>' if head else ""
        body_html = "<br>".join(linkify(l) for l in rest if l.strip())
        body_html = f'<div class="tg-body">{body_html}</div>' if body_html else ""

        local = [p for p in (extra.get("local_images") or []) if Path(p).exists()]
        remote = extra.get("photos") or []
        shots = ""
        if local or remote:
            rendered = []
            for i, value in enumerate((local or remote)[:3]):
                if local and not embed:
                    src = Path(value).relative_to(base_dir.parent).as_posix() if base_dir.parent in Path(value).parents else Path(value).as_posix()
                elif local:
                    src = data_uri(Path(value))
                else:
                    src = value
                rendered.append(f'<img loading="lazy" src="{esc(src)}" alt="">')
            shots = f'<div class="tg-shots n{min(len(rendered),3)}">{"".join(rendered)}</div>'

        reactions = ""
        if extra.get("reactions"):
            reactions = '<div class="tg-rx">' + "".join(
                f'<span>{esc(r.get("emoji"))}{esc(r.get("count")) if r.get("count") is not None else ""}</span>'
                for r in extra["reactions"][:6]
            ) + "</div>"

        views = f'<span class="tg-view">{esc(extra.get("views"))}</span>' if extra.get("views") else ""
        badge = f'<span class="merged-badge">AI 合并 · {len(entry.get("merged_from") or [])} 条</span>' if merged else ""

        originals = ""
        if merged and entry.get("originals"):
            rows = "".join(
                f'''<div class="orig">
  <div class="orig-head"><span class="orig-ch">@{esc(o.get("channel") or o.get("source_name"))}</span>
    <time>{esc(fmt_time(o.get("published_at")))}</time>
    <a href="{esc(o.get("url"))}" rel="noopener noreferrer">原文 ↗</a></div>
  <div class="orig-text">{linkify(o.get("text") or "")}</div>
</div>'''
                for o in entry["originals"]
            )
            originals = f'<details class="orig-wrap"><summary>展开原始消息（{len(entry["originals"])}）</summary>{rows}</details>'

        cards.append(f'''<article class="tg" style="--i:{min(index, 40)}">
  <div class="tg-bar">
    <a class="tg-ch" href="https://t.me/{esc(channel)}" rel="noopener noreferrer">@{esc(channel)}</a>
    <time>{esc(fmt_time(entry.get("published_at")))}</time>
    {badge}
    <a class="tg-id" href="{esc(entry.get("url"))}" rel="noopener noreferrer">#{esc(extra.get("message_id"))}</a>
  </div>
  {head_html}
  {body_html}
  {shots}
  <div class="tg-foot">{views}{reactions}</div>
  {originals}
</article>''')
    return f'<div class="tg-grid">{"".join(cards)}</div>'


def render_linuxdo_panel(items: list[dict]) -> str:
    order = ["今日要闻", "AI 前线", "开源开发", "福利羊毛", "社区闲话", "站外速览", "读者评论"]
    groups: dict[str, list[dict]] = {}
    for entry in items:
        groups.setdefault((entry.get("extra") or {}).get("section_name") or "其他", []).append(entry)

    issue = next(((e.get("extra") or {}).get("issue_title") for e in items if (e.get("extra") or {}).get("issue_title")), "")
    out = [f'<p class="issue">{esc(issue)}</p>'] if issue else []
    for name in [n for n in order if n in groups] + [n for n in groups if n not in order]:
        rows = []
        for entry in groups[name]:
            summary = (entry.get("summary") or "").strip()
            summary_html = f'<p class="row-sum">{esc(summary[:300])}</p>' if summary else ""
            rows.append(f'''<li class="row">
  <a class="row-title" href="{esc(entry.get("url"))}" rel="noopener noreferrer">{esc(entry.get("title"))}</a>
  {summary_html}
</li>''')
        out.append(f'<section class="sub"><h3>{esc(name)}<i>{len(groups[name])}</i></h3><ol class="rows">{"".join(rows)}</ol></section>')
    return "".join(out)


def render_sspai_panel(items: list[dict], base_dir: Path, embed: bool) -> str:
    morning = [i for i in items if "派早报" in i["title"]]
    others = [i for i in items if "派早报" not in i["title"]]
    out = []

    for entry in morning:
        sections = entry.get("sections") or []
        if sections:
            blocks = "".join(
                f'<div class="blk"><h4>{esc(s.get("title") or "")}</h4>{paragraphs(s.get("text") or "")}</div>'
                for s in sections
            )
        else:
            blocks = paragraphs(entry.get("body_text") or "")
        out.append(f'''<article class="feature">
  <div class="feature-head">
    <span class="kicker">派早报</span>
    <h3><a href="{esc(entry.get("url"))}" rel="noopener noreferrer">{esc(entry.get("title"))}</a></h3>
    <div class="meta"><time>{esc(fmt_time(entry.get("published_at")))}</time>{date_source_note(entry)}{meta_chips(entry)}</div>
  </div>
  <div class="feature-body">{blocks or '<p class="empty">本期未抓取全文</p>'}</div>
</article>''')

    if others:
        rows = "".join(article_row(e) for e in others)
        out.append(f'<section class="sub"><h3>其他文章<i>{len(others)}</i></h3><ol class="rows">{rows}</ol></section>')
    return "".join(out)


def article_row(entry: dict) -> str:
    body = entry.get("body_text")
    body_html = ""
    if body:
        body_html = f'<details class="full"><summary>全文 · {len(body)} 字</summary><div class="article">{paragraphs(body)}</div></details>'
    elif entry.get("summary"):
        body_html = f'<p class="row-sum">{esc(entry["summary"][:280])}</p>'
    return f'''<li class="row">
  <a class="row-title" href="{esc(entry.get("url"))}" rel="noopener noreferrer">{esc(entry.get("title"))}</a>
  <div class="meta"><time>{esc(fmt_time(entry.get("published_at")))}</time>{date_source_note(entry)}{meta_chips(entry)}</div>
  {body_html}
</li>'''


def render_article_panel(items: list[dict]) -> str:
    return f'<ol class="rows">{"".join(article_row(e) for e in items)}</ol>'


def render_list_panel(items: list[dict]) -> str:
    rows = []
    for entry in items:
        summary = (entry.get("summary") or "").strip()
        summary_html = f'<p class="row-sum">{linkify(summary[:240])}</p>' if summary else ""
        rows.append(f'''<li class="row">
  <a class="row-title" href="{esc(entry.get("url"))}" rel="noopener noreferrer">{esc(entry.get("title"))}</a>
  <div class="meta"><time>{esc(fmt_time(entry.get("published_at")))}</time>{date_source_note(entry)}{meta_chips(entry)}</div>
  {summary_html}
</li>''')
    return f'<ol class="rows">{"".join(rows)}</ol>'


# --------------------------------------------------------------------------
# page
# --------------------------------------------------------------------------

CSS = """
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#faf8f5; --surface:#ffffff; --ink:#1b1b1d; --ink-soft:#33333a;
  --muted:#6d6d76; --faint:#a0a0a8; --line:#e7e3dc; --line-soft:#f1eee8;
  --accent:#c0402c; --accent-soft:#fbeeea; --accent-ink:#8f2c1c;
  --shadow:0 1px 2px rgba(28,24,20,.04), 0 8px 24px -16px rgba(28,24,20,.18);
}
html{-webkit-text-size-adjust:100%}
body{
  background:var(--bg); color:var(--ink);
  font:16px/1.78 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans SC","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
  -webkit-font-smoothing:antialiased; text-rendering:optimizeLegibility;
}
a{color:inherit;text-decoration:none}
a.lnk{color:var(--accent);word-break:break-all}
time,.mono,.meta,.chip,.fresh,.dsrc,.tg-bar,.tg-foot,.hl-rank,.imp,.byline,.issue,.kicker{font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace}
::selection{background:var(--accent-soft)}

/* ---------- top bar ---------- */
.top{position:sticky;top:0;z-index:60;background:rgba(250,248,245,.88);backdrop-filter:saturate(140%) blur(12px);border-bottom:1px solid var(--line)}
.top-in{max-width:1180px;margin:0 auto;padding:12px 24px;display:flex;align-items:center;gap:24px;flex-wrap:wrap}
.brand{display:flex;align-items:baseline;gap:10px;white-space:nowrap}
.brand b{font-size:15px;font-weight:700;letter-spacing:-.01em}
.brand b::before{content:"";display:inline-block;width:7px;height:7px;background:var(--accent);margin-right:8px;vertical-align:1px}
.brand span{font-size:11.5px;color:var(--faint);font-family:ui-monospace,monospace}
.tabs{display:flex;gap:2px;overflow-x:auto;flex:1;scrollbar-width:none;padding-bottom:1px}
.tabs::-webkit-scrollbar{display:none}
.tab{position:relative;background:none;border:0;color:var(--muted);font:inherit;font-size:13.5px;
  padding:7px 13px;cursor:pointer;white-space:nowrap;transition:color .18s ease}
.tab:hover{color:var(--ink)}
.tab.active{color:var(--ink);font-weight:600}
.tab.active::after{content:"";position:absolute;left:13px;right:13px;bottom:-1px;height:2px;background:var(--accent);
  animation:underline .28s cubic-bezier(.2,.7,.3,1)}
@keyframes underline{from{transform:scaleX(0);opacity:0}to{transform:scaleX(1);opacity:1}}
.tab i{font-style:normal;color:var(--faint);font-size:11px;margin-left:6px;font-family:ui-monospace,monospace}
.tab.active i{color:var(--accent)}
.hint{font-size:11px;color:var(--faint);font-family:ui-monospace,monospace;white-space:nowrap}

/* ---------- layout ---------- */
main{max-width:1180px;margin:0 auto;padding:34px 24px 110px}
.panel{display:none}
.panel.active{display:block;animation:panelIn .32s cubic-bezier(.2,.7,.3,1)}
@keyframes panelIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.panel-head{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;padding-bottom:14px;border-bottom:2px solid var(--ink);margin-bottom:26px}
.panel-head h2{font-size:23px;font-weight:700;letter-spacing:-.02em}
.panel-head .hint2{font-size:12.5px;color:var(--muted)}
.panel-head .right{margin-left:auto;display:flex;gap:8px;align-items:center}
.fresh{font-size:11px;padding:2px 8px;border:1px solid var(--line);color:var(--muted);white-space:nowrap}
.fresh-yes{color:var(--accent);border-color:#e9c9c0;background:var(--accent-soft)}
.fresh-no{color:var(--faint)}
.empty{color:var(--faint);padding:14px 0}

/* ---------- digest ---------- */
.digest-head{padding:0 0 26px;border-bottom:1px solid var(--line);margin-bottom:30px}
.headline{font-size:clamp(19px,2.6vw,26px);line-height:1.5;font-weight:600;letter-spacing:-.015em;max-width:60ch}
.byline{display:inline-block;margin-top:14px;font-size:11px;color:var(--faint)}
.highlights{display:flex;flex-direction:column;gap:2px}
.hl{display:flex;gap:20px;padding:24px 0;border-bottom:1px solid var(--line-soft);animation:cardIn .4s cubic-bezier(.2,.7,.3,1) both}
.hl:nth-child(1){animation-delay:.02s}.hl:nth-child(2){animation-delay:.06s}.hl:nth-child(3){animation-delay:.1s}
.hl:nth-child(4){animation-delay:.14s}.hl:nth-child(5){animation-delay:.18s}
@keyframes cardIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
.hl-rank{font-size:13px;color:var(--faint);padding-top:3px;min-width:26px;position:relative}
.hl-rank::after{content:"";display:block;width:22px;height:3px;background:var(--accent);margin-top:6px;opacity:calc(var(--imp)/100)}
.hl-main{flex:1;min-width:0}
.hl-title{font-size:18px;font-weight:700;line-height:1.45;letter-spacing:-.01em;margin-bottom:8px}
.hl-summary{color:var(--ink-soft);max-width:70ch;margin-bottom:12px}
.hl-meta{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:12px}
.topic{font-size:11px;color:var(--accent-ink);background:var(--accent-soft);padding:2px 7px}
.why{font-size:11.5px;color:var(--faint)}
.imp{font-size:11px;color:var(--accent);border:1px solid #e9c9c0;padding:1px 6px}
.hl-refs{display:flex;flex-direction:column;gap:5px;border-left:2px solid var(--line);padding-left:14px}
.ref{display:flex;gap:10px;font-size:12.5px;color:var(--muted);transition:color .16s ease}
.ref:hover{color:var(--accent)}
.ref-src{color:var(--faint);white-space:nowrap;font-family:ui-monospace,monospace;font-size:11px;min-width:88px}
.ref-title{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.themes{margin-top:36px;padding-top:22px;border-top:1px solid var(--line)}
.themes h4{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);margin-bottom:14px}
.themes ul{list-style:none;display:flex;flex-direction:column;gap:12px}
.themes li{display:flex;gap:14px;font-size:14px;color:var(--muted);flex-wrap:wrap}
.themes b{color:var(--ink);min-width:120px}
.note{margin-top:26px;font-size:13px;color:var(--faint);font-style:italic}

/* ---------- telegram ---------- */
.tg-grid{columns:3;column-gap:18px}
@media(max-width:1000px){.tg-grid{columns:2}}
@media(max-width:640px){.tg-grid{columns:1}}
.tg{break-inside:avoid;background:var(--surface);border:1px solid var(--line);padding:16px;margin-bottom:18px;
  box-shadow:var(--shadow);animation:cardIn .4s cubic-bezier(.2,.7,.3,1) both;
  animation-delay:calc(var(--i) * .012s);transition:border-color .18s ease,transform .18s ease}
.tg:hover{border-color:#d9d3c9;transform:translateY(-1px)}
.tg-bar{display:flex;align-items:center;gap:9px;flex-wrap:wrap;font-size:11px;color:var(--faint);
  padding-bottom:10px;margin-bottom:11px;border-bottom:1px solid var(--line-soft)}
.tg-ch{color:var(--accent);font-weight:700}
.tg-ch:hover{text-decoration:underline}
.tg-id{margin-left:auto;color:var(--faint)}
.merged-badge{font-size:10px;color:var(--accent-ink);background:var(--accent-soft);padding:1px 6px;letter-spacing:.02em}
.tg-head{font-size:15px;font-weight:700;line-height:1.5;color:var(--ink);margin-bottom:8px;letter-spacing:-.005em}
.tg-body{font-size:14px;line-height:1.75;color:var(--ink-soft);overflow-wrap:anywhere}
.tg-body+.tg-head{margin-top:10px}
.tg-shots{display:grid;gap:6px;margin-top:12px}
.tg-shots.n1{grid-template-columns:1fr}
.tg-shots.n2{grid-template-columns:1fr 1fr}
.tg-shots.n3{grid-template-columns:1fr 1fr}
.tg-shots img{width:100%;height:auto;display:block;border:1px solid var(--line-soft)}
.tg-foot{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-top:11px;font-size:11px;color:var(--faint)}
.tg-rx{display:flex;gap:5px;flex-wrap:wrap}
.tg-rx span{border:1px solid var(--line);padding:0 5px;color:var(--muted)}
.orig-wrap{margin-top:12px;border-top:1px dashed var(--line)}
.orig-wrap summary{cursor:pointer;font-size:11.5px;color:var(--accent);padding:9px 0 0;list-style:none;
  font-family:ui-monospace,monospace;transition:opacity .16s ease}
.orig-wrap summary:hover{opacity:.72}
.orig-wrap summary::-webkit-details-marker{display:none}
.orig-wrap summary::before{content:"▸ "}
.orig-wrap[open] summary::before{content:"▾ "}
.orig{margin-top:12px;padding:11px;background:var(--bg);border-left:2px solid var(--line)}
.orig-head{display:flex;gap:10px;align-items:center;flex-wrap:wrap;font-size:10.5px;color:var(--faint);
  font-family:ui-monospace,monospace;margin-bottom:7px}
.orig-ch{color:var(--muted)}
.orig-text{font-size:13px;color:var(--ink-soft);line-height:1.7}

/* ---------- rows ---------- */
.rows{list-style:none}
.row{padding:15px 0;border-bottom:1px solid var(--line-soft)}
.row:last-child{border-bottom:0}
.row-title{font-size:15.5px;font-weight:600;line-height:1.5;display:block;transition:color .16s ease}
.row-title:hover{color:var(--accent)}
.row-sum{font-size:13.5px;color:var(--muted);margin-top:6px;max-width:74ch}
.meta{display:flex;flex-wrap:wrap;gap:9px;align-items:center;font-size:11px;color:var(--faint);margin-top:6px}
.chip{font-size:10.5px;border:1px solid var(--line);padding:0 6px;color:var(--muted)}
.chip-sec{color:var(--accent);border-color:#e9c9c0;background:var(--accent-soft)}
.chip-rank{color:var(--accent);border-color:#e9c9c0}
.dsrc{font-size:10.5px;color:var(--faint)}
.sub{margin-top:34px}
.sub:first-child{margin-top:0}
.sub h3{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);
  padding-bottom:9px;border-bottom:1px solid var(--line);margin-bottom:6px;display:flex;align-items:center;gap:10px}
.sub h3 i{font-style:normal;color:var(--faint);font-family:ui-monospace,monospace;letter-spacing:0}
.issue{font-size:13px;color:var(--muted);padding:12px 14px;background:var(--surface);border-left:3px solid var(--accent);margin-bottom:26px}

/* ---------- articles ---------- */
.feature{background:var(--surface);border:1px solid var(--line);padding:28px;margin-bottom:30px;box-shadow:var(--shadow)}
.feature-head{border-bottom:1px solid var(--line);padding-bottom:18px;margin-bottom:22px}
.kicker{font-size:10.5px;letter-spacing:.16em;text-transform:uppercase;color:var(--accent);display:block;margin-bottom:10px}
.feature-head h3{font-size:21px;line-height:1.45;font-weight:700;letter-spacing:-.015em;margin-bottom:10px}
.feature-head h3 a:hover{color:var(--accent)}
.feature-body{max-width:74ch}
.blk{margin-bottom:24px}
.blk h4{font-size:14.5px;color:var(--accent-ink);margin-bottom:9px;font-weight:700;line-height:1.5}
.blk p,.article p{color:var(--ink-soft);font-size:15px;line-height:1.85;margin-bottom:12px}
.blk p:last-child,.article p:last-child{margin-bottom:0}
details.full{margin-top:12px}
details.full summary{cursor:pointer;font-size:12px;color:var(--accent);list-style:none;
  font-family:ui-monospace,monospace;transition:opacity .16s ease}
details.full summary:hover{opacity:.72}
details.full summary::-webkit-details-marker{display:none}
details.full summary::before{content:"▸ 展开"}
details.full[open] summary::before{content:"▾ 收起"}
.article{margin-top:16px;padding-left:18px;border-left:2px solid var(--line);max-width:74ch;animation:panelIn .28s ease}

footer{max-width:1180px;margin:0 auto;padding:0 24px 80px;color:var(--faint);font-size:12px;line-height:1.9}
footer code{color:var(--muted);font-family:ui-monospace,monospace}
@media(max-width:640px){.top-in,main,footer{padding-left:16px;padding-right:16px}.hint{display:none}.feature{padding:20px}}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""

JS = """
(function(){
  var tabs=[].slice.call(document.querySelectorAll('.tab'));
  var panels=[].slice.call(document.querySelectorAll('.panel'));
  function show(id){
    tabs.forEach(function(t){t.classList.toggle('active',t.dataset.tab===id)});
    panels.forEach(function(p){p.classList.toggle('active',p.id==='panel-'+id)});
    if(history.replaceState)history.replaceState(null,'','#'+id);
  }
  tabs.forEach(function(t){t.addEventListener('click',function(){show(t.dataset.tab);window.scrollTo({top:0,behavior:'smooth'})})});
  document.addEventListener('keydown',function(e){
    if(e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA')return;
    if(e.key==='ArrowRight'||e.key==='ArrowLeft'){
      var cur=tabs.findIndex(function(t){return t.classList.contains('active')});
      var next=cur+(e.key==='ArrowRight'?1:-1);
      if(next>=0&&next<tabs.length){show(tabs[next].dataset.tab);e.preventDefault()}
      return;
    }
    var n=parseInt(e.key,10);
    if(n>=1&&n<=9){show(tabs[n-1].dataset.tab);return}
    if(n===0&&tabs[9]){show(tabs[9].dataset.tab)}
  });
  var h=location.hash.slice(1);
  if(h&&document.getElementById('panel-'+h))show(h);
})();
"""


def render(sources: dict[str, dict], digest: dict | None, output: Path, embed: bool) -> str:
    base_dir = output.parent
    names = [n for n in ORDER if n in sources]
    total = sum(len((sources[n].get("items") or [])) for n in names)
    day = (sources[names[0]].get("day") if names else "") or ""

    tabs, panels = [], []

    if digest and digest.get("cards"):
        tabs.append('<button class="tab active" data-tab="digest">今日汇总<i>%d</i></button>' % len(digest["cards"]))
        panels.append(f'<section class="panel active" id="panel-digest">'
                      f'<div class="panel-head"><h2>今日汇总</h2>'
                      f'<span class="hint2">AI 跨源合并与排序</span></div>'
                      f'{render_digest_panel(digest)}</section>')
        first_active = False
    else:
        first_active = True

    for name in names:
        payload = sources[name]
        items = payload.get("items") or []
        label, hint = SOURCE_META.get(name, (name, ""))
        status = payload.get("status") or {}

        if name == "telegram":
            body = render_telegram_panel(items, base_dir, embed)
        elif name == "linuxdo":
            body = render_linuxdo_panel(items)
        elif name == "sspai":
            body = render_sspai_panel(items, base_dir, embed)
        elif name in ("latepost", "geekpark", "ruanyifeng", "anywayfm"):
            body = render_article_panel(items)
        else:
            body = render_list_panel(items)

        active = " active" if first_active else ""
        first_active = False
        tabs.append(f'<button class="tab{active}" data-tab="{esc(name)}">{esc(label)}<i>{len(items)}</i></button>')
        panels.append(f'''<section class="panel{active}" id="panel-{esc(name)}">
  <div class="panel-head"><h2>{esc(label)}</h2><span class="hint2">{esc(hint)}</span>
    <div class="right">{freshness_badge(status)}</div></div>
  {body or '<p class="empty">无数据</p>'}
</section>''')

    updated = sum(1 for n in names if (sources[n].get("status") or {}).get("updated_today"))
    header_stats = f'{esc(fmt_day(day))} · {total} 条 · {updated}/{len(names)} 源今日更新'

    return f'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>每日摘要 · {esc(fmt_day(day))}</title>
<style>{CSS}</style>
</head>
<body>
<header class="top"><div class="top-in">
  <div class="brand"><b>每日摘要</b><span>{header_stats}</span></div>
  <nav class="tabs">{"".join(tabs)}</nav>
  <span class="hint">1–9 切换 · ←→ 翻页</span>
</div></header>
<main>{"".join(panels)}</main>
<footer>
  本地生成 · <code>tools/collect.py</code> → <code>tools/ai_apply.py</code> → <code>tools/render_digest.py</code><br>
  每条保留原始链接与时间戳。摘要为原文摘录；标注「AI 合并」的条目可展开原始消息核对。
</footer>
<script>{JS}</script>
</body></html>'''


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

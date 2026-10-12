#!/usr/bin/env python3
"""Render data/collected/*.json into one minimal black/red tabbed digest.

Design: black background, single red accent, no rounded corners, no gradients,
thin rules, monospace metadata. Sources switch horizontally via a sticky nav.

Usage:
    python tools/render_digest.py --indir data/collected --output site/digest.html
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
from datetime import datetime
from pathlib import Path

SOURCE_META = {
    "sspai": ("少数派", "派早报全文 · 其余仅标题"),
    "telegram": ("Telegram", "当日全部消息 · 瀑布流"),
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

KIND_BADGE = {
    "newsletter": "周刊",
    "article": "文章",
    "link": "链接",
    "topic": "帖子",
    "hotsearch": "热搜",
    "message": "消息",
}
DATE_SOURCE_LABEL = {
    "collection_time": "抓取时间",
    "relative_balloon": "页面相对时间",
    "archive_month": "归档月份",
    "api_relative": "接口相对时间",
    "issue_date": "出刊日",
    "atom": "RSS",
    "rss": "RSS",
    "api": "接口",
    "page": "页面",
    "post": "帖文",
    "article_page": "文章页",
}


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def fmt_time(value: str | None) -> str:
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)[:16]
    return dt.strftime("%m-%d %H:%M")


def paragraphs(text: str, limit: int | None = None) -> str:
    if not text:
        return ""
    chunks = [c.strip() for c in re.split(r"\n{2,}", text) if c.strip()]
    if limit:
        chunks = chunks[:limit]
    return "".join(f"<p>{esc(c)}</p>" for c in chunks)


def linkify(text: str) -> str:
    out = esc(text)
    return re.sub(r"(https?://[^\s<]+)", r'<a href="\1" rel="noopener noreferrer">\1</a>', out)


def meta_line(entry: dict) -> str:
    bits: list[str] = []
    when = fmt_time(entry.get("published_at"))
    if when:
        bits.append(f'<time>{esc(when)}</time>')
    source = DATE_SOURCE_LABEL.get(entry.get("date_source", ""), entry.get("date_source") or "")
    if source:
        bits.append(f'<span class="ds">{esc(source)}</span>')
    extra = entry.get("extra") or {}
    if extra.get("section_name"):
        bits.append(f'<span class="sec">{esc(extra["section_name"])}</span>')
    if extra.get("rank"):
        bits.append(f'<span class="rank">#{esc(extra["rank"])}</span>')
    if extra.get("heat"):
        bits.append(f'<span class="heat">{esc(str(extra["heat"])[:14])}</span>')
    if extra.get("score"):
        bits.append(f'<span>{esc(extra["score"])} pts</span>')
    if extra.get("comments"):
        bits.append(f'<span>{esc(extra["comments"])} 评论</span>')
    if entry.get("author"):
        bits.append(f'<span>{esc(entry["author"])}</span>')
    if entry.get("body_text"):
        bits.append(f'<span class="ft">{len(entry["body_text"])} 字全文</span>')
    return " ".join(bits)


def badge(entry: dict) -> str:
    kind = KIND_BADGE.get(entry.get("kind", ""), entry.get("kind") or "")
    return f'<span class="badge">{esc(kind)}</span>' if kind else ""


# --------------------------------------------------------------------------
# Row-style item (dense list)
# --------------------------------------------------------------------------

def row(entry: dict, index: int) -> str:
    summary = (entry.get("summary") or "").strip()
    summary_html = f'<p class="sum">{linkify(summary[:260])}</p>' if summary and len(summary) > 12 else ""
    return f'''<li class="row">
  <span class="idx">{index:02d}</span>
  <div class="rowbody">
    <a class="t" href="{esc(entry.get("url"))}" rel="noopener noreferrer">{esc(entry.get("title"))}</a>
    <div class="meta">{badge(entry)}{meta_line(entry)}</div>
    {summary_html}
  </div>
</li>'''


def row_with_body(entry: dict, index: int) -> str:
    """Row whose full text is collapsible."""
    body = entry.get("body_text")
    body_html = ""
    if body:
        scope = (entry.get("extra") or {}).get("body_scope")
        label = "全文" + ("（当日更新）" if scope == "today" else "（最新一期）" if scope == "latest" else "")
        body_html = f'<details class="full"><summary>{esc(label)} · {len(body)} 字</summary><div class="article">{paragraphs(body)}</div></details>'
    return f'''<li class="row">
  <span class="idx">{index:02d}</span>
  <div class="rowbody">
    <a class="t" href="{esc(entry.get("url"))}" rel="noopener noreferrer">{esc(entry.get("title"))}</a>
    <div class="meta">{badge(entry)}{meta_line(entry)}</div>
    {body_html or (f'<p class="sum">{linkify((entry.get("summary") or "")[:260])}</p>' if entry.get("summary") else "")}
  </div>
</li>'''


# --------------------------------------------------------------------------
# Source panels
# --------------------------------------------------------------------------

def panel_sspai(items: list[dict]) -> str:
    morning = [i for i in items if "派早报" in i["title"]]
    others = [i for i in items if "派早报" not in i["title"]]
    out: list[str] = []

    for entry in morning:
        sections = entry.get("sections") or []
        if sections:
            blocks = "".join(
                f'<div class="block"><h4>{esc(s.get("title") or "")}</h4>{paragraphs(s.get("text") or "")}</div>'
                for s in sections
            )
        else:
            blocks = paragraphs(entry.get("body_text") or "")
        out.append(f'''<article class="feature">
  <div class="feature-head">
    <span class="badge hot">派早报</span>
    <a class="ft-title" href="{esc(entry.get("url"))}" rel="noopener noreferrer">{esc(entry.get("title"))}</a>
    <div class="meta">{meta_line(entry)}</div>
  </div>
  <div class="feature-body">{blocks}</div>
</article>''')

    if others:
        out.append('<h3 class="group">其他文章</h3>')
        out.append('<ol class="rows">' + "".join(row(e, n) for n, e in enumerate(others, 1)) + "</ol>")
    return "".join(out)


def panel_telegram(items: list[dict], base_dir: Path) -> str:
    cards = []
    for entry in items:
        extra = entry.get("extra") or {}
        channel = extra.get("channel", "")
        text = extra.get("text") or entry.get("summary") or ""
        local = [p for p in (extra.get("local_images") or []) if Path(p).exists()]
        photos = local or (extra.get("photos") or [])
        reactions = extra.get("reactions") or []
        media = extra.get("media") or []

        shots = ""
        if photos:
            rendered = []
            for index, value in enumerate(photos[:2]):
                if local:
                    src = os.path.relpath(value, base_dir)
                else:
                    src = value
                fallback = (extra.get("photos") or [])[index] if local and index < len(extra.get("photos") or []) else ""
                onerror = f" onerror=\"this.onerror=null;this.src='{esc(fallback)}\"" if fallback else ""
                rendered.append(f'<img loading="lazy" src="{esc(src)}" alt=""{onerror}>')
            shots = '<div class="shots">' + "".join(rendered) + "</div>"

        reaction_html = ""
        if reactions:
            reaction_html = '<div class="rx">' + "".join(
                f'<span>{esc(r.get("emoji"))}{esc(r.get("count")) if r.get("count") is not None else ""}</span>'
                for r in reactions[:6]
            ) + "</div>"

        views = f'<span>{esc(extra.get("views"))}</span>' if extra.get("views") else ""
        media_badge = f'<span>{" ".join(esc(m) for m in media)}</span>' if media else ""

        cards.append(f'''<article class="card">
  <div class="card-head">
    <a class="ch" href="https://t.me/{esc(channel)}" rel="noopener noreferrer">@{esc(channel)}</a>
    <time>{esc(fmt_time(entry.get("published_at")))}</time>
    <a class="msgid" href="{esc(entry.get("url"))}" rel="noopener noreferrer">#{esc(extra.get("message_id"))}</a>
  </div>
  <div class="card-body">{linkify(text).replace(chr(10), "<br>")}</div>
  {shots}
  <div class="card-foot">{views}{media_badge}{reaction_html}</div>
</article>''')
    return '<div class="masonry">' + "".join(cards) + "</div>"


def panel_linuxdo(items: list[dict]) -> str:
    order = ["今日要闻", "AI 前线", "开源开发", "福利羊毛", "社区闲话", "站外速览", "读者评论"]
    groups: dict[str, list[dict]] = {}
    for entry in items:
        name = (entry.get("extra") or {}).get("section_name") or "其他"
        groups.setdefault(name, []).append(entry)

    issue = next(((e.get("extra") or {}).get("issue_title") for e in items if (e.get("extra") or {}).get("issue_title")), "")
    out = [f'<div class="issue-head">{esc(issue)}</div>'] if issue else []
    for name in [n for n in order if n in groups] + [n for n in groups if n not in order]:
        entries = groups[name]
        out.append(f'<h3 class="group">{esc(name)}<span class="gcount">{len(entries)}</span></h3>')
        out.append('<ol class="rows">' + "".join(row(e, n) for n, e in enumerate(entries, 1)) + "</ol>")
    return "".join(out)


def panel_simple(items: list[dict], with_body: bool = False) -> str:
    renderer = row_with_body if with_body else row
    return '<ol class="rows">' + "".join(renderer(e, n) for n, e in enumerate(items, 1)) + "</ol>"


# --------------------------------------------------------------------------
# Page
# --------------------------------------------------------------------------

def render(files: dict[str, dict], output: Path) -> str:
    names = [n for n in ORDER if n in files]
    base_dir = output.parent
    tabs, panels = [], []
    total = 0

    for name in names:
        payload = files[name]
        items = payload.get("items") or []
        total += len(items)
        label, hint = SOURCE_META.get(name, (name, ""))

        if name == "sspai":
            body = panel_sspai(items)
        elif name == "telegram":
            body = panel_telegram(items, base_dir)
        elif name == "linuxdo":
            body = panel_linuxdo(items)
        elif name in ("ruanyifeng", "anywayfm", "latepost", "geekpark"):
            body = panel_simple(items, with_body=True)
        else:
            body = panel_simple(items)

        active = " active" if name == names[0] else ""
        tabs.append(f'<button class="tab{active}" data-tab="{esc(name)}">{esc(label)}<i>{len(items)}</i></button>')
        panels.append(f'''<section class="panel{active}" id="panel-{esc(name)}">
  <div class="panel-head"><h2>{esc(label)}</h2><span class="hint">{esc(hint)}</span></div>
  {body or '<p class="empty">无数据</p>'}
</section>''')

    collected = files[names[0]].get("collected_at", "") if names else ""
    generated = fmt_time(collected)

    return f'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>每日摘要 · {total} 条</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
:root{{--bg:#000;--fg:#e8e8e8;--dim:#8a8a8a;--dimmer:#5c5c5c;--accent:#ff2f2f;--line:#1a1a1a;--panel:#080808}}
html{{background:var(--bg)}}
body{{background:var(--bg);color:var(--fg);font:15px/1.65 -apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
  -webkit-font-smoothing:antialiased}}
a{{color:var(--fg);text-decoration:none;border-bottom:1px solid transparent}}
a:hover{{color:var(--accent);border-bottom-color:var(--accent)}}
code,time,.meta,.ds,.rank,.heat,.ft,.idx,.badge,.count{{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}}

/* ---- sticky bar ---- */
.bar{{position:sticky;top:0;z-index:50;background:rgba(0,0,0,.94);border-bottom:1px solid var(--line);
  backdrop-filter:saturate(0) blur(6px)}}
.bar-inner{{width:min(1400px,100% - 32px);margin:0 auto;display:flex;align-items:center;gap:20px;
  padding:10px 0;flex-wrap:wrap}}
.brand{{font-size:15px;font-weight:700;letter-spacing:.02em;white-space:nowrap}}
.brand b{{color:var(--accent)}}
.brand span{{color:var(--dimmer);font-weight:400;font-size:12px;margin-left:8px;font-family:ui-monospace,monospace}}
.tabs{{display:flex;gap:0;overflow-x:auto;flex:1;scrollbar-width:none}}
.tabs::-webkit-scrollbar{{display:none}}
.tab{{background:none;border:0;border-bottom:2px solid transparent;color:var(--dim);font:inherit;font-size:13px;
  padding:6px 12px;cursor:pointer;white-space:nowrap;display:flex;align-items:center;gap:6px}}
.tab:hover{{color:var(--fg)}}
.tab.active{{color:var(--fg);border-bottom-color:var(--accent)}}
.tab i{{font-style:normal;color:var(--dimmer);font-size:11px;font-family:ui-monospace,monospace}}
.tab.active i{{color:var(--accent)}}
.kbd{{color:var(--dimmer);font-size:11px;font-family:ui-monospace,monospace;white-space:nowrap}}

/* ---- layout ---- */
main{{width:min(1400px,100% - 32px);margin:0 auto;padding:26px 0 90px}}
.panel{{display:none}}
.panel.active{{display:block}}
.panel-head{{display:flex;align-items:baseline;gap:12px;padding-bottom:14px;border-bottom:1px solid var(--line);margin-bottom:20px}}
.panel-head h2{{font-size:20px;font-weight:700;letter-spacing:.01em}}
.hint{{color:var(--dimmer);font-size:12px}}
.empty{{color:var(--dim)}}
.group{{font-size:13px;font-weight:700;color:var(--accent);margin:26px 0 10px;padding-bottom:6px;
  border-bottom:1px solid var(--line);display:flex;align-items:center;gap:10px;letter-spacing:.04em}}
.group:first-child{{margin-top:0}}
.gcount{{color:var(--dimmer);font-weight:400;font-family:ui-monospace,monospace}}
.issue-head{{color:var(--dim);font-size:13px;margin-bottom:18px;padding-left:10px;border-left:2px solid var(--accent)}}

/* ---- rows ---- */
.rows{{list-style:none}}
.row{{display:flex;gap:14px;padding:13px 0;border-bottom:1px solid var(--line)}}
.row:last-child{{border-bottom:0}}
.idx{{color:var(--dimmer);font-size:11px;padding-top:3px;min-width:18px}}
.rowbody{{min-width:0;flex:1}}
.t{{font-weight:600;font-size:15px;line-height:1.45}}
.meta{{color:var(--dim);font-size:11px;margin-top:5px;display:flex;flex-wrap:wrap;gap:10px;align-items:center}}
.meta .ds{{color:var(--dimmer)}}
.meta .sec{{color:var(--accent)}}
.meta .rank{{color:var(--accent)}}
.meta .heat{{color:var(--dim)}}
.meta .ft{{color:#b5b5b5}}
.sum{{color:var(--dim);font-size:13px;margin-top:6px;line-height:1.6}}
.badge{{border:1px solid var(--line);color:var(--dim);font-size:10px;padding:1px 6px;letter-spacing:.05em}}
.badge.hot{{border-color:var(--accent);color:var(--accent)}}

/* ---- full text ---- */
details.full{{margin-top:8px}}
details.full summary{{cursor:pointer;color:var(--accent);font-size:12px;font-family:ui-monospace,monospace;list-style:none}}
details.full summary::-webkit-details-marker{{display:none}}
details.full summary::before{{content:"▸ ";color:var(--accent)}}
details.full[open] summary::before{{content:"▾ "}}
.article{{margin:14px 0 6px;padding-left:14px;border-left:1px solid var(--line);max-width:78ch}}
.article p{{margin-bottom:12px;color:#cfcfcf}}
.article p:last-child{{margin-bottom:0}}

/* ---- sspai feature ---- */
.feature{{border:1px solid var(--line);padding:20px;margin-bottom:22px;background:var(--panel)}}
.feature-head{{margin-bottom:16px}}
.ft-title{{display:block;font-size:18px;font-weight:700;margin:10px 0 8px;line-height:1.4}}
.feature-body{{border-top:1px solid var(--line);padding-top:16px}}
.block{{margin-bottom:18px}}
.block h4{{font-size:14px;color:var(--accent);margin-bottom:6px;font-weight:700}}
.block p{{color:#c4c4c4;font-size:14px;margin-bottom:8px}}

/* ---- telegram masonry ---- */
.masonry{{columns:3;column-gap:14px}}
@media(max-width:1100px){{.masonry{{columns:2}}}}
@media(max-width:680px){{.masonry{{columns:1}}}}
.card{{break-inside:avoid;border:1px solid var(--line);background:var(--panel);padding:13px;margin-bottom:14px}}
.card-head{{display:flex;align-items:center;gap:10px;font-size:11px;font-family:ui-monospace,monospace;
  color:var(--dim);padding-bottom:8px;margin-bottom:9px;border-bottom:1px solid var(--line);flex-wrap:wrap}}
.card-head .ch{{color:var(--accent);font-weight:700}}
.card-head .msgid{{color:var(--dimmer)}}
.card-body{{font-size:13.5px;line-height:1.62;color:#dcdcdc;overflow-wrap:anywhere}}
.shots{{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:10px}}
.shots img{{width:100%;height:auto;display:block;border:1px solid var(--line)}}
.card-foot{{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-top:10px;font-size:11px;
  font-family:ui-monospace,monospace;color:var(--dimmer)}}
.rx{{display:flex;gap:6px;flex-wrap:wrap}}
.rx span{{border:1px solid var(--line);padding:0 5px;color:var(--dim)}}

footer{{width:min(1400px,100% - 32px);margin:0 auto;padding:0 0 70px;color:var(--dimmer);font-size:12px}}
footer code{{color:var(--dim)}}
@media(max-width:680px){{.bar-inner,main,footer{{width:calc(100% - 24px)}}.kbd{{display:none}}}}
</style>
</head>
<body>
<header class="bar"><div class="bar-inner">
  <div class="brand"><b>●</b> 每日摘要 <span>{esc(generated)} · {total} 条</span></div>
  <nav class="tabs">{"".join(tabs)}</nav>
  <span class="kbd">按 1–{len(names)} 切换</span>
</div></header>
<main>{"".join(panels)}</main>
<footer>本地生成 · <code>tools/collect.py</code> → <code>tools/render_digest.py</code> ·
每条保留原始链接与时间戳；摘要为原文摘录，非生成内容。</footer>
<script>
(function(){{
  var tabs = [].slice.call(document.querySelectorAll('.tab'));
  var panels = [].slice.call(document.querySelectorAll('.panel'));
  function show(id){{
    tabs.forEach(function(t){{ t.classList.toggle('active', t.dataset.tab === id); }});
    panels.forEach(function(p){{ p.classList.toggle('active', p.id === 'panel-' + id); }});
    if (history.replaceState) history.replaceState(null, '', '#' + id);
  }}
  tabs.forEach(function(t){{ t.addEventListener('click', function(){{ show(t.dataset.tab); }}); }});
  document.addEventListener('keydown', function(e){{
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    var n = parseInt(e.key, 10);
    if (n >= 1 && n <= tabs.length) show(tabs[n-1].dataset.tab);
  }});
  var hash = location.hash.slice(1);
  if (hash && document.getElementById('panel-' + hash)) show(hash);
}})();
</script>
</body></html>'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--indir", type=Path, default=Path("data/collected"))
    parser.add_argument("--output", type=Path, default=Path("site/digest.html"))
    args = parser.parse_args()

    files: dict[str, dict] = {}
    for path in sorted(args.indir.glob("*.json")):
        if path.name in ("index.json", "telegram_ai_queue.json", "telegram_duplicate_groups.json"):
            continue
        try:
            files[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue

    if not files:
        print("no collected data found; run tools/collect.py first")
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(files, args.output), encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

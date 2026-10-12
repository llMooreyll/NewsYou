#!/usr/bin/env python3
"""Render data/collected/*.json into one styled daily digest page.

Usage:
    python tools/feed_collect.py --all --fulltext --outdir data/collected
    python tools/render_digest_html.py --indir data/collected \
        --output site/captures/digest.html
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
from pathlib import Path

SOURCE_META = {
    "ruanyifeng": ("阮一峰的网络日志", "中文科技周刊", "zh"),
    "anywayfm": ("Anyway.Now 安妮薇看看", "设计 / 产品 链接集", "zh"),
    "sspai": ("少数派 · 派早报", "每日科技早报", "zh"),
    "linuxdo": ("linux.do 晚报", "社区热帖日报", "zh"),
    "hackernews": ("Hacker News", "top stories", "en"),
    "weibo": ("微博热搜", "实时热搜榜", "zh"),
    "zhihu": ("知乎热榜", "全站热榜", "zh"),
}
ORDER = ["sspai", "ruanyifeng", "linuxdo", "anywayfm", "hackernews", "weibo", "zhihu"]


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def linkify(text: str) -> str:
    escaped = esc(text or "")
    escaped = re.sub(r"(https?://[^\s<]+)", r'<a href="\1" rel="noopener noreferrer">\1</a>', escaped)
    return escaped.replace("\n", "<br>")


def fmt_time(value) -> str:
    if not value:
        return ""
    text = str(value).replace("+00:00", " UTC").replace("T", " ")
    return text[:19] + (" UTC" if text.endswith("UTC") else "")


def render_item(item: dict, index: int) -> str:
    title = esc(item.get("title"))
    url = item.get("url") or ""
    meta_bits = []
    if item.get("rank"):
        meta_bits.append(f"#{item['rank']}")
    if item.get("heat"):
        meta_bits.append(str(item["heat"]))
    if item.get("score"):
        meta_bits.append(f"{item['score']} pts")
    if item.get("comments"):
        meta_bits.append(f"{item['comments']} 💬")
    if item.get("published_at"):
        meta_bits.append(fmt_time(item["published_at"]))
    if item.get("position"):
        meta_bits.append(f"#{item['position']}")

    meta = f'<span class="meta">{esc(" · ".join(map(str, meta_bits)))}</span>' if meta_bits else ""
    summary = item.get("summary") or ""
    summary_html = f'<p class="summary">{linkify(summary[:400])}</p>' if summary.strip() else ""

    return f'''<li class="item">
  <span class="num">{index:02d}</span>
  <div class="item-body">
    <a class="title" href="{esc(url)}" rel="noopener noreferrer">{title}</a>
    {meta}
    {summary_html}
  </div>
</li>'''


def render_sspai(item: dict) -> str:
    """派早报 gets a nested list because each issue bundles many news items."""
    sections = item.get("sections") or []
    inner = "".join(
        f'<li><span class="sec-title">{esc(s.get("title"))}</span>'
        f'<p>{linkify((s.get("text") or "")[:420])}</p></li>'
        for s in sections
    )
    return f'''<article class="feature">
  <div class="feature-head">
    <span class="badge">最新一期</span>
    <a class="feature-title" href="{esc(item.get("url"))}" rel="noopener noreferrer">{esc(item.get("title"))}</a>
    <span class="meta">{esc(item.get("author") or "")} · {fmt_time(item.get("published_at"))} · {item.get("body_chars") or 0} 字</span>
  </div>
  <ol class="sections">{inner or "<li>正文未抓取，可加 --fulltext 重试</li>"}</ol>
</article>'''


def render_source(name: str, payload: dict) -> str:
    label, subtitle, _lang = SOURCE_META.get(name, (name, "", ""))
    items = payload.get("items") or []
    body = ""

    if name == "sspai" and items and items[0].get("sections"):
        body += render_sspai(items[0])
        rest = items[1:]
    else:
        rest = items

    if rest:
        body += "<ol class=\"items\">" + "".join(
            render_item(item, i) for i, item in enumerate(rest, 1)
        ) + "</ol>"

    return f'''<section class="source" id="{esc(name)}">
  <header class="source-head">
    <h2>{esc(label)}</h2>
    <span class="sub">{esc(subtitle)}</span>
    <span class="count">{len(items)} 条</span>
  </header>
  {body or '<p class="meta">无数据</p>'}
</section>'''


def render(files: dict[str, dict], indir: Path, output: Path) -> str:
    order = [n for n in ORDER if n in files] + [n for n in files if n not in ORDER]
    nav = "".join(
        f'<a href="#{esc(n)}">{esc(SOURCE_META.get(n, (n, "", ""))[0])}</a>' for n in order
    )
    total = sum(len(files[n].get("items") or []) for n in order)
    generated = files[order[0]].get("collected_at") if order else ""
    sections = "".join(render_source(n, files[n]) for n in order)

    return f'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>每日摘要 · {total} 条</title>
<style>
:root{{--bg:#0b1020;--panel:#121a2e;--ink:#edf3ff;--muted:#9eacc8;--line:#2a385b;--accent:#78e6c1;--accent2:#8bb7ff}}
*{{box-sizing:border-box}}
body{{margin:0;background:radial-gradient(circle at 16% 0%,#1b3156 0,transparent 32%),var(--bg);color:var(--ink);
  font:16px/1.7 system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
.wrap{{width:min(940px,calc(100% - 32px));margin:auto}}
header.top{{padding:52px 0 26px;border-bottom:1px solid var(--line)}}
.eyebrow{{color:var(--accent);text-transform:uppercase;letter-spacing:.15em;font-size:.74rem;font-weight:700}}
h1{{font-size:clamp(1.9rem,4.6vw,3.2rem);margin:12px 0 12px;letter-spacing:-.04em}}
.stats{{color:var(--muted);font-size:.9rem}}
nav{{display:flex;flex-wrap:wrap;gap:8px;padding:18px 0 0}}
nav a{{border:1px solid var(--line);background:#101a2e;border-radius:999px;padding:4px 12px;font-size:.82rem;
  color:#cbd8f5;text-decoration:none}}
nav a:hover{{border-color:var(--accent);color:var(--accent)}}
main{{padding:8px 0 70px}}
.source{{padding:34px 0;border-bottom:1px solid var(--line)}}
.source-head{{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:16px}}
.source-head h2{{font-size:1.4rem;margin:0;letter-spacing:-.02em}}
.sub{{color:var(--muted);font-size:.84rem}}
.count{{margin-left:auto;color:var(--accent);font-size:.8rem;font-variant-numeric:tabular-nums}}
.items{{list-style:none;margin:0;padding:0}}
.item{{display:flex;gap:14px;padding:12px 0;border-top:1px dashed var(--line)}}
.item:first-child{{border-top:0}}
.num{{color:var(--muted);font-size:.78rem;font-variant-numeric:tabular-nums;padding-top:3px;min-width:20px}}
.item-body{{min-width:0}}
.title{{color:var(--ink);text-decoration:none;font-weight:600}}
.title:hover{{color:var(--accent2);text-decoration:underline}}
.meta{{display:block;color:var(--muted);font-size:.78rem;margin-top:3px;font-variant-numeric:tabular-nums}}
.summary{{color:#b9c6e2;font-size:.9rem;margin:6px 0 0}}
.feature{{background:linear-gradient(150deg,var(--panel),#0f172b);border:1px solid var(--line);border-radius:16px;padding:20px;margin-bottom:16px}}
.feature-head{{margin-bottom:12px}}
.badge{{display:inline-block;border:1px solid #347e6b;background:#12372f;color:var(--accent);border-radius:999px;
  padding:1px 10px;font-size:.72rem;margin-bottom:8px}}
.feature-title{{display:block;color:var(--ink);text-decoration:none;font-weight:700;font-size:1.08rem}}
.feature-title:hover{{color:var(--accent)}}
.sections{{margin:12px 0 0;padding-left:20px}}
.sections li{{margin-bottom:14px}}
.sec-title{{font-weight:600;display:block;margin-bottom:4px}}
.sections p{{margin:0;color:#b9c6e2;font-size:.9rem}}
footer{{padding:26px 0 60px;color:var(--muted);font-size:.85rem}}
</style></head>
<body>
<header class="top"><div class="wrap">
  <div class="eyebrow">Newsletter digest · script-generated</div>
  <h1>每日摘要</h1>
  <div class="stats">{total} 条 · {len(order)} 个来源 · 采集于 {esc(fmt_time(generated))}</div>
  <nav>{nav}</nav>
</div></header>
<main class="wrap">{sections}</main>
<footer class="wrap">由 <code>tools/feed_collect.py</code> + <code>tools/render_digest_html.py</code> 本地生成。
每条均保留原始链接；摘要为原文摘录，非生成内容。</footer>
</body></html>'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--indir", type=Path, default=Path("data/collected"))
    parser.add_argument("--output", type=Path, default=Path("site/captures/digest.html"))
    args = parser.parse_args()

    files: dict[str, dict] = {}
    for path in sorted(args.indir.glob("*.json")):
        if path.name == "index.json":
            continue
        try:
            files[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(files, args.indir, args.output), encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

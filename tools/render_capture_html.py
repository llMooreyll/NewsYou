#!/usr/bin/env python3
"""Render a CDP JSON capture as a self-contained, readable HTML report."""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


def esc(value) -> str:
    return html.escape(str(value or ""), quote=True)


def render(data: dict, source_path: Path) -> str:
    text = data.get("text", "")
    status = data.get("capture_status", "unknown")
    status_label = "Capture available" if status == "ok" and len(text) > 0 else "Capture needs attention"
    status_class = "ok" if status == "ok" and len(text) > 0 else "warn"
    links = data.get("links") or []
    link_html = "\n".join(
        f'<li><a href="{esc(item.get("href"))}">{esc(item.get("text")) or esc(item.get("href"))}</a></li>'
        for item in links
        if item.get("href")
    )
    pretty = json.dumps(data, ensure_ascii=False, indent=2)
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(data.get("title")) or "Browser capture"} · Newsletter capture</title>
<style>
:root{{--bg:#0b1020;--panel:#121a2e;--panel2:#182440;--ink:#edf3ff;--muted:#9eacc8;--line:#2a385b;--accent:#78e6c1;--warn:#ffc875;--bad:#ff8b9f}}
*{{box-sizing:border-box}} body{{margin:0;background:radial-gradient(circle at 20% 0%,#1b3156 0,transparent 36%),var(--bg);color:var(--ink);font:16px/1.6 system-ui,-apple-system,Segoe UI,sans-serif}}
.wrap{{width:min(1080px,calc(100% - 36px));margin:auto}} header{{padding:48px 0 34px;border-bottom:1px solid var(--line)}}
.eyebrow{{color:var(--accent);text-transform:uppercase;letter-spacing:.14em;font-size:.75rem;font-weight:700}}h1{{font-size:clamp(1.8rem,4vw,3.4rem);line-height:1.08;margin:12px 0 18px;letter-spacing:-.04em}}h2{{font-size:1.25rem;margin:0 0 12px}}section{{padding:28px 0;border-bottom:1px solid var(--line)}}
.card{{background:linear-gradient(145deg,var(--panel),#0f172b);border:1px solid var(--line);border-radius:16px;padding:20px;margin:16px 0}}.meta{{display:grid;grid-template-columns:160px 1fr;gap:8px;color:var(--muted)}}.meta b{{color:var(--ink)}}
.status{{display:inline-block;border-radius:999px;padding:5px 12px;border:1px solid;font-weight:700}}.status.ok{{color:var(--accent);border-color:#347e6b;background:#12372f}}.status.warn{{color:var(--warn);border-color:#806438;background:#3a2b13}}
.content{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f1e8;color:#202735;border-radius:14px;padding:24px;font:16px/1.75 Georgia,serif;min-height:80px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#080d18;border:1px solid var(--line);padding:18px;border-radius:12px;color:#c7d8ff;font-size:.82rem;max-height:600px;overflow:auto}}a{{color:#8bb7ff}}ul{{padding-left:24px;color:var(--muted)}}footer{{padding:24px 0 60px;color:var(--muted)}}
@media(max-width:600px){{.wrap{{width:calc(100% - 24px)}}.meta{{grid-template-columns:1fr}}}}
</style></head>
<body><header><div class="wrap"><div class="eyebrow">Newsletter browser capture</div><h1>{esc(data.get("title")) or "Untitled page"}</h1><span class="status {status_class}">{status_label}</span></div></header>
<main class="wrap"><section><h2>Capture metadata</h2><div class="card"><div class="meta">
<b>Requested URL</b><span>{esc(data.get("requested_url"))}</span>
<b>Final URL</b><span>{esc(data.get("url"))}</span>
<b>Language</b><span>{esc(data.get("language")) or "Not detected"}</span>
<b>Ready state</b><span>{esc(data.get("ready_state"))}</span>
<b>Method</b><span>{esc(data.get("capture_method"))}</span>
<b>Status</b><span>{esc(data.get("capture_status"))}</span>
<b>Captured at</b><span>{esc(data.get("captured_at"))}</span>
</div></div></section>
<section><h2>Readable page text</h2><div class="content">{esc(text) or "No readable page content was captured."}</div></section>
<section><h2>Links discovered</h2><div class="card"><ul>{link_html or "<li>No links discovered.</li>"}</ul></div></section>
<section><h2>Raw JSON</h2><details><summary>Show raw capture JSON</summary><pre>{esc(pretty)}</pre></details></section></main>
<footer class="wrap">Generated locally from <code>{esc(source_path)}</code>. This is an evidence/capture report, not a bypass of access controls.</footer></body></html>'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(data, args.input), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()

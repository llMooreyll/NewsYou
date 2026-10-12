#!/usr/bin/env python3
"""Render a telegram_preview.py JSON capture as a styled local digest page."""
from __future__ import annotations

import argparse
import base64
import html
import json
import mimetypes
import os
import re
from pathlib import Path

MEDIA_ICONS = {
    "photo": "🖼",
    "video": "🎬",
    "video_message": "⭕",
    "document": "📄",
    "voice": "🎙",
    "audio": "🎵",
    "poll": "📊",
    "sticker": "🏷",
    "link_preview": "🔗",
    "forwarded": "🔁",
    "reply": "↩️",
    "pinned": "📌",
}


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def relpath(target: str, base_dir: Path) -> str:
    """Relative link from the generated HTML file to a local media file."""
    try:
        return os.path.relpath(target, base_dir)
    except ValueError:
        return target


def data_uri(path: Path) -> str:
    """Inline a local image so the page is fully self-contained."""
    raw = path.read_bytes()
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def linkify(text: str) -> str:
    escaped = esc(text)
    escaped = re.sub(
        r"(https?://[^\s<]+)",
        r'<a href="\1" rel="noopener noreferrer">\1</a>',
        escaped,
    )
    return escaped.replace("\n", "<br>")


def render(data: dict, source_path: Path, output_path: Path, embed: bool = False) -> str:
    source = data.get("source", {})
    channel = source.get("channel", "unknown")
    posts = data.get("posts", [])
    base_dir = output_path.parent
    local_count = 0

    cards = []
    for index, post in enumerate(posts, 1):
        media = " ".join(MEDIA_ICONS.get(m, m) for m in post.get("media", []))
        body = linkify(post.get("text") or "(media or empty post)")

        local = [p for p in (post.get("local_images") or []) if Path(p).exists()]
        remote = list(post.get("photos") or [])
        local_count += len(local)

        figures = []
        for n, path in enumerate(local, 1):
            fallback = remote[n - 1] if n - 1 < len(remote) else ""
            onerror = (
                f" onerror=\"this.onerror=null;this.src='{esc(fallback)}'\"" if fallback else ""
            )
            src = data_uri(Path(path)) if embed else relpath(path, base_dir)
            href = fallback or (src if embed else relpath(path, base_dir))
            figures.append(
                f'<a class="shot" href="{esc(href)}" target="_blank" rel="noopener noreferrer">'
                f'<img loading="lazy" src="{esc(src)}" alt="image {n}"{onerror}></a>'
            )
        for n, url in enumerate(remote[len(local):], len(local) + 1):
            figures.append(
                f'<a class="shot" href="{esc(url)}" target="_blank" rel="noopener noreferrer">'
                f'<img loading="lazy" src="{esc(url)}" alt="image {n}"></a>'
            )
        gallery = f'<div class="gallery">{"".join(figures)}</div>' if figures else ""

        for url in (post.get("video_thumbs") or [])[:3]:
            gallery += (
                f'<div class="gallery"><a class="shot" href="{esc(url)}" target="_blank" '
                f'rel="noopener noreferrer"><img loading="lazy" src="{esc(url)}" alt="video thumbnail"></a></div>'
            )

        reactions = post.get("reactions") or []
        reaction_html = (
            '<div class="reactions">'
            + "".join(
                f'<span class="reaction">{esc(r.get("emoji"))}<b>{esc(r.get("count")) if r.get("count") is not None else ""}</b></span>'
                for r in reactions
            )
            + "</div>"
            if reactions
            else ""
        )

        if post.get("document_titles"):
            gallery += '<div class="docs">' + "".join(
                f'📄 {esc(d)}' for d in post["document_titles"]
            ) + "</div>"

        downloaded = (
            f'<span class="pill ok">🖼 {len(local)} {"embedded" if embed else "saved"}</span>'
            if local
            else (f'<span class="pill">🖼 {len(remote)}</span>' if remote else "")
        )

        cards.append(
            f'''<article class="post">
  <div class="post-head">
    <span class="index">{index:02d}</span>
    <a class="id" href="{esc(post.get("permalink"))}" rel="noopener noreferrer">#{esc(post.get("message_id"))}</a>
    <time>{esc((post.get("published_at") or "").replace("T", " ").replace("+00:00", " UTC"))}</time>
    <span class="spacer"></span>
    {f'<span class="pill">{esc(post.get("views_text"))} views</span>' if post.get("views_text") else ""}
    {f'<span class="pill">{media}</span>' if media else ""}
    {downloaded}
  </div>
  <div class="body">{body}</div>
  {gallery}
  {reaction_html}
</article>'''
        )

    return f'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>@{esc(channel)} · newest {len(posts)} posts</title>
<style>
:root{{--bg:#0b1020;--panel:#121a2e;--ink:#edf3ff;--muted:#9eacc8;--line:#2a385b;--accent:#78e6c1}}
*{{box-sizing:border-box}}
body{{margin:0;background:radial-gradient(circle at 18% 0%,#1b3156 0,transparent 34%),var(--bg);color:var(--ink);
     font:16px/1.75 system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
.wrap{{width:min(920px,calc(100% - 32px));margin:auto}}
header{{padding:52px 0 30px;border-bottom:1px solid var(--line)}}
.eyebrow{{color:var(--accent);text-transform:uppercase;letter-spacing:.15em;font-size:.74rem;font-weight:700}}
h1{{font-size:clamp(1.9rem,4.5vw,3.1rem);margin:12px 0 14px;letter-spacing:-.04em}}
.meta{{color:var(--muted);font-size:.9rem}}
.meta code{{background:#0d1526;padding:2px 6px;border-radius:6px;border:1px solid var(--line)}}
main{{padding:26px 0 60px}}
.post{{background:linear-gradient(150deg,var(--panel),#0f172b);border:1px solid var(--line);border-radius:16px;
      padding:18px 20px;margin:14px 0}}
.post-head{{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding-bottom:10px;border-bottom:1px dashed var(--line);margin-bottom:12px}}
.index{{color:var(--accent);font-weight:800;font-size:.8rem;font-variant-numeric:tabular-nums}}
.id{{color:#8bb7ff;text-decoration:none;font-weight:600;font-variant-numeric:tabular-nums}}
.id:hover{{text-decoration:underline}}
time{{color:var(--muted);font-size:.8rem;font-variant-numeric:tabular-nums}}
.spacer{{flex:1}}
.pill{{border:1px solid var(--line);background:#0e1729;border-radius:999px;padding:2px 10px;font-size:.75rem;color:#cbd8f5;white-space:nowrap}}
.body{{overflow-wrap:anywhere;margin-bottom:12px}}
.body a{{color:#8bb7ff}}
.gallery{{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:10px;margin:14px 0 10px}}
.shot{{display:block;border:1px solid var(--line);border-radius:10px;overflow:hidden;background:#0a1120}}
.shot img{{display:block;width:100%;height:auto;max-height:340px;object-fit:cover}}
.shot:hover{{border-color:var(--accent)}}
.docs{{color:var(--muted);font-size:.85rem;margin:6px 0}}
.reactions{{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px;padding-top:10px;border-top:1px dashed var(--line)}}
.reaction{{border:1px solid var(--line);background:#0e1729;border-radius:999px;padding:1px 9px;font-size:.78rem}}
.reaction b{{color:var(--muted);font-weight:600;margin-left:4px}}
.pill.ok{{border-color:#347e6b;background:#12372f;color:var(--accent)}}
footer{{padding:20px 0 60px;color:var(--muted);font-size:.85rem}}
</style></head>
<body>
<header><div class="wrap">
  <div class="eyebrow">Telegram public preview · no credentials</div>
  <h1>@{esc(channel)}</h1>
  <div class="meta">{len(posts)} posts &nbsp;·&nbsp; {local_count} images {"embedded in this file" if embed else "linked locally"} &nbsp;·&nbsp;
    captured {esc(data.get("captured_at"))} &nbsp;·&nbsp; method <code>{esc(data.get("capture_method"))}</code></div>
</div></header>
<main class="wrap">
{"".join(cards) if cards else '<p class="meta">No posts captured.</p>'}
</main>
<footer class="wrap">Generated locally from <code>{esc(source_path)}</code>.
Original posts remain the source of truth; follow the #id links for citation.</footer>
</body></html>'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--embed",
        action="store_true",
        help="inline images as base64 data URIs so the page works anywhere",
    )
    args = parser.parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(data, args.input, args.output, args.embed), encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

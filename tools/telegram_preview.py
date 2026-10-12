#!/usr/bin/env python3
"""Collect public Telegram channel posts with no API key, no login, no bot.

This uses Telegram's public web preview at https://t.me/s/<channel>, which is
the same mechanism RSSHub falls back to. It requires only Python's standard
library and works for PUBLIC channels only.

First page:      latest ~20 posts
Older pages:     ?before=<lowest message id already seen>

Usage:
    python tools/telegram_preview.py zaihuapd --limit 10
    python tools/telegram_preview.py telegram --pages 3 --output data/telegram/telegram.json
    python tools/telegram_preview.py telegram --before 441
    python tools/telegram_preview.py zaihuapd --limit 10 --download-images
    python tools/telegram_preview.py telegram --list

This is a plain HTTP fetch of a public page. It does not log in, does not use
your account, and does not attempt to reach channels that Telegram hides from
the public preview.

Images are only downloaded when you pass --download-images. Keep volumes
bounded: media is copyrighted by its original owners and is fetched here for
personal reference only.
"""
from __future__ import annotations

import argparse
import html as html_mod
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
DEFAULT_MEDIA_ROOT = Path("data/telegram/media")

MESSAGE_SPLIT = re.compile(r'(?=<div class="tgme_widget_message\b)')
POST_ID = re.compile(r'data-post="([^"]+)"')
DATE = re.compile(r'<time[^>]+datetime="([^"]+)"')
VIEWS = re.compile(r'tgme_widget_message_views">([^<]+)<')
AUTHOR = re.compile(r'tgme_widget_message_author[^>]*>(.*?)</a>', re.S)
TEXT_DIV = re.compile(
    r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>\s*'
    r'(?:<div class="tgme_widget_message_(?:footer|reply_markup)|</div>)',
    re.S,
)
REACTIONS_BLOCK = re.compile(r'<div class="tgme_widget_message_reactions.*?</div>', re.S)
REACTION = re.compile(
    r'<span class="tgme_reaction"><i class="emoji"[^>]*><b>([^<]+)</b></i>([^<]*)</span>'
)
EMOJI_ASSET = re.compile(r'^//telegram\.org/img/emoji/')

# media capture
PHOTO_TAG = re.compile(r'<a class="tgme_widget_message_photo_wrap[^>]*>', re.S)
VIDEO_THUMB_TAG = re.compile(r'<a class="tgme_widget_message_video_thumb[^>]*>', re.S)
STICKER_TAG = re.compile(r'<div class="tgme_widget_message_sticker[^"]*"[^>]*>', re.S)
STYLE_URL = re.compile(r"background-image:url\('([^']+)'\)")
VIDEO_SRC = re.compile(r'<video[^>]+src="([^"]+)"')
DOC_TITLE = re.compile(r'tgme_widget_message_document_title[^>]*>([^<]+)<')
LINK_PREVIEW_TAG = re.compile(r'<a class="tgme_widget_message_link_preview[^>]*>', re.S)
IMG_SRC = re.compile(r'<img[^>]+src="([^"]+)"')

MEDIA_MARKERS = {
    "photo": "tgme_widget_message_photo_wrap",
    "video": "tgme_widget_message_video_wrap",
    "video_message": "tgme_widget_message_roundvideo",
    "document": "tgme_widget_message_document_wrap",
    "voice": "tgme_widget_message_voice",
    "audio": "tgme_widget_message_audio",
    "poll": "tgme_widget_message_poll",
    "sticker": "tgme_widget_message_sticker",
    "link_preview": "tgme_widget_message_link_preview",
    "forwarded": "tgme_widget_message_forwarded_from",
    "reply": "tgme_widget_message_reply",
    "pinned": "tgme_widget_message_pinned",
}

CONTENT_TYPE_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "video/mp4": ".mp4",
}


class PreviewError(RuntimeError):
    pass


def strip_tags(fragment: str) -> str:
    fragment = re.sub(r'<br\s*/?>', '\n', fragment)
    fragment = re.sub(r'</p>\s*<p[^>]*>', '\n\n', fragment)
    fragment = re.sub(r'<[^>]+>', '', fragment)
    text = html_mod.unescape(fragment)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def normalize_url(url: str) -> str:
    if url.startswith("//"):
        return "https:" + url
    return url


def fetch(url: str, timeout: int, retries: int = 2) -> str:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(
            url, headers={"User-Agent": DEFAULT_UA, "Accept-Language": "en,zh;q=0.8"}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise PreviewError(f"could not fetch {url}: {last_error}")


def fetch_bytes(url: str, timeout: int) -> tuple[bytes, str]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": DEFAULT_UA,
            "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
            "Referer": "https://t.me/",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(), response.headers.get("Content-Type", "").split(";")[0].strip().lower()


def parse_reactions(block: str) -> list[dict]:
    reactions = []
    for emoji, count in REACTION.findall(block):
        count_text = count.strip().replace("\u00a0", "")
        try:
            value = int(count_text)
        except ValueError:
            value = None
        reactions.append({"emoji": emoji.strip(), "count": value})
    return reactions


def style_urls(tags: list[str]) -> list[str]:
    urls = []
    for tag in tags:
        match = STYLE_URL.search(tag)
        if match:
            urls.append(normalize_url(match.group(1)))
    return urls


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def parse_posts(page_html: str) -> list[dict]:
    posts: list[dict] = []
    for block in MESSAGE_SPLIT.split(page_html):
        if "data-post=" not in block:
            continue
        post_match = POST_ID.search(block)
        if not post_match:
            continue
        post_ref = post_match.group(1)
        channel, _, message_id = post_ref.rpartition("/")

        reactions = parse_reactions(block)
        text_block = REACTIONS_BLOCK.sub("", block)

        text_match = TEXT_DIV.search(text_block)
        text = strip_tags(text_match.group(1)) if text_match else ""

        date_match = DATE.search(text_block)
        views_match = VIEWS.search(text_block)
        author_match = AUTHOR.search(text_block)

        photos = dedupe(style_urls(PHOTO_TAG.findall(text_block)))
        video_thumbs = dedupe(style_urls(VIDEO_THUMB_TAG.findall(text_block)))
        stickers = dedupe(
            url for url in style_urls(STICKER_TAG.findall(text_block)) if not EMOJI_ASSET.match(url)
        )
        videos = dedupe([normalize_url(v) for v in VIDEO_SRC.findall(text_block)])
        documents = [d.strip() for d in DOC_TITLE.findall(text_block)]
        link_preview_images = [
            normalize_url(u)
            for tag in LINK_PREVIEW_TAG.findall(text_block)
            for u in IMG_SRC.findall(tag)
            if not EMOJI_ASSET.match(normalize_url(u))
        ]

        media = sorted(name for name, marker in MEDIA_MARKERS.items() if marker in text_block)

        posts.append(
            {
                "platform": "telegram",
                "channel": channel,
                "message_id": int(message_id) if message_id.isdigit() else message_id,
                "permalink": f"https://t.me/{post_ref}",
                "published_at": date_match.group(1) if date_match else None,
                "author": strip_tags(author_match.group(1)) if author_match else None,
                "text": text,
                "views_text": views_match.group(1) if views_match else None,
                "reactions": reactions,
                "media": media,
                "photos": photos,
                "video_src": videos,
                "video_thumbs": dedupe(video_thumbs + [u for u in link_preview_images if u not in video_thumbs]),
                "stickers": stickers,
                "document_titles": documents,
                "capture_method": "telegram_public_preview",
            }
        )
    return posts


def collect(
    channel: str,
    pages: int,
    before: int | None,
    timeout: int,
    delay: float,
    limit: int | None = None,
) -> list[dict]:
    channel = channel.lstrip("@").strip("/")
    for prefix in ("https://t.me/s/", "http://t.me/s/", "t.me/s/"):
        if channel.startswith(prefix):
            channel = channel[len(prefix):]

    collected: list[dict] = []
    seen: set = set()
    cursor = before

    for page_index in range(pages):
        url = f"https://t.me/s/{channel}"
        if cursor is not None:
            url += f"?before={cursor}"
        page_html = fetch(url, timeout=timeout)

        if "tgme_widget_message" not in page_html and "tgme_channel_info" not in page_html:
            if page_index == 0:
                raise PreviewError(
                    f"no public preview content for '{channel}'. "
                    "It may be private, renamed, or hidden from t.me/s."
                )
            break

        posts = parse_posts(page_html)
        if not posts:
            break

        new_posts = [p for p in posts if (p["channel"], p["message_id"]) not in seen]
        for post in new_posts:
            seen.add((post["channel"], post["message_id"]))
        collected.extend(new_posts)

        numeric = [p["message_id"] for p in posts if isinstance(p["message_id"], int)]
        if not numeric:
            break
        lowest = min(numeric)
        if cursor is not None and lowest >= cursor:
            break
        cursor = lowest

        print(f"  page {page_index + 1}: {len(posts)} posts, lowest id {lowest}", file=sys.stderr)
        if page_index < pages - 1:
            time.sleep(delay)

    collected.sort(key=lambda p: (p["message_id"] if isinstance(p["message_id"], int) else 0), reverse=True)
    if limit is not None and limit > 0:
        collected = collected[:limit]
    return collected


def download_images(
    posts: list[dict],
    channel: str,
    media_root: Path,
    per_post: int,
    total_cap: int,
    timeout: int,
    delay: float,
) -> tuple[int, int, int]:
    """Download photo images for the given posts. Returns (files, bytes, skipped)."""
    files = 0
    total_bytes = 0
    skipped = 0

    for post in posts:
        if files >= total_cap:
            break
        urls = post.get("photos", [])[:per_post]
        if not urls:
            post["local_images"] = []
            continue

        target_dir = media_root / channel / str(post["message_id"])
        target_dir.mkdir(parents=True, exist_ok=True)
        local: list[str] = []

        for index, url in enumerate(urls, 1):
            if files >= total_cap:
                break
            existing = sorted(target_dir.glob(f"{index:02d}.*"))
            if existing and existing[0].stat().st_size > 0:
                local.append(str(existing[0]))
                files += 1
                total_bytes += existing[0].stat().st_size
                skipped += 1
                continue
            try:
                payload, content_type = fetch_bytes(url, timeout)
            except Exception as exc:  # noqa: BLE001 - report and continue
                print(f"  ! image failed {post['message_id']}#{index}: {exc}", file=sys.stderr)
                continue
            suffix = CONTENT_TYPE_EXT.get(content_type) or Path(urllib.parse.urlparse(url).path).suffix or ".jpg"
            path = target_dir / f"{index:02d}{suffix}"
            path.write_bytes(payload)
            local.append(str(path))
            files += 1
            total_bytes += len(payload)
            print(
                f"  + {post['message_id']}/{path.name}  {len(payload) / 1024:.0f} KiB  {content_type}",
                file=sys.stderr,
            )
            time.sleep(delay)

        post["local_images"] = local

    return files, total_bytes, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("channel", help="public channel username, e.g. zaihuapd or @NewlearnerChannel")
    parser.add_argument("--pages", type=int, default=1, help="how many preview pages to walk backwards (default 1)")
    parser.add_argument("--before", type=int, default=None, help="start before this message id (pagination)")
    parser.add_argument("--limit", type=int, default=None, help="keep only the newest N posts")
    parser.add_argument("--output", type=Path, default=None, help="write JSON here")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--delay", type=float, default=1.5, help="seconds between page and image requests")
    parser.add_argument("--list", action="store_true", help="print a short summary instead of full JSON")
    parser.add_argument("--download-images", action="store_true", help="also download post photos")
    parser.add_argument("--media-root", type=Path, default=DEFAULT_MEDIA_ROOT)
    parser.add_argument("--max-images-per-post", type=int, default=12)
    parser.add_argument("--image-cap", type=int, default=120, help="maximum total images per run")
    parser.add_argument("--image-delay", type=float, default=0.4, help="seconds between image downloads")
    args = parser.parse_args()

    try:
        posts = collect(args.channel, args.pages, args.before, args.timeout, args.delay, args.limit)
    except PreviewError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    channel_slug = args.channel.lstrip("@").strip("/")

    if args.download_images:
        print(f"downloading images for {len(posts)} posts...", file=sys.stderr)
        files, total_bytes, skipped = download_images(
            posts,
            channel_slug,
            args.media_root,
            args.max_images_per_post,
            args.image_cap,
            args.timeout,
            args.image_delay,
        )
        print(
            f"images: {files} files, {total_bytes / 1024 / 1024:.2f} MiB, {skipped} already present",
            file=sys.stderr,
        )

    if args.list:
        for post in posts:
            first_line = " ".join(post["text"].split())[:100]
            media = f"  [{','.join(post['media'])}]" if post["media"] else ""
            photos = f"  🖼×{len(post['photos'])}" if post["photos"] else ""
            print(f"{post['message_id']:>7}  {post['published_at'] or '-'}{media}{photos}")
            print(f"         {first_line}")
        print(f"\n{len(posts)} posts", file=sys.stderr)
        return 0

    payload = {
        "source": {"platform": "telegram", "channel": channel_slug, "mode": "public_preview"},
        "capture_method": "telegram_public_preview",
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "count": len(posts),
        "posts": posts,
    }
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
        print(args.output)
    else:
        print(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

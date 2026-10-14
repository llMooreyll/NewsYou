#!/usr/bin/env python3
"""Telegram public channels — the generic adapter.

Uses Telegram's public web preview at https://t.me/s/<channel>: no API key, no
login, no bot. Works for PUBLIC channels only. Adding a channel is one string in
DEFAULT_CHANNELS (or one line in config.toml `telegram.channels`); nothing else
changes.

First page:   latest ~20 posts
Older pages:  ?before=<lowest message id already seen>

This module also owns post parsing (``parse_posts``) and image downloading, so
``tools/telegram_preview.py`` can stay a thin command-line wrapper.
"""
from __future__ import annotations

import html as html_mod
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import base
from .base import CST, UA, FetchError

SOURCE = "telegram"

# The built-in channel list. config.toml `telegram.channels` overrides it; this
# stays the fallback so the collector works with no configuration at all.
# The nature of each channel is decided in registry.UNIT_NATURE, not here:
# this list only says what to fetch.
DEFAULT_CHANNELS = [
    "MaiTungNews",
    "tnews365",
    "zaihuapd",
    "xhqcankao",
    "GodlyNews1",
    "NewlearnerChannel",
    "DNSPODT",
    "douView",
    "Vwyxrd",
    "CNSmydream",
    "reorx_share",
    "notonlyshare",
    "bigwalnut",
    "codedump_notes",
    "hacker_news_zh",
    "gledos_microblogging",
    "wxbyg",
    "zhihu_bazaar",
]

DEFAULT_MEDIA_ROOT = base.media_root()

class PreviewError(RuntimeError):
    pass

# --------------------------------------------------------------------------
# Parsing patterns
# --------------------------------------------------------------------------

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
# A message can contain two text divs: the quoted reply (js-message_reply_text)
# and the message itself (js-message_text). Matching the generic class picks up
# the quote first, which is how the panel ended up showing replied-to content as
# the message title. The reply is now removed and the real text div is targeted.
REPLY_MARKER = re.compile(r'<a class="tgme_widget_message_reply[^"]*"', re.S)
MESSAGE_TEXT_MARKER = re.compile(r'<div class="tgme_widget_message_text[^"]*js-message_text"', re.S)
MESSAGE_TEXT_WRAPPER = re.compile(r'\s*<div class="tgme_widget_message_text\b')
REACTIONS_BLOCK = re.compile(r'<div class="tgme_widget_message_reactions.*?</div>', re.S)
REACTION = re.compile(
    r'<span class="tgme_reaction"><i class="emoji"[^>]*><b>([^<]+)</b></i>([^<]*)</span>'
)
EMOJI_ASSET = re.compile(r'^//telegram\.org/img/emoji/')

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
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
    "image/webp": ".webp", "image/gif": ".gif", "video/mp4": ".mp4",
}

# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def fetch(url: str, timeout: int = 25, retries: int = 2) -> str:
    last: Exception | None = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(
            url, headers={"User-Agent": UA, "Accept-Language": "en,zh;q=0.8"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise PreviewError(f"could not fetch {url}: {last}")

def fetch_bytes(url: str, timeout: int) -> tuple[bytes, str]:
    request = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
        "Referer": "https://t.me/",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(), response.headers.get("Content-Type", "").split(";")[0].strip().lower()

# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def strip_tags(fragment: str) -> str:
    fragment = re.sub(r'<br\s*/?>', '\n', fragment)
    fragment = re.sub(r'</p>\s*<p[^>]*>', '\n\n', fragment)
    fragment = re.sub(r'<[^>]+>', '', fragment)
    text = html_mod.unescape(fragment)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def normalize_url(url: str) -> str:
    return "https:" + url if url.startswith("//") else url

def style_urls(tags: list[str]) -> list[str]:
    urls = []
    for tag in tags:
        match = STYLE_URL.search(tag)
        if match:
            urls.append(normalize_url(match.group(1)))
    return urls

def dedupe(values) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out

def _element_at(html: str, marker: re.Pattern, tag: str = "div") -> str:
    """Return the balanced <tag> element the marker's opening tag begins.

    The markers are written to start at the opening tag itself, so the match
    offset is the element start. Taking "the last opener before the match"
    instead would land on a nested or preceding element.
    """
    match = marker.search(html)
    if not match:
        return ""
    return base.balanced_element(html, tag, match.start())

def _element_inner(element: str, tag: str = "div") -> str:
    """Strip the element's own opening and closing tags, keeping the inside."""
    if not element:
        return ""
    open_end = element.find(">")
    close_at = element.rfind(f"</{tag}>")
    if open_end < 0 or close_at < 0:
        return ""
    return element[open_end + 1:close_at]

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

def parse_posts(page_html: str) -> list[dict]:
    """Parse any t.me/s/<channel> page into structured posts (channel-agnostic)."""
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

        # Drop the quoted reply before anything else reads the block, so its text,
        # photos and link previews are not attributed to this message.
        reply_element = _element_at(text_block, REPLY_MARKER, tag="a")
        reply_text = strip_tags(_element_inner(reply_element, tag="a"))
        if reply_element:
            text_block = text_block.replace(reply_element, "")

        text_element = _element_at(text_block, MESSAGE_TEXT_MARKER)
        if not text_element:
            # fall back to the old regex for pages with an unexpected structure
            fallback = TEXT_DIV.search(text_block)
            text_element = f"<div>{fallback.group(1)}</div>" if fallback else ""
        inner_html = _element_inner(text_element)
        # the fallback path (and some edited posts) can hand back the text div
        # itself; text_html is defined as the *inside* of that div
        while MESSAGE_TEXT_WRAPPER.match(inner_html):
            unwrapped = _element_inner(base.balanced_element(inner_html, "div", inner_html.find("<div")))
            if unwrapped == inner_html:
                break
            inner_html = unwrapped
        text = strip_tags(inner_html)
        text_html = inner_html

        date_match = DATE.search(text_block)
        views_match = VIEWS.search(text_block)
        author_match = AUTHOR.search(text_block)

        photos = dedupe(style_urls(PHOTO_TAG.findall(text_block)))
        video_thumbs = dedupe(style_urls(VIDEO_THUMB_TAG.findall(text_block)))
        stickers = dedupe(url for url in style_urls(STICKER_TAG.findall(text_block))
                          if not EMOJI_ASSET.match(url))
        videos = dedupe([normalize_url(v) for v in VIDEO_SRC.findall(text_block)])
        documents = [d.strip() for d in DOC_TITLE.findall(text_block)]
        link_preview_images = [
            normalize_url(u)
            for tag in LINK_PREVIEW_TAG.findall(text_block)
            for u in IMG_SRC.findall(tag)
            if not EMOJI_ASSET.match(normalize_url(u))
        ]

        media = sorted(name for name, marker in MEDIA_MARKERS.items() if marker in text_block)

        posts.append({
            "platform": "telegram",
            "channel": channel,
            "message_id": int(message_id) if message_id.isdigit() else message_id,
            "permalink": f"https://t.me/{post_ref}",
            "published_at": date_match.group(1) if date_match else None,
            "author": strip_tags(author_match.group(1)) if author_match else None,
            "text": text,
            "text_html": text_html,
            "reply_text": reply_text,
            "views_text": views_match.group(1) if views_match else None,
            "reactions": reactions,
            "media": media,
            "photos": photos,
            "video_src": videos,
            "video_thumbs": dedupe(video_thumbs + [u for u in link_preview_images if u not in video_thumbs]),
            "stickers": stickers,
            "document_titles": documents,
            "capture_method": "telegram_public_preview",
        })
    return posts

def walk_channel(channel: str, pages: int, before: int | None,
                 timeout: int = 25, delay: float = 0.8) -> list[dict]:
    """Walk backwards through a channel's public preview, newest first."""
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
                    "It may be private, renamed, or hidden from t.me/s.")
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
        if page_index < pages - 1:
            time.sleep(delay)

    collected.sort(key=lambda p: (p["message_id"] if isinstance(p["message_id"], int) else 0),
                   reverse=True)
    return collected

# --------------------------------------------------------------------------
# Images
# --------------------------------------------------------------------------

def download_images(posts: list[dict], channel: str, media_root: Path,
                    per_post: int = 2, total_cap: int = 200, timeout: int = 25,
                    delay: float = 0.3) -> tuple[int, int, int]:
    """Download photo images for the given posts. Returns (files, bytes, skipped)."""
    files = total_bytes = skipped = 0
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
                local.append(base.project_relative(existing[0]))
                files += 1
                total_bytes += existing[0].stat().st_size
                skipped += 1
                continue
            try:
                payload, content_type = fetch_bytes(url, timeout)
            except Exception as exc:  # noqa: BLE001
                print(f"  ! image failed {post['message_id']}#{index}: {exc}", file=sys.stderr)
                continue
            suffix = (CONTENT_TYPE_EXT.get(content_type)
                      or Path(urllib.parse.urlparse(url).path).suffix or ".jpg")
            path = target_dir / f"{index:02d}{suffix}"
            path.write_bytes(payload)
            local.append(base.project_relative(path))
            files += 1
            total_bytes += len(payload)
            time.sleep(delay)

        post["local_images"] = local
    return files, total_bytes, skipped

# --------------------------------------------------------------------------
# Collector entry point
# --------------------------------------------------------------------------

def resolve_day(day: str, reference=None) -> str | None:
    """'today' | 'yesterday' | 'auto' | 'YYYY-MM-DD' -> YYYY-MM-DD (CST)."""
    from datetime import datetime, timedelta
    reference = reference or datetime.now(CST)
    if day in ("today", "auto"):
        return None if day == "auto" else reference.date().isoformat()
    if day == "yesterday":
        return (reference.date() - timedelta(days=1)).isoformat()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day or ""):
        return day
    return None

# Per-channel outcome of the last collect() call. collect.py stores it in the
# source status so a failing or empty channel is visible, not just a log line.
_LAST_REPORT: dict = {}

def last_report() -> dict:
    return dict(_LAST_REPORT)

def pick_day(day_counts_by_channel: dict[str, dict[str, int]]) -> str | None:
    """'auto': the newest day any channel has posts for, shared by all channels.

    Choosing per channel let a channel that was quiet today contribute
    yesterday's posts under today's panel, and let channels disagree on the day.
    """
    days = {d for counts in day_counts_by_channel.values() for d in counts}
    return max(days) if days else None

def collect(limit: int = 400, day: str = "auto", pages: int = 4,
            channels: list[str] | None = None, **_):
    channels = channels or DEFAULT_CHANNELS
    target = resolve_day(day)
    walked: dict[str, list[dict]] = {}
    report: dict[str, dict] = {}

    for channel in channels:
        try:
            walked[channel] = walk_channel(channel, pages, None)
        except PreviewError as exc:
            print(f"  ! telegram {channel} failed: {exc}", file=sys.stderr)
            report[channel] = {"ok": False, "error": str(exc), "fetched": 0, "kept": 0}

    day_counts: dict[str, dict[str, int]] = {}
    for channel, posts in walked.items():
        counts: dict[str, int] = {}
        for post in posts:
            dt = base.to_cst(post.get("published_at"))
            if dt:
                counts[dt.date().isoformat()] = counts.get(dt.date().isoformat(), 0) + 1
        day_counts[channel] = counts
    chosen = target or pick_day(day_counts)

    collected: list[dict] = []
    for channel, posts in walked.items():
        kept = 0
        for post in posts:
            dt = base.to_cst(post.get("published_at"))
            if not dt or not chosen or dt.date().isoformat() != chosen:
                continue
            kept += 1
            text = post.get("text") or ""
            collected.append(base.item(
                SOURCE, f"Telegram · @{channel}",
                (text.splitlines() or [""])[0][:120] or f"#{post['message_id']}",
                post.get("permalink") or f"https://t.me/{channel}/{post['message_id']}",
                kind="message", published_at=dt.isoformat(), date_source="post",
                summary=text,
                extra={
                    "collector": "public_preview",
                    "channel": channel,
                    "message_id": post.get("message_id"),
                    "views": post.get("views_text"),
                    "media": post.get("media") or [],
                    "photos": post.get("photos") or [],
                    "reactions": post.get("reactions") or [],
                    "text": text,
                    "text_html": post.get("text_html") or "",
                    "reply_text": post.get("reply_text") or "",
                    "day": chosen,
                },
            ))
        newest = max(day_counts.get(channel) or {"": 0})
        report[channel] = {"ok": True, "fetched": len(posts), "kept": kept,
                           "newest_day": newest or None}
        if kept == 0:
            print(f"  telegram {channel}: no posts on {chosen} (newest {newest or 'none'})",
                  file=sys.stderr)

    _LAST_REPORT.clear()
    _LAST_REPORT.update({"day": chosen, "mode": "fixed" if target else "auto",
                         "channels": {c: report[c] for c in channels if c in report}})
    collected.sort(key=lambda x: str(x.get("published_at") or ""), reverse=True)
    if not target and chosen:
        print(f"  telegram: auto-selected day {chosen} for all channels", file=sys.stderr)
    return collected[:limit]

def download_for_items(items: list[dict], media_root: Path, per_post: int = 2,
                       cap: int = 200) -> int:
    """Download images for already-collected items, grouped by channel."""
    by_channel: dict[str, list[dict]] = {}
    for entry in items:
        by_channel.setdefault(entry["extra"]["channel"], []).append(entry)

    total = 0
    for channel, group in by_channel.items():
        if total >= cap:
            break
        shim = [{"message_id": e["extra"].get("message_id"), "photos": e["extra"].get("photos") or []}
                for e in group]
        try:
            files, _bytes, _skipped = download_images(
                shim, channel, media_root, per_post, cap - total)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! image download failed for {channel}: {exc}", file=sys.stderr)
            continue
        total += files
        for entry, shim_entry in zip(group, shim):
            entry["extra"]["local_images"] = shim_entry.get("local_images") or []
    return total

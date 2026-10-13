#!/usr/bin/env python3
"""Collect public Telegram channel posts with no API key, no login, no bot.

Thin command-line wrapper around tools/collectors/telegram.py, which holds the
implementation and is what collect.py uses. Kept as a standalone entry point
because it is handy for poking at a single channel.

Uses Telegram's public web preview at https://t.me/s/<channel>, the same
mechanism RSSHub falls back to. PUBLIC channels only. Standard library only.

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
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from collectors import telegram as tg                      # noqa: E402
from collectors.telegram import PreviewError               # noqa: E402

DEFAULT_MEDIA_ROOT = tg.DEFAULT_MEDIA_ROOT

def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
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
        posts = tg.walk_channel(args.channel, args.pages, args.before,
                                timeout=args.timeout, delay=args.delay)
    except PreviewError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.limit is not None and args.limit > 0:
        posts = posts[:args.limit]

    channel_slug = args.channel.lstrip("@").strip("/")

    if args.download_images:
        print(f"downloading images for {len(posts)} posts...", file=sys.stderr)
        files, total_bytes, skipped = tg.download_images(
            posts, channel_slug, args.media_root, args.max_images_per_post,
            args.image_cap, args.timeout, args.image_delay)
        print(f"images: {files} files, {total_bytes / 1024 / 1024:.2f} MiB, "
              f"{skipped} already present", file=sys.stderr)

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

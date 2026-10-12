# Telegram without an `api_id`

If `my.telegram.org` won't give you credentials, you are **not blocked**. Four of the five practical methods below need no `api_id` at all.

---

## First: diagnose the `my.telegram.org` failure (2 minutes, worth doing)

Common causes, in order of frequency:

| Symptom | Likely cause | What to try |
|---|---|---|
| "Create New Application" returns a generic error | Network/IP reputation, or a form-validation quirk | Change network (home ISP ↔ mobile data), try a different browser or a private window, retry after a few hours — reports say this error is often server-side and transient |
| Page won't load at all | Regional/IP blocking | Try a different network first; if you use a VPN, try **without** it and then **with** it — community reports conflict on which direction helps |
| Login code never arrives | Code is sent to your **other logged-in Telegram apps**, not always by SMS | Check the Telegram app on your phone/desktop for the login message |
| "You already have an api_id" | One `api_id` per phone number | Log in and view your existing app at `my.telegram.org/apps` — do not create a second one |

Do **not** work around this by reusing an `api_id` you found in someone else's repository. Telegram's own sample `api_id` and any leaked/published one will produce `API_ID_PUBLISHED_FLOOD`, and using them can get your account restricted.

---

## The five methods, compared

| Method | Needs `api_id`? | Needs login? | History | Future posts | Public channels | Private channels | Verified here |
|---|---|---|---|---|---|---|---|
| **1. Public web preview** `t.me/s/<channel>` | No | No | Paged backwards, ~20/page | Yes, by polling | Yes | No | ✅ tested |
| **2. Desktop export** (JSON) | No | App login only | Full, one-time | No | Caveat ⚠️ | Yes | ✅ schema confirmed |
| **3. Bot API** (`@BotFather`) | No | No | No | Yes, live | Only if bot is admin | Only if bot is admin | ✅ docs confirmed |
| **4. Your browser session** (CDP) | No | Your own session | Via scrolling | Yes | Yes | Yes | ⚠️ design only |
| **5. RSSHub public route** | No | No | ~20 posts | Yes | Yes | No | ✅ same mechanism as 1 |
| *(later)* Telethon / MTProto | **Yes** | Yes | Full | Yes | Yes | Yes | ⚠️ blocked on `api_id` |

---

## Method 1 — Public web preview (recommended default)

This is the best option for your case: public channels, no credentials, no account risk.

### Verified behaviour (tested today from this machine)

```text
GET https://t.me/s/telegram
  -> 200, 127,499 bytes
  -> 20 posts, each exposed as data-post="telegram/<id>"
  -> ids 441 ... 460

GET https://t.me/s/telegram?before=441
  -> next page: ids 417 ... 445
```

So pagination works with `?before=<lowest id already seen>`, and each page yields **20 posts**.

### Adapter included in this repo

`tools/telegram_preview.py` — standard library only, no installs:

```bash
# latest 20 posts, human-readable
python tools/telegram_preview.py telegram --list

# walk backwards 3 pages (~60 posts), save JSON
python tools/telegram_preview.py telegram --pages 3 \
  --output data/telegram/telegram-news.json

# continue from where you stopped
python tools/telegram_preview.py telegram --before 400 --output data/telegram/older.json
```

Verified output: 60 posts across 3 pages (ids 400–460), no duplicates, with per-post:

```json
{
  "message_id": 400,
  "permalink": "https://t.me/telegram/400",
  "published_at": "2025-08-18T13:34:04+00:00",
  "views_text": "2.73M",
  "media": ["video"],
  "text": "Profile Ratings. Profiles now show a numerical rating ..."
}
```

Fields extracted per post: `channel`, `message_id`, `permalink`, `published_at`, `author`, `text`, `views_text`, `media[]` (photo/video/document/voice/poll/sticker/link preview/forward/reply/pinned), `images[]`, `video_src[]`, `document_titles[]`.

### Backfill and incremental sync

```text
BACKFILL
  page 1  -> ids 441..460   store all
  page 2  -> ?before=441    -> 420..445  (keep only new)
  page 3  -> ?before=420    -> 400..420  (keep only new)
  stop when: oldest id <= your cutoff date, or max pages reached

INCREMENTAL (every 30-60 min)
  GET /s/<channel>          -> 20 latest
  keep posts whose (channel, message_id) is not already stored
  also re-check recent posts for edits
```

Identity key: `(channel_username, message_id)`. Permalink: `https://t.me/<channel>/<id>`. Both are stable, so dedupe is trivial and re-runs are idempotent.

### Limitations

- **Public channels only.** Private channels return nothing.
- **Telegram deliberately hides some channels** from the preview — RSSHub's docs call out pornography, copyright, and political channels. If `t.me/s/<name>` shows nothing in a normal browser, the adapter can't help; treat that as a hard boundary, not a bug to work around.
- **Recent window.** Deep history means many pages. 1,000 posts ≈ 50 requests. Rate-limit yourself to a few pages per minute.
- No media binaries, only URLs and metadata.

---

## Method 2 — Telegram Desktop export (no API, best for one-time history)

`Settings → Advanced → Export Telegram data` → choose **JSON**.

Official schema: <https://core.telegram.org/import-export>

### Layout

```text
export_folder/
├── result.json            # everything, including all chats and messages
├── chats/
│   └── chat_01/           # photos, files, voice_messages, video_files, stickers…
├── lists/other_data.json
├── stories/
└── profile_pictures/
```

If you export a single chat, `result.json` is a single Chat object instead of the full Result object.

### Message fields you get

`id`, `type`, `date`, `date_unixtime`, `from`, `from_id`, `edited`, `reply_to_message_id`, `text`, `text_entities`, `photo`, `file`, `thumbnail`, `media_type`, `mime_type`, `author`, `forwarded_from`, `saved_from`, `via_bot`, `poll`, `location_information`, and service-message fields such as `action`, `actor`, `members`.

Chat objects carry `id`, `name`, `type` (`public_channel`, `private_channel`, `public_supergroup`, `private_supergroup`, `personal_chat`, `bot_chat`, `saved_messages`, `replies`) and `messages[]`.

### ⚠️ The important caveat

Telegram's own schema documentation states:

> `messages` — Array of Message. Array of messages in the chat. **Note that public group and channel exports will only contain messages sent by the user requesting the export.**

Read that carefully: for **public** channels, the export may contain only *your own* messages, not the channel's posts. Private channels and groups are the value case here, because that content isn't available any other way.

Practical rule:

- Export is excellent for **private channels, groups, and your own messages**.
- For **public channels**, verify with a test export before relying on it; otherwise use Method 1.

### Parsing

`result.json` is plain JSON, so no dependency is required. For a typed parser:

- `StackTheFennec/telegram-export-parser` (TypeScript)
- any JSON reader plus the mapping in this project's `docs/CONTENT_MODEL.md`

Suggested mapping into our content model: chat `name`/`id` → source, message `id` → `external_id`, `date` → `published_at`, `from`/`author` → author, `text_entities` → formatted body, `photo`/`file` → local media path.

---

## Method 3 — Bot API via `@BotFather` (no `my.telegram.org`)

A bot token comes from `@BotFather` **inside Telegram**. No `api_id`, no `api_hash`, no developer portal.

```text
1. Message @BotFather -> /newbot -> get token
2. Add the bot to the channel as an ADMINISTRATOR
3. GET https://api.telegram.org/bot<token>/getUpdates
     ?allowed_updates=["channel_post","edited_channel_post"]
```

Why this may still not fit your goal:

- **Bots cannot read channel messages without admin rights.** This is a hard Bot API rule, not a configuration issue.
- **No history.** `getUpdates` returns only updates from the last 24 hours; a bot cannot walk backwards through a channel.
- Therefore a bot only works for channels **you own or administer**.

Where it is genuinely useful: your own channel, or a channel whose owner will grant your bot admin. Then you get real-time `channel_post` pushes with no scraping at all.

---

## Method 4 — Your own browser session via CDP (covers private channels)

You already have an authenticated browser. This project already has the tooling:

```bash
python tools/cdp_capture.py "https://web.telegram.org/a/#-1001234567890" \
  --output data/browser-captures/tg-channel.json --wait 8
```

Why this works when Method 1 doesn't: it uses **your** session, so it reaches private channels too.

Practical notes:

- Web Telegram renders lazily; you must scroll up to load older messages, so a real adapter needs to drive scrolling, not just load once.
- A better variant than scraping the DOM is reading the client's local storage/IndexedDB cache for the channel, which is more structured and survives re-renders.
- It requires the browser to be running and logged in — good for a "when I'm at my desk" collection job, not a 24/7 crawler.
- This remains your own session, your own subscriptions, personal use. Do not use it to bulk-join or mass-archive channels you have no relationship with.

---

## Method 5 — RSSHub public route (same mechanism as Method 1)

```text
https://rsshub.app/telegram/channel/<username>
```

RSSHub's Telegram route uses `t.me/s/<username>` when no session is configured — it is Method 1 with a maintained parser and all the display option flags (`showMediaTagInTitle`, `includeFwd`, `includeReply`, `searchQuery`, …).

Use it if you want a feed URL with zero code. Use `tools/telegram_preview.py` if you want control over pagination, storage, and dedupe.

To upgrade to MTProto later you'd need `TELEGRAM_SESSION`, which requires an `api_id` — so that upgrade is blocked for now by the same problem.

---

## Downloading images

Yes — the public preview exposes real photo URLs, so images can be fetched without any credentials.

### The distinction that matters

A naive parse of `t.me/s/<channel>` yields a lot of image URLs, but almost none are photos:

| URL pattern | What it is | Keep? |
|---|---|---|
| `https://cdn*.telesco.pe/file/<hash>` inside `tgme_widget_message_photo_wrap` | the actual post photo, 800px wide | ✅ yes |
| `//telegram.org/img/emoji/40/<hex>.png` | reaction emoji sprite | ❌ no |
| `tgme_widget_message_user_photo` | channel/author avatar | ❌ no |
| `tgme_widget_message_video_thumb` | video poster frame | ✅ as thumbnail |
| `<video src=...>` | video file reference | ✅ metadata only |
| `tgme_widget_message_link_preview` images | link preview thumbnail | ✅ separate |

Measured on the 10 newest `@zaihuapd` posts: the naive count was **100+ image URLs**, of which **6 were real photos**. `tools/telegram_preview.py` now separates these into `photos`, `video_thumbs`, `stickers`, and `reactions`, so reaction sprites no longer pollute the data.

### Usage

```bash
python tools/telegram_preview.py zaihuapd --limit 10 --download-images \
  --output data/telegram/zaihuapd-latest10.json
```

Relevant flags:

| Flag | Default | Purpose |
|---|---|---|
| `--download-images` | off | enable photo downloads |
| `--media-root` | `data/telegram/media` | storage root |
| `--max-images-per-post` | 12 | per-post cap (albums) |
| `--image-cap` | 120 | total cap per run |
| `--image-delay` | 0.4 | seconds between downloads |

### Layout and behaviour

```text
data/telegram/media/<channel>/<message_id>/01.jpg
                                         02.jpg
```

- Files are numbered in album order, so the sequence is preserved.
- Content type decides the extension (`.jpg`, `.png`, `.webp`, `.gif`, `.mp4`).
- Re-runs **skip existing files**, so a download is resumable and idempotent.
- Failures are logged per image and do not abort the run.
- Each post in the JSON gains a `local_images` array of local paths.
- The HTML renderer prefers local files and falls back to remote URLs when images were not downloaded.

### Measured result

```text
6 files, 0.44 MiB, 6/6 resolved in the rendered digest
JPEG 800x533  800x533  800x267  800x800  800x400  800x407
```

### Policy — read before scaling this up

Images are **not** the same as text. Storing them creates different obligations:

1. **Copyright belongs to the original publisher or photographer.** Channel posts frequently repost third-party images (this channel cites 9To5Mac, 香港 01, The Verge, and others). A personal digest should link to the original source, not become the source.
2. **Prefer links over copies.** The default in this project stays `metadata_only`; downloading is opt-in per run.
3. **Keep volume bounded.** Caps exist so a 10,000-post backfill cannot become a 100,000-image scrape. Only download for posts that survive editorial review.
4. **Set retention.** Apply the same `retention_days` policy to media as to raw captures, and delete on that schedule.
5. **Never redistribute.** If you ever publish the newsletter, publish the link and at most a thumbnail, and attribute the original.
6. **Do not archive personal or sensitive imagery.** Recipe: if the post is about private individuals, skip the download entirely.

Recommended pipeline position:

```text
collect metadata (always)
   -> rank / dedupe / review
   -> download images ONLY for accepted items
```

Not the other way round.

---

## What NOT to do

| Don't | Why |
|---|---|
| Reuse a "public" or sample `api_id` from GitHub | `API_ID_PUBLISHED_FLOOD`; can get your account restricted |
| Buy, share, or import someone's `tdata` / `.session` | That is account takeover territory; also frequently fails or is malware |
| Hand your session to a hosted scraping service | You are giving away full access to your Telegram account |
| Try to force `t.me/s` to reveal channels Telegram hides | It is a deliberate access decision; respect it |
| Mass-join channels to "collect them" | Telegram explicitly prohibits this and watches unofficial API usage |

---

## Recommended combination for you

```text
public channels  -> history   : tools/telegram_preview.py --pages N     (no creds)
public channels  -> new posts : tools/telegram_preview.py, every 30-60 min (no creds)
private channels -> history   : Telegram Desktop export to JSON          (no creds)
private channels -> new posts : browser session via CDP, or a bot if you can admin
channels you own -> any       : Bot API channel_post                     (no creds)
if api_id ever works          : add Telethon mode for full depth         (upgrade, not requirement)
```

This covers most of your goal today, with no `api_id` and no account risk. The only real gap is *private channels' future posts*, which the browser session can cover when your browser is open.

---

## Adapter design for this project

Give the Telegram adapter two modes, mirroring RSSHub's own design:

```yaml
- id: telegram-public-preview
  platform: telegram
  mode: preview                 # no credentials
  locator: channel:NewlearnerChannel
  interval_minutes: 45
  max_items_per_run: 20

- id: telegram-selected-channels
  platform: telegram
  mode: mtproto_user            # upgrade path, requires api_id
  locator: selected_channels
  credentials_ref: env:TELEGRAM_API_ID_AND_HASH
  enabled: false
```

```python
class TelegramAdapter(Protocol):
    def sync(self) -> list[dict]: ...

class PreviewAdapter(TelegramAdapter):     # today
    """t.me/s/<channel>; public channels; ~20 posts/page; ?before=<id> pagination"""

class MtprotoAdapter(TelegramAdapter):     # later, if api_id becomes available
    """Telethon; iter_messages / min_id cursor; private channels; full depth"""
```

Both produce the same normalized record shape, so nothing downstream changes when you upgrade:

```text
identity:   (channel, message_id)
link:       https://t.me/<channel>/<message_id>
time:       published_at, edited_at
body:       text, entities
media:      media types, image/video/document URLs or local paths
provenance: capture_method = telegram_public_preview | telegram_export | telegram_bot_api | telegram_browser
```

---

## What was verified in this session

| Claim | How it was verified |
|---|---|
| `t.me/s/<channel>` needs no credentials and returns 20 posts | Live HTTP fetch: 127,499 bytes, 20 `data-post` values |
| Pagination works | `?before=441` returned the older block 417–445 |
| A parser can be written with the standard library only | `tools/telegram_preview.py` produced 60 posts over 3 pages, ids 400–460, no duplicates |
| Desktop export schema and its public-channel caveat | Official schema read at <https://core.telegram.org/import-export> |
| Bots need channel admin rights and cannot read history | Bot API docs plus multiple independent community confirmations |
| Telegram monitors unofficial clients; abusive use risks bans | Official docs at <https://core.telegram.org/api/obtaining_api_id> |

---

## Sources reviewed

- Telegram public preview (live-tested): <https://t.me/s/telegram>
- Telegram data export schema: <https://core.telegram.org/import-export>
- Chat export tool announcement: <https://telegram.org/blog/export-and-more>
- Creating a Telegram application / `api_id` caveats: <https://core.telegram.org/api/obtaining_api_id>
- Telegram Bot API: <https://core.telegram.org/bots/api>
- Telegram API Terms: <https://core.telegram.org/api/terms>
- `my.telegram.org` creation-error reports: <https://stackoverflow.com/questions/68965496> and <https://github.com/tdlib/telegram-bot-api/issues/573>
- Bot admin requirement: <https://stackoverflow.com/questions/45002999>
- RSSHub Telegram route implementation: `lib/routes/telegram/channel.ts`, `lib/routes/telegram/tglib/client.ts`

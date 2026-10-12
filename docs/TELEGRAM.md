# Telegram collection research

## Executive recommendation

Because you already subscribe to many high-quality Telegram channels, the correct integration is usually a **user-authorized Telegram client connection**, not a bot.

```text
your Telegram account (already subscribed channels)
          |
          | MTProto / TDLib / Telethon
          v
channel discovery + incremental message sync
          |
          v
raw message JSON + media metadata + provenance
          |
          v
normalized newsletter items
          |
          v
deduplication, topic labels, review, publication
```

A Bot API integration is useful for channels you own or administer, but it is generally not the right tool for reading the complete history of channels that your personal account follows.

## Three access methods

### Method A — Telegram Bot API

Official interface:

- <https://core.telegram.org/bots/api>

The Bot API is an HTTPS API using a bot token. It can receive updates such as `channel_post` and `edited_channel_post`, but the bot must be able to receive those updates in the relevant chat. It is primarily appropriate when:

- you own the channel;
- you can add the bot to the channel with the required permissions;
- you want a live stream of new posts;
- you do not need to read the user's existing subscriptions or complete historical archives.

The Bot API is **not** the recommended method for aggregating arbitrary channels followed by your personal account.

### Method B — User account through MTProto

Official documentation:

- <https://core.telegram.org/api>
- <https://core.telegram.org/api/obtaining_api_id>
- <https://core.telegram.org/api/terms>

This connects as a user account, with an authorization flow involving your phone number, login code, and possibly two-factor authentication. It can access the chats and channels that this account is allowed to access, including channels you have joined.

You must create an `api_id` and `api_hash` through Telegram's official API development tools at <https://my.telegram.org>. These values are credentials and must not be committed to this repository.

A practical Python implementation could use:

- [Telethon](https://docs.telethon.dev/), which exposes a high-level Python client;
- [Pyrogram](https://docs.pyrogram.org/), another Python MTProto framework;
- [TDLib](https://core.telegram.org/tdlib/docs/), Telegram's official cross-platform client library.

For this project, the first practical implementation would likely be Telethon because it makes channel discovery, history iteration, and event handling relatively straightforward. TDLib is the more official and comprehensive long-term client library, but it introduces more native-library setup.

### Method C — Telegram Desktop export

Telegram Desktop supports chat export:

- <https://telegram.org/blog/export-and-more>

This is useful for a one-time historical import without building an API client. It is not a good recurring collector because it is manual and does not provide continuous incremental synchronization.

## How to gather your subscribed channels

The user-account collector should work in two phases.

### Phase 1 — discover channels

After authorization, read the account's available chat list and classify entries:

- channels;
- groups/supergroups;
- private chats;
- folders/archive entries.

Create a local source record for each channel you explicitly select. Do not automatically collect every chat by default.

```yaml
id: telegram-channel-example
platform: telegram
mode: mtproto_user
locator: '@example_channel'
language: [en]
topics: [technology]
include_history: true
media_policy: metadata_only
retention_days: 90
enabled: true
```

### Phase 2 — incremental synchronization

For each selected channel, store a cursor such as:

```text
telegram_chat_id
last_message_id
last_message_date
last_sync_at
```

Initial sync:

1. resolve the channel username or selected chat ID;
2. fetch the latest N messages;
3. optionally walk backward in bounded pages for historical messages;
4. save the message ID, date, text, entities, media metadata, reply/forward relationships, and permalink where available;
5. update the cursor only after the page is durably stored.

Recurring sync:

1. fetch messages newer than `last_message_id`;
2. process edited messages as new observations of the same message identity;
3. mark deleted messages instead of silently removing local evidence;
4. save the new cursor;
5. apply a per-channel request budget and backoff.

TDLib's official documentation describes `getChatHistory`: messages are returned in reverse chronological order, and the application continues using the last received message ID for pagination. TDLib also exposes new-message updates for live synchronization.

## Message fields to preserve

For each Telegram message, preserve:

- `chat_id` and channel username/title;
- `message_id`;
- message URL/permalink when available;
- publication date and edit date;
- original text and formatted entities;
- captions for media;
- media type and remote/file identifiers;
- album/grouped-media ID;
- reply-to message ID;
- forwarded-from information where exposed;
- link previews and outbound URLs;
- view/forward/reaction counts only when available and needed;
- capture method and capture time;
- local content hash;
- retention and deletion status.

Do not flatten a Telegram post into plain text only. A high-quality channel post may contain an image caption, an attached document, a video, a link preview, and a reply chain.

## Recommended Telegram adapter interface

```python
class TelegramCollector:
    async def authorize(self) -> None:
        """Interactive first login; reuse a local protected session later."""

    async def list_selected_chats(self) -> list[dict]:
        """Return channels explicitly selected by the user."""

    async def backfill(self, chat_id: int, limit: int) -> list[dict]:
        """Fetch a bounded historical page."""

    async def sync_new(self, chat_id: int, after_message_id: int) -> list[dict]:
        """Fetch newer messages and edits."""

    async def download_permitted_media(self, message_id: int) -> Path | None:
        """Optional, explicitly enabled media step."""
```

The first implementation should only collect message text, links, timestamps, and media metadata. Media downloads and transcription should be opt-in per channel.

## Telegram-specific constraints

### Private channels

A user account can only access a private channel if that account is already a member or has otherwise been granted access. The collector must never attempt to join channels automatically without explicit user action.

### Bot versus user account

A bot token cannot simply impersonate the user's subscriptions. If the goal is “collect the channels I already follow,” MTProto/TDLib with the user's authorized account is the appropriate model.

### Login/session security

The session file is highly sensitive. It can grant access to the Telegram account. Therefore:

- keep it outside Git;
- keep it outside the public showcase;
- store it in a user-selected local directory;
- use file permissions appropriate to the host;
- do not copy it into raw captures;
- provide a logout/delete-session command;
- never ask the user to paste a login code into a newsletter configuration file.

### Rate limits and abuse monitoring

Telegram's official API documentation warns that unofficial API clients and abusive use are monitored. Avoid flooding, mass joining, unsolicited messaging, fake counters, or aggressive historical crawling. Use bounded backfills, low polling frequency, and explicit source selection.

### Copyright and privacy

A personal digest should keep links and short excerpts rather than republishing complete channel archives. Channel posts may contain copyrighted text, images, video, personal information, or restricted material. Store only what is needed for personal research and citation, and define retention/deletion behavior.

### AI and Telegram terms

Telegram's current API Terms page states that API use is subject to its Content Licensing and AI Scraping terms, and includes a prohibition on using, accessing, or aggregating Telegram data to train, fine-tune, develop, enhance, or deploy AI/ML technologies. This is an important constraint for the newsletter design:

- a non-AI feed reader/archive is the safer initial interpretation;
- using an AI summarizer, classifier, embedding service, or model on Telegram content may require separate legal/terms review or permission;
- do not send Telegram content to a third-party AI API by default;
- retain the original link and use short, attributed excerpts;
- treat this as a product/legal decision requiring review, not as a technical detail.

This document is not legal advice.

## Proposed Telegram newsletter workflow

### Safe first version

```text
selected Telegram channels
  -> text/link metadata only
  -> local SQLite/PostgreSQL records
  -> keyword/topic filters without an AI model
  -> dedupe by chat_id + message_id/content hash
  -> human review
  -> Markdown newsletter with links and short excerpts
```

### Later, only after terms review

```text
permitted message content
  -> local processing under an approved policy
  -> topic classification / translation / summary
  -> evidence-linked newsletter item
```

The later workflow should be configurable so Telegram sources can be excluded from AI processing while still appearing in a non-AI collection inbox.

## Recommended implementation order for Telegram

1. Add `telegram` to the source configuration and content model.
2. Add a manual channel allowlist.
3. Add an optional user-account authorization flow.
4. Store the session outside the repository.
5. List available chats and let the user select channels.
6. Backfill only the latest 20–100 messages per selected channel.
7. Add incremental sync by message ID.
8. Preserve original text, entities, links, and media metadata.
9. Add a local Markdown exporter with citations.
10. Add media and AI processing only after terms and retention decisions are explicit.

## Telethon implementation guide

Nothing in this section is installed. It is the implementation contract for the Telegram adapter.

### Step 0 — Get your own API credentials

1. Log in at <https://my.telegram.org> with the phone number of the account that already follows your channels.
2. Open **API development tools** and create an application.
3. Save `api_id` and `api_hash`.

One `api_id` per phone number. These are credentials: keep them out of Git.

Store them as environment variables or in a file outside this repository:

```text
TELEGRAM_API_ID=1234567
TELEGRAM_API_HASH=0123456789abcdef0123456789abcdef
TELEGRAM_SESSION_PATH=~/.newsletter-telegram/session
```

### Step 1 — Install (only when you decide to implement)

```bash
python -m pip install telethon
```

This project does not install it for you.

### Step 2 — First login creates a session

```python
import asyncio, os
from telethon import TelegramClient

API_ID = int(os.environ["TELEGRAM_API_ID"])
API_HASH = os.environ["TELEGRAM_API_HASH"]
SESSION = os.path.expanduser("~/.newsletter-telegram/session")

async def main():
    async with TelegramClient(SESSION, API_ID, API_HASH) as client:
        me = await client.get_me()
        print("signed in as", me.username or me.id)

asyncio.run(main())
```

The first run asks for the phone number, the login code Telegram sends you, and your two-factor password if enabled. Afterwards the `.session` file is reused, so no code is needed again.

Security rules for that file:

- it grants access to your Telegram account — treat it like a password;
- never commit it, never copy it into raw captures, never put it in the showcase;
- keep it in a directory only your user can read (`chmod 700` on the directory);
- provide a logout path that deletes it.

If you must store the session as a string instead of a file (for example in a secret manager), Telethon supports it:

```python
from telethon.sessions import StringSession

with TelegramClient(StringSession(), API_ID, API_HASH) as client:
    print(client.session.save())   # store this in a secret manager, not in Git
```

### Step 3 — List the channels you already follow

```python
from telethon.tl.types import Channel

async with TelegramClient(SESSION, API_ID, API_HASH) as client:
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if isinstance(entity, Channel) and entity.broadcast:
            print(dialog.id, dialog.name, getattr(entity, "username", None))
```

`entity.broadcast` distinguishes real channels from supergroups. This is where you build your allowlist — deliberately, by hand.

Resolve a channel later by username or id:

```python
entity = await client.get_entity("@example_channel")   # or the numeric id
```

### Step 4 — Backfill history (bounded)

```python
async for message in client.iter_messages(entity, limit=100):
    record = {
        "chat_id": message.chat_id,
        "message_id": message.id,
        "date": message.date.isoformat(),
        "edit_date": message.edit_date.isoformat() if message.edit_date else None,
        "text": message.text,
        "grouped_id": message.grouped_id,
        "reply_to": message.reply_to_msg_id,
        "views": message.views,
        "forwards": message.forwards,
        "has_media": message.media is not None,
        "permalink": f"https://t.me/{channel_username}/{message.id}",
    }
```

For older pages, continue from the last seen message id instead of restarting:

```python
oldest = None
while True:
    batch = await client.get_messages(entity, limit=100, offset_id=oldest or 0)
    if not batch:
        break
    for message in batch:
        ...  # store
    oldest = batch[-1].id
```

Always bound both the page size and the total. Do not walk years of history in one run.

### Step 5 — Incremental sync (only new messages)

Keep a cursor per channel:

```text
channel_id | last_message_id | last_sync_at
```

Then fetch only what is newer:

```python
new_messages = await client.get_messages(entity, min_id=last_message_id, limit=100)
```

`min_id` returns messages above that id, which is exactly the "future articles" problem solved. Update the cursor only after the batch is durably written.

### Step 6 — Live capture instead of polling (optional)

```python
from telethon import events

@client.on(events.NewMessage(chats=allowed_channel_ids))
async def handler(event):
    await store(event.message)

await client.run_until_disconnected()
```

Trade-off: a long-running process is faster but must be supervised. A cron-style `min_id` poll every 30–60 minutes is simpler and safer for a personal digest, and it naturally tolerates restarts.

### Step 7 — Preserve what matters

Telethon exposes far more than text. Store at least:

| Field | Why |
|---|---|
| `message.id` | stable identity |
| `message.date`, `edit_date` | timeline and edit detection |
| `message.text`, `message.entities` | formatted text, links, mentions, hashtags, code |
| `message.media` + caption | images, documents, video, polls, voice |
| `message.grouped_id` | album grouping (one post, several files) |
| `message.reply_to_msg_id` | thread context |
| `message.forward` | provenance of reposted content |
| `message.views`, `forwards` | weak popularity signal |
| permalink `t.me/<channel>/<id>` | citation |

Do not reduce a post to plain text. A channel post can be a caption plus an album plus a link preview.

### Step 8 — Sanity checks before you scale

1. Start with **one** channel and a **20-message** backfill.
2. Verify dedupe by `(chat_id, message_id)`.
3. Verify edited messages update rather than duplicate.
4. Verify deleted messages are marked, not silently removed.
5. Add a per-channel daily request budget.
6. Only then add the rest of your allowlist, a few channels at a time.

### Rate limits and account safety

- Telegram monitors unofficial API clients. Flooding, mass joining, spam, and fake counters are explicitly prohibited and can end in a permanent ban.
- Keep backfills small, poll infrequently, and never send unsolicited messages from an account used for collection.
- A collection account with normal human usage patterns is safer than a fresh account that immediately scrapes hundreds of channels.

### Cross-check: how RSSHub does it

RSSHub's Telegram route uses a Telethon fork (`teleproto`) in `lib/routes/telegram/tglib/client.ts`. It reads configuration from `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, and `TELEGRAM_SESSION`, and falls back to the public `https://t.me/s/:username` preview when no session is configured. That is a useful pattern to copy:

```text
if session configured  -> authenticated MTProto feed (works for restricted channels and media)
else                   -> public preview feed (works only where t.me/s/ is available)
```

It also supports a SOCKS4/5 `PROXY_URI` or a dedicated MTProxy (`TELEGRAM_PROXY_HOST` / `PORT` / `SECRET`) for MTProto connections.

Generate a session with the bundled helper:

```bash
node ./lib/routes/telegram/scripts/get-telegram-session.mjs
```

### Which Telegram path should this project take?

| Goal | Path |
|---|---|
| New posts from public channels, minimal setup | RSSHub `/telegram/channel/:username` (public `t.me/s` mode) |
| Public channels plus media, one-off | RSSHub with `TELEGRAM_SESSION` configured |
| Full history + incremental sync + your own storage | **Telethon adapter (recommended primary)** |
| One-time archive of an existing chat | Telegram Desktop export |
| Channels you own or administer | Bot API `channel_post` updates |

## Sources inspected

- Telegram APIs: <https://core.telegram.org/>
- Creating a Telegram application: <https://core.telegram.org/api/obtaining_api_id>
- Telegram API Terms: <https://core.telegram.org/api/terms>
- Telegram Bot API: <https://core.telegram.org/bots/api>
- TDLib getting started: <https://core.telegram.org/tdlib/getting-started>
- TDLib documentation: <https://core.telegram.org/tdlib/docs/>
- TDLib `getChatHistory`: <https://core.telegram.org/tdlib/docs/classtd_1_1td__api_1_1get_chat_history.html>
- Telegram Desktop export: <https://telegram.org/blog/export-and-more>
- Telethon documentation: <https://docs.telethon.dev/en/stable/modules/client.html>
- Pyrogram documentation: <https://docs.pyrogram.org/index>
- TDLib source: <https://github.com/tdlib/td>

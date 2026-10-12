# Stage 2a — Telegram merge & dedupe

You are cleaning up one day of Telegram news posts collected from several
Chinese-language channels. These channels repost each other constantly, so the
same event often appears three or four times with different wording.

You do **not** edit the data. You only return decisions. A separate script
validates and applies them, so anything you leave out is simply left untouched.

## Input

A JSON object with:

```
day          the day being processed, YYYY-MM-DD (Asia/Shanghai)
item_count   number of items
instructions this document
items[]      every message collected that day
```

Each item:

| field | meaning |
|---|---|
| `id` | stable reference, e.g. `telegram:zaihuapd:44363` — always use this |
| `channel` | channel username without `@` |
| `title` | first line of the post, already truncated |
| `summary` | the full post text |
| `published_at` | ISO-8601 in `+08:00` |
| `url` | permalink |
| `content_hash` | exact-duplicate hash, already used for byte-identical dedupe |
| `views`, `media`, `reactions` | weak signals only |

## Two operations

### `drop` — same event, one version is strictly worse

Use when two or more items report **the same event** and one of them carries
**less information**: shorter text, missing numbers, missing source
attribution, pure repost of the other.

```
{"op": "drop", "id": "<id to remove>", "canonical_id": "<id kept>", "reason": "..."}
```

Rules:

- The kept item must contain everything the dropped one does, plus more.
- If both contain *some* unique information, do **not** drop — use `merge`.
- Never drop something just because another channel is more popular.

### `merge` — same event, both versions have unique facts

Use when items cover the same event but each adds something the other lacks:
one has the figure, another has the attribution, a third has the reaction.

```
{
  "op": "merge",
  "primary_id": "<the most complete item>",
  "merge_ids": ["<other id>", "<other id>"],
  "title": "neutral headline",
  "summary": "merged neutral summary",
  "topics": ["ai"],
  "confidence": "high"
}
```

Rules for the merged text:

- Combine **facts only**. No inference, no framing, no adjectives that were not
  in the sources.
- Preserve every number, name, unit, and date exactly as written.
- If the sources contradict each other, keep both claims and mark the conflict
  with `「说法不一」` rather than silently choosing one.
- Write 2–4 sentences. Do not pad.
- `primary_id` must appear in `merge_ids`' cluster — it is the item the merged
  entry replaces, and its `url` becomes the canonical link.

## Topics

Use only these values: `technology`, `ai`, `business`, `policy`, `society`,
`security`, `internet`, `other`.

## Output

Return **JSON only**, no prose, no markdown fence:

```json
{
  "schema": "newsletter.telegram-decisions/1",
  "day": "2026-10-12",
  "model": "<your model identifier>",
  "actions": [
    {"op": "drop", "id": "telegram:xhqcankao:32420", "canonical_id": "telegram:zaihuapd:44363", "reason": "shorter repost, no unique facts"},
    {"op": "merge", "primary_id": "telegram:zaihuapd:44363", "merge_ids": ["telegram:tnews365:35680"], "title": "…", "summary": "…", "topics": ["ai"], "confidence": "high"}
  ]
}
```

`actions` may be empty. Do not invent ids. Do not return items you are leaving
alone.

## Hard rules

1. Never invent facts, numbers, names, or quotes.
2. Never merge items that share only a topic but not an event.
3. Never drop an item that contains unique information.
4. Only reference ids that appear in the input.
5. When unsure, do nothing. An untouched item is always safe; a wrong merge is not.

# The reserved AI review step

Telegram collection produces raw messages from seven channels. The same news
event is routinely posted by several of them in different words, so
**hash-based deduplication is not enough**. This document defines the step that
is deliberately left open for an AI pass.

## Why a hash is not enough

Measured on the 2026-10-12 collection:

```text
60 messages, 7 channels
exact-duplicate groups found by content hash: 0
```

Zero. The channels never repeat each other word for word:

| Channel | Title |
|---|---|
| @MaiTungNews | 2026年诺贝尔经济学奖授予罗伯特·霍尔和迈克尔·伍德福德… |
| @tnews365 | 2026年诺贝尔和平奖授予纳瓦内特姆·皮莱… |

Same day, same category of event, different stories. Meanwhile other pairs
cover the same story with different framing and detail levels. Only semantic
clustering can collapse those.

## Two-stage design

```text
stage 1  deterministic, in tools/collect.py       (already implemented)
         - normalize text, hash it
         - collapse byte-identical reposts
         - emit telegram_duplicate_groups.json

stage 2  semantic, AI-assisted                   (reserved, not automated)
         - cluster same-event items across channels
         - pick one primary, merge facts
         - rank by importance
         - emit clusters with merged neutral summaries
```

Stage 1 exists so the page is readable today and so stage 2 receives a smaller,
already-deduplicated set.

## The queue file

`tools/collect.py` writes `data/collected/telegram_ai_queue.json`:

```json
{
  "purpose": "Reserved step: AI deduplication and ranking of Telegram messages.",
  "generated_at": "2026-10-13T01:12:00+08:00",
  "model_instructions": "<the full prompt>",
  "count": 60,
  "items": [
    {
      "id": "zaihuapd:44363",
      "channel": "zaihuapd",
      "title": "🤖 DeepSeek 拟领投前腾讯科学家机器人实验室",
      "summary": "<full post text>",
      "published_at": "2026-10-12T22:59:00+08:00",
      "url": "https://t.me/zaihuapd/44363",
      "content_hash": "…"
    }
  ]
}
```

Each item carries a stable `id` (`channel:message_id`), the original text, a
CST timestamp, the permalink, and a content hash. That is everything a model
needs and nothing it does not.

## The expected response schema

```json
{
  "clusters": [
    {
      "primary_id": "zaihuapd:44363",
      "duplicate_ids": ["xhqcankao:32420"],
      "title": "neutral headline",
      "summary": "merged neutral summary",
      "topics": ["ai"],
      "importance": 82,
      "sources": ["@zaihuapd", "@xhqcankao"]
    }
  ]
}
```

`importance` is 0–100 based on impact and information density, **not** channel
popularity. `topics` come from a fixed vocabulary: technology, ai, business,
policy, society, security, internet, other.

## Guardrails written into the prompt

1. **Never invent facts** that are not in the input.
2. **Surface conflicts** instead of silently choosing one version.
3. **Preserve numbers exactly** — do not round or paraphrase figures.
4. **Keep the source language**; do not translate unless asked.
5. **Never merge** items that merely share a topic; they must share an event.

## How to run it

Manual, reviewable, and reversible:

```bash
# 1. collect and build the queue
python tools/collect.py telegram --day 2026-10-12 --outdir data/collected

# 2. hand the queue to a model with the embedded instructions
#    (the prompt is stored in the file itself, so nothing is lost)

# 3. store the model's JSON reply
#    data/collected/telegram_clusters.json

# 4. render with clusters applied
python tools/render_digest.py --indir data/collected --output site/digest.html
```

The renderer does not require clusters; when
`data/collected/telegram_clusters.json` is absent it shows the pre-deduplicated
messages. When it is present it can group by cluster and show the merged
summary plus all source links.

## Privacy and terms constraints

Telegram's current API Terms restrict using platform data for AI/ML training,
fine-tuning, and model development. This step is designed with that in mind:

- the content is **your own** collection of public channel posts;
- the queue is a **local file** you choose whether to send anywhere;
- nothing is transmitted automatically — there is no hidden network call;
- the step is **optional** and can stay entirely manual;
- for a strict reading of the terms, keep this step off and rely on stage 1 plus
  human review;
- review Telegram's terms and your own obligations before enabling any
  third-party model on this data.

This document is not legal advice.

## Where it fits

```text
tools/collect.py            → raw + pre-dedupe + queue          (automated)
data/collected/…_queue.json → your choice of model or human      (manual)
data/collected/…_clusters.json → merged, ranked clusters          (stored)
tools/render_digest.py      → digest                             (automated)
```

Keeping the middle step manual preserves the ability to audit exactly what the
model was given and what it returned.

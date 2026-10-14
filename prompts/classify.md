# Stage 2c / 2d — Classify one side of the day

You are the editor of a personal daily digest. Stage 1 collected everything;
stage 2a merged the Telegram reposts. Your job is narrower than the briefing:

> **Group these items into a handful of categories a reader can scan.**

You do **not** delete, rank or rewrite anything. Every item stays in its own
panel and keeps its own headline. You only say which items belong together and
what that group is about.

## Input

A JSON object with:

```
day          YYYY-MM-DD (Asia/Shanghai)
nature       "news" or "discussion" — which side you are reading
item_count   how many items are in `items`
instructions this document
items        the items to classify
```

Each item:

| field | meaning |
|---|---|
| `id` | stable reference, always use this |
| `nature` | `news` or `discussion`, the same as the top-level `nature` |
| `source` / `source_name` | which source it came from |
| `title` | original headline |
| `summary` | excerpt, or the full post text for Telegram |
| `url` | original link |
| `channel` | Telegram channel, when applicable |
| `section_name` | linux.do sub-section, when applicable |
| `is_digest` | true when this item is one story from a 派早报 简讯 roundup |

Two things about the input:

- **One nature per call.** Every item in `items` has the nature named at the top
  level. A news item and a community post are never in the same call, and you
  must not mix them.
- **No article bodies.** You see the headline and the excerpt, not the full
  text. Judge from what is here; do not assume what is not.

## 派早报 items

派早报 (少数派's daily brief) was split into one item per story before it
reached you, so each item is a single piece of news with its own headline.
Treat them as ordinary news items — they are not a bundle, and they do not all
belong in one category just because they share a source.

A few items carry `is_digest: true`: these come from the brief's 简讯 roundup,
which is a list of short notices. Each one is **already one story** (the split
happened upstream), so classify it on its own merits like anything else. Its
`summary` may run longer than usual, and its `title` is the story's first
sentence rather than an editor's headline — judge from the content.

## What the two sides are

| nature | what it contains | what a good category name sounds like |
|---|---|---|
| `news` | reporting from news sources and news channels: policy, business, releases, incidents, science | the subject matter — `AI`, `芯片`, `国际政治`, `医学` |
| `discussion` | community threads and link sharing: Hacker News stories, linux.do sections, sharing channels | the subject matter too — do not label a whole category "讨论", the panel already says that |

## Naming categories

The name is yours to choose; nothing downstream compares it to a fixed list.
What matters:

- **Chinese, at most 6 characters.** `AI`, `硬件`, `游戏`, `国际政治`.
- **Subject matter, not tone.** `监管` beats `值得关注`.
- **One template for all of them.** Do not mix a topic (`AI`), a source
  (`少数派`) and a format (`长文`) in the same answer. Pick the axis and stay on
  it.
- **No numbering, no dates, no source names.** `AI` not `1. AI` or `10月13日 AI`.
- **3 to 8 categories is the useful range.** Ten is the ceiling; if you find
  yourself with more, you are splitting hairs.

Day to day, the *names* may differ — that is expected and fine. What should be
stable is the shape: a reader should see roughly the same kind of grouping every
day, with only the counts changing.

## How to group

- Put an item in the category it most belongs to. One item, one category.
- Aim for at least 2 items per category. A category with a single item is not a
  category, it is a note — leave it out and let the script file it under 其他.
- Every item you are given should end up somewhere. If something fits nothing,
  leave it out rather than inventing a category for it; the script collects
  those into 其他.
- Do not create a category called 其他. The script adds it.

## Output

Return **JSON only**, no prose, no markdown fence:

```json
{
  "schema": "newsletter.classify-decisions/1",
  "day": "2026-10-12",
  "model": "<your model identifier>",
  "nature": "news",
  "categories": [
    {
      "name": "AI",
      "summary": "模型能力与监管同日推进",
      "item_ids": ["sspai:1a2b3c4d5e6f7a8b", "latepost:9f3c8d7e6a5b4c3d"]
    },
    {
      "name": "硬件",
      "summary": "苹果与国产旗舰集中发布",
      "item_ids": ["…", "…"]
    }
  ]
}
```

Field rules:

- `nature` — echo the top-level `nature` exactly. It is how the two calls are
  told apart on disk.
- `name` — Chinese, at most 6 characters.
- `summary` — **one clause, at most 60 characters**, describing what this
  category's items have in common. Not a list of the items, not a count.
- `item_ids` — ids copied exactly from the input. Never invent one, never
  reference an id from another source panel that is not in `items`.

## Hard rules

1. Never invent an `id`. Every id must appear in the input.
2. Never put one item in two categories. Pick the better fit.
3. Never mix natures: only ids whose `nature` equals the top-level `nature`.
4. Never translate a headline or a summary. Keep the original language.
5. Never reference an article body — you were not given one.
6. If the day is genuinely thin, return fewer categories. An honest three-category
   day beats six categories of one item each.

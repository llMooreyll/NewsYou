#!/usr/bin/env python3
"""Generate demo stage-2 decision files from the current collection.

These are hand-written heuristics, not model output. They exist so the
repository ships a working example of both decision formats, and so the digest
shows what stage 2 produces without anyone needing to call a model first.

The selection is **derived from the data**, not from hardcoded keywords: a
hardcoded list silently degrades to "0 keywords" the moment the day's messages
change. Merges come from cross-channel keyword overlap, keywords from word
frequency, the summary from the busiest sources, and categories from the sources
the items came from.

Real usage replaces these with actual model output.

    python tools/demo_decisions.py --collected data/collected --ai data/ai
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ai_stage  # noqa: E402

COLLECTED = Path("data/collected")
AIDIR = Path("data/ai")

MODEL_NOTE = "demo-decisions (heuristic example, not model output)"

# Words that carry no topic signal in these feeds.
STOPWORDS = {
    "公司", "表示", "宣布", "发布", "消息", "报道", "记者", "今天", "昨日", "今日",
    "月", "日", "年", "中国", "美国", "来源", "根据", "以及", "并且", "但是", "因为",
    "我们", "他们", "这个", "那个", "一个", "可以", "已经", "目前", "进行", "相关",
    "the", "and", "for", "with", "that", "this", "from", "have", "will", "are",
}

def keywords(text: str) -> set[str]:
    """Cheap topic-keyword extraction: CJK runs of >=2 and ASCII words of >=4."""
    text = text or ""
    latin = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]{3,}", text)}
    cjk_runs = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    cjk: set[str] = set()
    for run in cjk_runs:
        if run not in STOPWORDS:
            cjk.add(run)
        # also add 2-3 char shingles so "电动汽车" and "汽车贸易" can overlap
        for size in (2, 3):
            for i in range(len(run) - size + 1):
                gram = run[i:i + size]
                if gram not in STOPWORDS:
                    cjk.add(gram)
    return {w for w in (latin | cjk) if w not in STOPWORDS}

def overlap(a: set[str], b: set[str]) -> set[str]:
    return a & b

def build_merges(entries: list[dict], id_of) -> list[dict]:
    """Merge messages from different channels that share a clear topic."""
    kws = {id_of(e): keywords(e.get("extra", {}).get("text") or e.get("title", "")) for e in entries}
    used: set[str] = set()
    actions: list[dict] = []

    candidates: list[tuple[int, str, str, set[str]]] = []
    for i, a in enumerate(entries):
        for b in entries[i + 1:]:
            if a["extra"].get("channel") == b["extra"].get("channel"):
                continue
            shared = overlap(kws[id_of(a)], kws[id_of(b)])
            if len(shared) >= 4:
                candidates.append((len(shared), id_of(a), id_of(b), shared))

    candidates.sort(key=lambda x: -x[0])
    for score, primary, secondary, shared in candidates:
        if primary in used or secondary in used:
            continue
        used.update({primary, secondary})
        pe = next(e for e in entries if id_of(e) == primary)
        se = next(e for e in entries if id_of(e) == secondary)
        pt = (pe["extra"].get("text") or "").strip()
        st = (se["extra"].get("text") or "").strip()
        # the longer message usually carries more detail; keep it as primary
        if len(st) > len(pt):
            primary, secondary, pe, se, pt, st = secondary, primary, se, pe, st, pt
        actions.append({
            "op": "merge", "primary_id": primary, "merge_ids": [secondary],
            "title": (pt.splitlines() or [pe["title"]])[0][:90],
            "summary": re.sub(r"\s+", " ", pt)[:400],
            "topics": ["other"], "confidence": "medium",
        })
        if len(actions) >= 3:
            break
    return actions

def build_keywords(entries: list[dict], id_of, merges: list[dict]) -> list[dict]:
    """Weighted keywords from keyword frequency across the day.

    A stand-in for the model's judgement, built the same way the model is asked
    to build it: a word many messages share is a subject of the day, and its
    weight follows how many carry it. Ties keep a spread rather than collapsing
    to one size, which is what the display relies on.
    """
    counts: dict[str, int] = {}
    for entry in entries:
        text = (entry.get("extra") or {}).get("text") or entry.get("title") or ""
        for word in keywords(text):
            counts[word] = counts.get(word, 0) + 1
    if not counts:
        return []
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:12]
    top = ranked[0][1] or 1
    return [{"text": word, "weight": max(10, min(95, int(10 + 85 * n / top)))}
            for word, n in ranked]

def build_summary(entries: list[dict], id_of, merges: list[dict],
                  channels: list[str]) -> tuple[list[dict], list[str]]:
    """A stand-in for the model's prose, plus the ids it would have cited.

    Two paragraphs rather than one, because that is the shape the real answer
    takes: one thread per paragraph, each naming the words it is about so the
    page can mark them. Derived from the data like everything else here.
    """
    if not entries:
        return [{"text": "当日无内容。", "keywords": []}], []
    scored = sorted(entries, key=lambda e: -(len((e.get("extra") or {}).get("text") or "")))
    cited = [id_of(e) for e in scored[:12]]
    busiest = sorted({e.get("source") for e in entries if e.get("source")})
    return [
        {"text": f"当日共 {len(entries)} 条，来自 {len(busiest)} 个来源"
                 f"（{('、'.join(busiest[:5]))}）。",
         "keywords": busiest[:3]},
        {"text": f"{len(channels)} 个 Telegram 频道有更新，"
                 f"{len(merges)} 组跨频道重复已合并。"
                 f"本条为演示文案，用于替代模型输出。",
         "keywords": ["Telegram"]},
    ], cited

def load_items(collected: Path) -> dict[str, list[dict]]:
    """Every collected source, not just Telegram: the classification covers the
    whole day, so the example has to as well."""
    out: dict[str, list[dict]] = {}
    for path in sorted(collected.glob("*.json")):
        if path.name in ("index.json", "telegram_duplicate_groups.json"):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "items" in payload:
            out[path.stem] = payload["items"]
    return out

def build_categories(sources: dict[str, list[dict]], side: str, id_of,
                     skip: set[str]) -> list[dict]:
    """Bucket one side by where its items came from.

    This is not a model answer and does not pretend to be one. It exists so the
    shipped example has the right shape, and so `make demo` exercises the same
    validator, shape rules and applier that real output goes through.

    `skip` holds the ids a merge absorbed: they are no longer top-level items,
    so a category naming one would be rejected.
    """
    buckets: dict[str, list[str]] = {}
    for name in ai_stage.source_order(sources):
        for entry in sources[name]:
            key = id_of(entry)
            if key in skip or (entry.get("nature") or "news") != side:
                continue
            extra = entry.get("extra") or {}
            label = ("@" + str(extra["channel"])) if extra.get("channel") else (
                entry.get("source_name") or entry.get("source") or "其他")
            buckets.setdefault(label, []).append(key)
    return [{"name": name, "summary": f"{len(ids)} 条来自 {name}", "item_ids": ids}
            for name, ids in sorted(buckets.items(), key=lambda kv: (-len(kv[1]), kv[0]))
            if len(ids) >= ai_stage.CATEGORY_MIN_ITEMS]

def main() -> int:
    parser = argparse.ArgumentParser(description="Generate demo stage-2 decision files.")
    parser.add_argument("--collected", type=Path, default=COLLECTED)
    parser.add_argument("--ai", type=Path, default=AIDIR)
    args = parser.parse_args()
    collected, aidir = args.collected, args.ai

    path = collected / "telegram.json"
    if not path.exists():
        print(f"{path} not found; run tools/collect.py first", file=sys.stderr)
        return 1

    entries = json.loads(path.read_text(encoding="utf-8"))["items"]
    sources = load_items(collected)
    index = json.loads((collected / "index.json").read_text(encoding="utf-8"))
    day = index.get("day", "")
    engine = index.get("engine", "legacy")

    def id_of(entry: dict) -> str:
        return ai_stage.item_id(entry)

    merges = build_merges(entries, id_of)
    # a merge member is not a top-level item any more, so no category may name it
    absorbed = {m for action in merges for m in action["merge_ids"]}
    channels = sorted({e["extra"].get("channel") for e in entries if e["extra"].get("channel")})
    keywords = build_keywords(entries, id_of, merges)
    summary, cited = build_summary(entries, id_of, merges, channels)
    aidir.mkdir(parents=True, exist_ok=True)

    for side, stem in ai_stage.CLASSIFY_SIDES:
        categories = build_categories(sources, side, id_of, absorbed)
        (aidir / f"{stem}_decisions.json").write_text(json.dumps({
            "schema": ai_stage.CLASSIFY_SCHEMA,
            "day": day,
            "model": MODEL_NOTE,
            "nature": side,
            "categories": categories,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {aidir}/{stem}_decisions.json ({len(categories)} categories)")

    (aidir / "telegram_decisions.json").write_text(json.dumps({
        "schema": ai_stage.TELEGRAM_SCHEMA,
        "day": day,
        "model": MODEL_NOTE,
        "actions": merges,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    (aidir / "digest_decisions.json").write_text(json.dumps({
        "schema": ai_stage.DIGEST_SCHEMA,
        "day": day,
        "model": MODEL_NOTE,
        "summary": summary,
        "keywords": keywords,
        "source_ids": cited,
        "stats_note": (f"{len(channels)} 个频道当日共 {len(entries)} 条消息，"
                       f"其中 {len(merges)} 组为跨频道重复并已合并。引擎：{engine}。"),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"wrote {aidir}/telegram_decisions.json ({len(merges)} actions)")
    print(f"wrote {aidir}/digest_decisions.json ({len(keywords)} keywords, {len(cited)} audit ids)")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

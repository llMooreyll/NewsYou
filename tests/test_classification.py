"""Offline tests for classification: identity, nature, categories, anchors.

    ./.venv/bin/python -m unittest discover -s tests -v

No network, no model calls. Stage 2c/2d is a pure function over a decision
object, so everything here runs against hand-written decisions — which is also
how the pipeline is meant to work without a model at all.
"""
from __future__ import annotations

import contextlib
import io
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import ai_stage  # noqa: E402
import collect  # noqa: E402
import config  # noqa: E402
import render_digest  # noqa: E402
from collectors import base, registry  # noqa: E402

DAY = "2026-10-13"

def _item(source, title, url, **extra):
    return base.item(source, source, title, url, extra=extra)

def _stamped(source, title, url, nature="news", **extra):
    """An item as stage 1 leaves it: with an id and a nature."""
    entry = _item(source, title, url, **extra)
    entry["id"] = ai_stage.item_id(entry)
    entry["nature"] = nature
    return entry

def _tg(channel, mid, text, nature="news"):
    entry = base.item("telegram", f"Telegram · @{channel}", text.split("\n")[0],
                      f"https://t.me/{channel}/{mid}", kind="message", summary=text,
                      extra={"channel": channel, "message_id": mid, "text": text,
                             "local_images": [], "photos": [], "media": []})
    entry["id"] = ai_stage.item_id(entry)
    entry["nature"] = nature
    return entry

# --------------------------------------------------------------------------
# identity and nature
# --------------------------------------------------------------------------

class StampedIdentity(unittest.TestCase):
    def test_item_id_prefers_the_stamp(self):
        entry = _item("sspai", "标题", "https://sspai.com/post/1")
        derived = ai_stage.item_id(entry)
        entry["id"] = "sspai:handwritten"
        self.assertEqual(ai_stage.item_id(entry), "sspai:handwritten")
        self.assertNotEqual(derived, "sspai:handwritten")

    def test_the_derivation_is_unchanged_without_a_stamp(self):
        a = _item("sspai", "标题", "https://sspai.com/post/1?utm_source=rss")
        b = _item("sspai", "别的标题", "https://sspai.com/post/1")
        self.assertEqual(ai_stage.item_id(a), ai_stage.item_id(b))

class Stamping(unittest.TestCase):
    def test_ids_and_natures_are_written_onto_the_items(self):
        items = [_item("sspai", "标题", "https://sspai.com/post/1"),
                 _tg("chan", 7, "正文")]
        collect._stamp_identity("telegram", items, {}, {"telegram:chan": "discussion"})
        self.assertTrue(all(e.get("id") for e in items))
        self.assertEqual(items[0]["nature"], "news")
        self.assertEqual(items[1]["nature"], "discussion")

    def test_two_items_sharing_one_url_do_not_share_an_id(self):
        """dedupe() keys on title+summary, the id keys on the URL, so the same
        URL under two titles survives as two items. A shared id would make every
        citation ambiguous."""
        items = [base.item("sspai", "少数派", "标题一", "https://sspai.com/post/1"),
                 base.item("sspai", "少数派", "标题二", "https://sspai.com/post/1")]
        log = io.StringIO()
        with contextlib.redirect_stderr(log):
            collect._stamp_identity("sspai", items, {}, {})
        self.assertNotEqual(items[0]["id"], items[1]["id"])
        self.assertIn("duplicate id", log.getvalue())

    def test_a_unit_name_nothing_matched_is_reported(self):
        log = io.StringIO()
        with contextlib.redirect_stderr(log):
            collect._stamp_identity("linuxdo", [_item("linuxdo", "t", "https://linux.do/t/1")],
                                    {}, {"linuxdo:站外速览": "news"})
        self.assertIn("站外速览", log.getvalue())

class NatureResolution(unittest.TestCase):
    def test_an_unlisted_source_is_news(self):
        self.assertEqual(registry.source_nature_of("sspai", None, source_nature={},
                                                   unit_nature={}), "news")

    def test_a_source_default_beats_the_fallback(self):
        self.assertEqual(registry.source_nature_of("hackernews", None,
                                                   source_nature={"hackernews": "discussion"},
                                                   unit_nature={}), "discussion")

    def test_a_unit_override_beats_its_source(self):
        self.assertEqual(registry.source_nature_of("telegram", "wxbyg", source_nature={},
                                                   unit_nature={"telegram:wxbyg": "longform"}),
                         "longform")

    def test_the_shipped_defaults_agree_with_the_registry(self):
        cfg = config.load(ROOT / "config" / "config.toml")
        self.assertEqual(cfg.get("sources.units"), registry.UNIT_NATURE)
        self.assertEqual(cfg.get("sources.nature"), registry.nature_groups())
        self.assertEqual(cfg.get("telegram.channels"), list(registry.get("telegram").DEFAULT_CHANNELS))

class ClassifyGroups(unittest.TestCase):
    def test_longform_is_in_neither_side(self):
        news = _stamped("sspai", "新闻", "https://sspai.com/1")
        disc = _stamped("hackernews", "帖子", "https://news.ycombinator.com/item?id=1",
                        nature="discussion")
        long = _stamped("ruanyifeng", "周刊", "https://ruanyifeng.com/blog/1", nature="longform")
        groups = ai_stage.classify_groups({"sspai": [news], "hackernews": [disc],
                                          "ruanyifeng": [long]})
        self.assertEqual([e["id"] for e in groups["news"]], [news["id"]])
        self.assertEqual([e["id"] for e in groups["discussion"]], [disc["id"]])

    def test_an_item_without_a_nature_counts_as_news(self):
        """What a data set collected before the field existed looks like."""
        old = _item("sspai", "旧数据", "https://sspai.com/1")
        groups = ai_stage.classify_groups({"sspai": [old]})
        self.assertEqual(len(groups["news"]), 1)

# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------

class ClassifyValidation(unittest.TestCase):
    def setUp(self):
        self.news = [_stamped("sspai", "新闻一", "https://sspai.com/post/1"),
                     _stamped("sspai", "新闻二", "https://sspai.com/post/2")]
        self.disc = [_stamped("hackernews", "帖子", "https://news.ycombinator.com/item?id=1",
                              nature="discussion")]
        self.index = {e["id"]: e for e in self.news + self.disc}
        self.a, self.b = (e["id"] for e in self.news)

    def validate(self, categories, nature="news"):
        return ai_stage.validate_classify_decisions({"categories": categories},
                                                    nature, self.index)

    def test_an_unknown_id_is_rejected(self):
        cats, problems = self.validate([{"name": "AI", "item_ids": ["sspai:nope"]}])
        self.assertEqual(cats, [])
        self.assertTrue(any("unknown id" in p for p in problems))

    def test_an_id_of_the_other_nature_is_rejected(self):
        """A news id in the discussion file would file a news item under a
        community heading."""
        cats, problems = self.validate([{"name": "闲聊", "item_ids": [self.a]}],
                                       nature="discussion")
        self.assertEqual(cats, [])
        self.assertTrue(any("not discussion" in p for p in problems))

    def test_an_item_is_claimed_once(self):
        cats, problems = self.validate([{"name": "甲", "item_ids": [self.a]},
                                        {"name": "乙", "item_ids": [self.a, self.b]}])
        self.assertEqual([c["name"] for c in cats], ["甲", "乙"])
        self.assertEqual(cats[1]["item_ids"], [self.b])
        self.assertTrue(any("already categorised" in p for p in problems))

    def test_a_repeated_name_becomes_one_category(self):
        cats, problems = self.validate([{"name": "AI", "item_ids": [self.a]},
                                        {"name": "AI", "item_ids": [self.b]}])
        self.assertEqual(len(cats), 1)
        self.assertEqual(cats[0]["item_ids"], [self.a, self.b])
        self.assertTrue(any("twice" in p for p in problems))

    def test_the_summary_is_truncated_to_the_limit(self):
        cats, _ = self.validate([{"name": "AI", "summary": "长" * 300,
                                  "item_ids": [self.a, self.b]}])
        self.assertEqual(len(cats[0]["summary"]), ai_stage.CATEGORY_SUMMARY_LIMIT)

    def test_a_category_with_no_usable_ids_is_dropped(self):
        cats, problems = self.validate([{"name": "AI", "item_ids": []}])
        self.assertEqual(cats, [])
        self.assertTrue(any("no usable item_ids" in p for p in problems))

# --------------------------------------------------------------------------
# shape rules
# --------------------------------------------------------------------------

class CategoryShape(unittest.TestCase):
    def items(self, count: int) -> list[dict]:
        return [_stamped("sspai", f"标题{i}", f"https://sspai.com/post/{i}")
                for i in range(count)]

    def test_a_category_below_the_minimum_folds_into_the_fallback(self):
        items = self.items(3)
        ids = [e["id"] for e in items]
        cats, problems = ai_stage.finalize_categories(
            [{"name": "大", "summary": "", "item_ids": ids[:2]},
             {"name": "小", "summary": "", "item_ids": ids[2:]}], items, "news")
        self.assertEqual([c["name"] for c in cats], ["大", ai_stage.CATEGORY_FALLBACK])
        self.assertEqual(cats[1]["item_ids"], ids[2:])
        self.assertTrue(any("folded" in p for p in problems))

    def test_every_item_of_the_side_is_accounted_for(self):
        items = self.items(4)
        ids = [e["id"] for e in items]
        cats, _ = ai_stage.finalize_categories(
            [{"name": "甲", "summary": "", "item_ids": ids[:3]}], items, "news")
        placed = [ref for c in cats for ref in c["item_ids"]]
        self.assertEqual(sorted(placed), sorted(ids))

    def test_an_unplaced_item_lands_in_the_fallback(self):
        items = self.items(3)
        ids = [e["id"] for e in items]
        cats, _ = ai_stage.finalize_categories(
            [{"name": "甲", "summary": "", "item_ids": ids[:2]}], items, "news")
        self.assertEqual(cats[-1]["name"], ai_stage.CATEGORY_FALLBACK)
        self.assertEqual(cats[-1]["item_ids"], ids[2:])

    def test_categories_past_the_cap_fold_into_the_fallback(self):
        items = self.items(30)
        ids = [e["id"] for e in items]
        many = [{"name": f"类{i}", "summary": "", "item_ids": [ids[i * 2], ids[i * 2 + 1]]}
                for i in range(15)]
        cats, problems = ai_stage.finalize_categories(many, items, "news")
        named = [c for c in cats if c["name"] != ai_stage.CATEGORY_FALLBACK]
        self.assertEqual(len(named), ai_stage.CATEGORY_LIMIT)
        self.assertTrue(any("trimmed" in p for p in problems))
        placed = [ref for c in cats for ref in c["item_ids"]]
        self.assertEqual(sorted(placed), sorted(ids))

    def test_categories_are_ordered_by_size(self):
        items = self.items(5)
        ids = [e["id"] for e in items]
        cats, _ = ai_stage.finalize_categories(
            [{"name": "小", "summary": "", "item_ids": ids[:2]},
             {"name": "大", "summary": "", "item_ids": ids[2:]}], items, "news")
        self.assertEqual([c["name"] for c in cats], ["大", "小"])

    def test_anchors_are_sequence_numbers_and_continue_across_sides(self):
        items = self.items(6)
        ids = [e["id"] for e in items]
        index = {e["id"]: e for e in items}
        cats, _ = ai_stage.finalize_categories(
            [{"name": "大", "summary": "", "item_ids": ids[:4]},
             {"name": "小", "summary": "", "item_ids": ids[4:]}], items, "news")
        resolved = ai_stage.apply_classify_decisions(cats, index, anchor_start=3)
        # ordered by size, and the second side's anchors continue where the first stopped
        self.assertEqual([c["anchor"] for c in resolved], ["cat-4", "cat-5"])
        self.assertEqual([c["name"] for c in resolved], ["大", "小"])
        self.assertEqual([ref["id"] for ref in resolved[0]["refs"]], ids[:4])

    def test_has_categories_ignores_an_empty_file(self):
        self.assertFalse(ai_stage.has_categories(None))
        self.assertFalse(ai_stage.has_categories({"sides": {"news": {"categories": []}}}))
        self.assertTrue(ai_stage.has_categories(
            {"sides": {"news": {"categories": [{"anchor": "cat-1"}]}}}))

# --------------------------------------------------------------------------
# rendering: anchors, chips, the panel without a briefing
# --------------------------------------------------------------------------

class AnchorConsistency(unittest.TestCase):
    """A citation has to land somewhere, and an id may appear only once.

    The digest panel is a view over items that live in the source panels, so the
    two halves have to agree: an item without an anchor is a dead chip, and an
    anchor emitted in both places makes getElementById return whichever the
    document reaches first.
    """

    def setUp(self):
        self.items = [
            _stamped("sspai", "文一", "https://sspai.com/post/1"),
            _stamped("solidot", "文二", "https://www.solidot.org/story?sid=1"),
            _tg("chan", 5, "电报正文"),
            _stamped("weibo", "热搜一", "https://s.weibo.com/x", rank=1, heat="100万"),
        ]
        sources = {e["source"]: {"items": [e], "status": {}, "day": DAY} for e in self.items}
        self.html = render_digest.render(sources, self.briefing(), Path("/tmp/x.html"), False,
                                         {}, self.categories())

    def ref(self, entry):
        return {"id": entry["id"], "source": entry["source"],
                "source_name": entry["source_name"], "channel": (entry.get("extra") or {}).get("channel"),
                "title": entry["title"], "url": entry["url"]}

    def briefing(self):
        return {"cards": [{"rank": 1, "title": "头条", "summary": "摘要", "importance": 90,
                           "refs": [self.ref(self.items[0])]}]}

    def categories(self):
        return {"sides": {"news": {"nature": "news", "item_count": len(self.items),
                                   "classified": len(self.items),
                                   "categories": [{"anchor": "cat-1", "name": "AI",
                                                   "summary": "导语",
                                                   "refs": [self.ref(e) for e in self.items]}]}}}

    def test_no_id_appears_twice(self):
        ids = re.findall(r'id="([^"]+)"', self.html)
        self.assertEqual(len(ids), len(set(ids)),
                         f"duplicate DOM id: {sorted({i for i in ids if ids.count(i) > 1})}")

    def test_every_item_has_an_anchor_in_its_own_panel(self):
        for entry in self.items:
            self.assertIn(f'id="{render_digest.item_anchor(entry)}"', self.html)

    def test_every_chip_resolves(self):
        keys = re.findall(r'data-jump="([^"]+)"', self.html)
        self.assertTrue(keys)
        for key in keys:
            self.assertIn(f'id="it-{key}"', self.html, f"chip {key} points at nothing")

    def test_a_chip_carries_a_fallback_href(self):
        self.assertIn(f'href="#it-{self.items[0]["id"]}"', self.html)

    def test_the_category_rows_do_not_duplicate_the_anchor(self):
        """The row carries the chip; the id belongs to the source panel."""
        row = re.search(r'<li class="tp rv" style="--i:1">.*?</li>', self.html, re.S)
        self.assertIsNotNone(row)
        self.assertNotIn('id="it-', row.group(0))

class DigestPanelWithoutBriefing(unittest.TestCase):
    """Stage 2b failing must not make the classification invisible, and the
    other way round: the two arrive in separate files."""

    def categories(self):
        return {"sides": {"news": {"nature": "news", "item_count": 3, "classified": 2,
                                   "categories": [{"anchor": "cat-1", "name": "AI",
                                                   "summary": "导语", "refs": [
                                                       {"id": "sspai:1", "source": "sspai",
                                                        "source_name": "少数派", "title": "T",
                                                        "url": "https://sspai.com/1"}]}]}}}

    def render(self, digest, categories):
        return render_digest.render({"sspai": {"items": [], "status": {}, "day": DAY}}, digest,
                                    Path("/tmp/x.html"), False, {}, categories)

    def test_categories_alone_still_produce_the_panel(self):
        html = self.render(None, self.categories())
        self.assertIn('data-tab="digest"', html)
        self.assertIn('id="cat-1"', html)
        self.assertIn("已归类 2/3", html)

    def test_neither_input_means_no_panel(self):
        html = self.render(None, None)
        self.assertNotIn('data-tab="digest"', html)

    def test_an_empty_digest_does_not_break_the_categories(self):
        html = self.render({"cards": []}, self.categories())
        self.assertIn('id="cat-1"', html)
        self.assertNotIn('id="dg-lead"', html)

# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------

class NatureConfig(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def write(self, text: str) -> Path:
        path = self.dir / "config.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_an_unknown_source_is_rejected(self):
        with self.assertRaises(config.ConfigError):
            config.load(self.write('[sources.nature]\ndiscussion = ["nope"]\n'))

    def test_a_feed_id_is_a_known_source(self):
        cfg = config.load(self.write(
            '[[feeds]]\nid = "my-blog"\nurl = "https://e/f"\n\n'
            '[sources.nature]\ndiscussion = ["my-blog"]\n'))
        self.assertEqual(cfg.get("sources.nature.discussion"), ["my-blog"])

    def test_a_unit_key_without_a_colon_is_rejected(self):
        with self.assertRaises(config.ConfigError):
            config.load(self.write('[sources.units]\ntelegram = "discussion"\n'))

    def test_a_nested_units_table_is_rejected(self):
        """[sources.units.telegram] would flatten into keys nothing looks up,
        which is exactly the silent no-op the config layer promises to avoid."""
        with self.assertRaises(config.ConfigError):
            config.load(self.write('[sources.units.telegram]\nreorx_share = "discussion"\n'))

    def test_an_unknown_channel_is_rejected(self):
        with self.assertRaises(config.ConfigError):
            config.load(self.write('[sources.units]\n"telegram:nope" = "discussion"\n'))

    def test_an_unknown_nature_is_rejected(self):
        with self.assertRaises(config.ConfigError):
            config.load(self.write('[sources.units]\n"telegram:wxbyg" = "long"\n'))

    def test_show_config_dumps_keys_toml_can_read_back(self):
        import tomllib
        dumped = config.load(ROOT / "config" / "config.toml").dump(show_origin=False)
        again = tomllib.loads(dumped)
        self.assertEqual(again["sources"]["units"]["telegram:wxbyg"], "longform")
        self.assertEqual(again["sources"]["units"]["linuxdo:站外速览"], "news")

if __name__ == "__main__":
    unittest.main()

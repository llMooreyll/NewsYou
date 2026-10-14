"""Offline tests for the 少数派 split and the digest summary stage.

    ./.venv/bin/python -m unittest discover -s tests -v

No network: the article HTML and the API payloads are hand-written fixtures in
the shape the real ones have, so the splitting rules are exercised without
depending on what 少数派 published today.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import ai_stage  # noqa: E402
import config  # noqa: E402
import render_digest  # noqa: E402
from collectors import base, registry, sspai  # noqa: E402

DAY = "2026-10-13"

# --------------------------------------------------------------------------
# fixtures: one issue of 派早报 in the shape the extractor produces
# --------------------------------------------------------------------------

def _section(title, text):
    return {"title": title, "text": text, "html": f"<p>{text}</p>"}

def _digest_section(title, items):
    """A 简讯 section: `text` newline-joined, `html` a list, nesting included.

    The nested `<ul>` under the second item is what the real pages have, and is
    the reason the splitter tracks depth instead of taking every `<li>`.
    """
    html = "<ul>" + "".join(f"<li>{t}</li>" for t in items) + "</ul>"
    return {"title": title, "text": "\n".join(items), "html": html}

def _issue_body():
    return {"sections": [
        _section("Apple 发布 HomePod mini 2", "Apple 今天发布了新一代智能扬声器。"),
        _section("Google 发布 Gemini 4 Argon", "Google 发布了新模型，输出上限提至 100 万词元。"),
        _section("少数派的近期动态", "参与调研，送出 50 元面值京东卡。"),
        _digest_section("不妨一看的简讯",
                        ["三星即将推出的首款智能眼镜规格披露。",
                         "《无人深空》将于 10 月 28 日上线 iOS。"]),
        _section("你可能错过的文章", "社区速递 161 | 小米手表 S5。"),
    ]}

def _parent(url="https://sspai.com/post/116138"):
    return base.item("sspai", "少数派", "派早报：Apple 发布 HomePod mini 2 等", url,
                     kind="article", published_at=f"{DAY}T08:40:39+08:00",
                     date_source="api", summary="推广文案", author="少数派编辑部",
                     tags=["派早报"],
                     extra={"collector": "api_v1", "post_type": sspai.POST_TYPE_BRIEF})

# --------------------------------------------------------------------------
# section selection
# --------------------------------------------------------------------------

class IssueSections(unittest.TestCase):
    def test_promotions_are_dropped(self):
        titles = [s["title"] for s in sspai._issue_sections(_issue_body())]
        self.assertNotIn("少数派的近期动态", titles)
        self.assertNotIn("你可能错过的文章", titles)

    def test_promotion_variants_are_recognised(self):
        for title in ("你可能错过的好文章", "少数派的近期动态"):
            self.assertTrue(sspai.PROMO_SECTION.match(title), title)
        self.assertFalse(sspai.PROMO_SECTION.match("看看就行的简讯"))

    def test_news_sections_survive_in_order(self):
        titles = [s["title"] for s in sspai._issue_sections(_issue_body())]
        self.assertEqual(titles[:2], ["Apple 发布 HomePod mini 2",
                                      "Google 发布 Gemini 4 Argon"])

    def test_the_digest_section_is_expanded(self):
        """简讯 is a list of stories, so it becomes one block per story."""
        blocks = sspai._issue_sections(_issue_body())
        digest = [b for b in blocks if b.get("digest_index")]
        self.assertEqual(len(digest), 2)
        self.assertEqual(digest[0]["title"], "三星即将推出的首款智能眼镜规格披露")

    def test_the_digest_section_is_not_kept_whole(self):
        titles = [s["title"] for s in sspai._issue_sections(_issue_body())]
        self.assertNotIn("不妨一看的简讯", titles)

    def test_an_empty_section_is_skipped(self):
        body = {"sections": [_section("有内容", "正文"), {"title": "空", "text": "", "html": ""}]}
        self.assertEqual([s["title"] for s in sspai._issue_sections(body)], ["有内容"])

class DigestSplit(unittest.TestCase):
    def test_nesting_is_respected(self):
        """The real pages nest a <ul> of details under one story; taking every
        <li> would turn those details into separate stories."""
        html = ("<ul><li>第一条独立新闻。</li>"
                "<li>第二条新闻：<ul><li>细节一。</li><li>细节二。</li></ul></li>"
                "<li>第三条独立新闻。</li></ul>")
        items = sspai._top_level_items(html)
        self.assertEqual(len(items), 3)
        self.assertIn("细节一", items[1])
        self.assertNotIn("细节一", items[0])

    def test_a_mismatch_falls_back_to_the_whole_section(self):
        """If the two representations disagree, keeping the section whole beats
        inventing a split."""
        section = {"title": "简讯", "text": "只有一行",
                   "html": "<ul><li>a</li><li>b</li></ul>"}
        blocks = sspai._digest_blocks(section)
        self.assertEqual(len(blocks), 1)
        self.assertNotIn("digest_index", blocks[0])

    def test_first_sentence_becomes_the_title(self):
        self.assertEqual(sspai._first_sentence("第一条新闻。第二条新闻。"), "第一条新闻")

    def test_a_colon_does_not_end_a_title(self):
        """《复仇者联盟 5：毁灭之日》would be cut in half by splitting on ：."""
        text = "漫威影业宣布《复仇者联盟 5：毁灭之日》定档 12 月 16 日。"
        self.assertEqual(sspai._first_sentence(text), text.rstrip("。"))

    def test_a_long_first_sentence_is_truncated(self):
        head = sspai._first_sentence("甲" * 200 + "。后文")
        self.assertLessEqual(len(head), 61)
        self.assertTrue(head.endswith("…"))

# --------------------------------------------------------------------------
# child items
# --------------------------------------------------------------------------

class ChildItems(unittest.TestCase):
    def setUp(self):
        self.parent = _parent()
        self.children = sspai._split_issue(self.parent, _issue_body(), "modern", "today")

    def test_one_item_per_story(self):
        # two news sections plus two 简讯 stories
        self.assertEqual(len(self.children), 4)

    def test_ids_are_prefixed_with_the_issue(self):
        parent_id = ai_stage.item_id(self.parent)
        self.assertEqual(self.children[0]["id"], f"{parent_id}:1")
        self.assertEqual(self.children[-1]["id"], f"{parent_id}:4")
        self.assertEqual(len({c["id"] for c in self.children}), 4)

    def test_every_child_carries_the_issue_metadata(self):
        for child in self.children:
            extra = child["extra"]
            self.assertEqual(extra["column"], "派早报")
            self.assertEqual(extra["issue_id"], ai_stage.item_id(self.parent))
            self.assertEqual(extra["issue_title"], self.parent["title"])
            self.assertEqual(extra["issue_url"], self.parent["url"])
            self.assertEqual(extra["issue_total"], 4)

    def test_indexes_are_sequential(self):
        self.assertEqual([c["extra"]["issue_index"] for c in self.children], [1, 2, 3, 4])

    def test_digest_stories_are_flagged(self):
        flags = [bool(c["extra"].get("is_digest")) for c in self.children]
        self.assertEqual(flags, [False, False, True, True])

    def test_the_body_becomes_the_childs_own_text(self):
        self.assertIn("智能扬声器", self.children[0]["body_text"])
        self.assertNotIn("输出上限", self.children[0]["body_text"])
        self.assertIn("输出上限", self.children[1]["body_text"])

    def test_children_do_not_nest_sections(self):
        for child in self.children:
            self.assertFalse(child.get("sections"))

    def test_an_unsplittable_body_returns_nothing(self):
        """The caller keeps the parent item, which is the honest outcome."""
        self.assertEqual(sspai._split_issue(self.parent, {"sections": []}, "modern", "today"), [])
        self.assertEqual(sspai._split_issue(self.parent, {"sections": [
            _section("少数派的近期动态", "推广")]}, "modern", "today"), [])

# --------------------------------------------------------------------------
# nature: only the brief reaches classification
# --------------------------------------------------------------------------

class BriefNature(unittest.TestCase):
    def setUp(self):
        self.parent = _parent()
        self.children = sspai._split_issue(self.parent, _issue_body(), "modern", "today")
        self.others = [
            base.item("sspai", "少数派", "一篇长文", "https://sspai.com/post/1",
                      extra={"collector": "api_v1", "post_type": 1}),
            base.item("sspai", "少数派", "另一篇", "https://sspai.com/post/2",
                      extra={"collector": "api_v1", "post_type": 1}),
        ]
        self.cfg = config.load(ROOT / "config" / "config.toml")
        self.source_nature, self.unit_nature = collect_maps(self.cfg)

    def test_the_brief_is_news_and_the_rest_is_longform(self):
        import collect
        items = self.children + self.others
        collect._stamp_identity("sspai", items, self.source_nature, self.unit_nature)
        by_column = {}
        for entry in items:
            by_column.setdefault(entry["extra"].get("column"), set()).add(entry["nature"])
        self.assertEqual(by_column["派早报"], {"news"})
        self.assertEqual(by_column[None], {"longform"})

    def test_only_the_brief_reaches_the_classifier(self):
        import collect
        items = self.children + self.others
        collect._stamp_identity("sspai", items, self.source_nature, self.unit_nature)
        groups = ai_stage.classify_groups({"sspai": items})
        self.assertEqual(len(groups["news"]), len(self.children))
        self.assertEqual({e["extra"]["column"] for e in groups["news"]}, {"派早报"})

    def test_a_derived_unit_does_not_warn_when_unmatched(self):
        """派早报 is set by the collector, so a day without it is not a typo."""
        import collect
        import contextlib, io
        log = io.StringIO()
        with contextlib.redirect_stderr(log):
            collect._stamp_identity("sspai", self.others, self.source_nature,
                                    self.unit_nature)
        self.assertEqual(log.getvalue(), "")

def collect_maps(cfg):
    import collect
    return collect._nature_maps(cfg)

# --------------------------------------------------------------------------
# brief state
# --------------------------------------------------------------------------

class BriefState(unittest.TestCase):
    def issue(self, day: str) -> dict:
        return {"published_at": f"{day}T08:00:00+08:00"}

    def test_a_weekend_is_suspended(self):
        saturday = datetime(2026, 10, 17, 10, 0)      # 周六
        self.assertEqual(sspai.brief_state([], saturday), "suspended")

    def test_todays_issue_is_published(self):
        monday = datetime(2026, 10, 12, 10, 0)
        self.assertEqual(sspai.brief_state([self.issue("2026-10-12")], monday), "published")

    def test_a_weekday_morning_before_publication_is_pending(self):
        monday = datetime(2026, 10, 12, 7, 0)
        self.assertEqual(sspai.brief_state([self.issue("2026-10-09")], monday), "pending")

    def test_a_weekday_without_an_issue_is_suspended(self):
        monday = datetime(2026, 10, 12, 11, 0)
        self.assertEqual(sspai.brief_state([self.issue("2026-10-09")], monday), "suspended")

# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

class SspaiPanel(unittest.TestCase):
    def setUp(self):
        parent = _parent()
        self.children = sspai._split_issue(parent, _issue_body(), "modern", "today")
        for i, child in enumerate(self.children, 1):
            child["id"] = f"sspai:issue:{i}"
            child["nature"] = "news"
        self.others = [base.item("sspai", "少数派", "一篇长文", "https://sspai.com/post/1",
                                 extra={"collector": "api_v1"})]

    def test_stories_are_grouped_under_their_issue(self):
        html = render_digest.render_sspai_panel(self.children + self.others,
                                                {"brief_state": "published"})
        self.assertIn("派早报", html)
        self.assertIn("近期文章", html)
        self.assertIn("一篇长文", html)

    def test_each_story_gets_its_own_anchor(self):
        html = render_digest.render_sspai_panel(self.children, {"brief_state": "published"})
        for child in self.children:
            self.assertIn(f'id="it-{child["id"]}"', html)

    def test_the_issue_head_is_not_an_anchor(self):
        """The issue is a grouping, not an item, so it must not take an id that
        a citation could collide with."""
        html = render_digest.render_sspai_panel(self.children, {"brief_state": "published"})
        self.assertEqual(html.count('class="feat-h rv"'), 1)
        self.assertNotIn('class="feat-h rv" id=', html)

    def test_no_brief_on_a_weekend_says_so(self):
        html = render_digest.render_sspai_panel(self.others, {"brief_state": "suspended"})
        self.assertIn("今日停稿", html)
        self.assertNotIn("尚未发布", html)

    def test_a_weekday_before_publication_says_something_else(self):
        html = render_digest.render_sspai_panel(self.others, {"brief_state": "pending"})
        self.assertIn("尚未发布", html)
        self.assertNotIn("今日停稿", html)

    def test_the_fallback_run_shows_no_absence_notice(self):
        html = render_digest.render_sspai_panel(self.others, {"brief_state": "unknown"})
        self.assertNotIn("停稿", html)
        self.assertNotIn("尚未发布", html)

# --------------------------------------------------------------------------
# digest summary stage
# --------------------------------------------------------------------------

class DigestSummary(unittest.TestCase):
    def validate(self, decisions, known=("a:1", "b:2")):
        return ai_stage.validate_digest_decisions(decisions, set(known))

    def test_keywords_are_sorted_by_weight(self):
        digest, _ = self.validate({"keywords": [
            {"text": "小", "weight": 10}, {"text": "大", "weight": 90}]})
        self.assertEqual([k["text"] for k in digest["keywords"]], ["大", "小"])

    def test_a_weight_is_clamped(self):
        digest, _ = self.validate({"keywords": [{"text": "甲", "weight": 500},
                                                {"text": "乙", "weight": -20}]})
        self.assertEqual([k["weight"] for k in digest["keywords"]], [100, 0])

    def test_a_missing_weight_defaults_to_the_middle(self):
        digest, _ = self.validate({"keywords": [{"text": "甲"}]})
        self.assertEqual(digest["keywords"][0]["weight"], 50)

    def test_a_bare_string_keyword_is_accepted(self):
        digest, _ = self.validate({"keywords": ["甲"]})
        self.assertEqual(digest["keywords"], [{"text": "甲", "weight": 50}])

    def test_duplicate_keywords_are_rejected(self):
        digest, problems = self.validate({"keywords": [{"text": "甲"}, {"text": "甲"}]})
        self.assertEqual(len(digest["keywords"]), 1)
        self.assertTrue(any("repeated" in p for p in problems))

    def test_too_many_keywords_are_trimmed(self):
        digest, problems = self.validate(
            {"keywords": [{"text": f"词{i}", "weight": i} for i in range(30)]})
        self.assertEqual(len(digest["keywords"]), ai_stage.KEYWORD_LIMIT)
        self.assertTrue(any("trimmed" in p for p in problems))

    def test_a_long_summary_is_trimmed_not_rejected(self):
        """It is the only global view of the day; losing it would empty the
        panel."""
        digest, problems = self.validate({"summary": "长" * 2000})
        self.assertEqual(len(digest["summary"]), ai_stage.DIGEST_SUMMARY_LIMIT)
        self.assertTrue(any("trimmed" in p for p in problems))

    def test_an_unknown_audit_id_is_rejected(self):
        digest, problems = self.validate({"summary": "s", "source_ids": ["nope"]})
        self.assertEqual(digest["source_ids"], [])
        self.assertTrue(any("unknown id" in p for p in problems))

    def test_the_summary_survives_a_bad_audit_id(self):
        digest, _ = self.validate({"summary": "s", "source_ids": ["a:1", "nope"]})
        self.assertEqual(digest["summary"], "s")
        self.assertEqual(digest["source_ids"], ["a:1"])

    def test_an_alias_resolves_in_the_audit_list(self):
        digest, problems = ai_stage.validate_digest_decisions(
            {"source_ids": ["old:1"]}, {"a:1"}, {"old:1": "a:1"})
        self.assertEqual(digest["source_ids"], ["a:1"])
        self.assertEqual(problems, [])

    def test_the_old_schema_is_rejected_outright(self):
        """A /1 file on the same day would otherwise pass the day gate and be
        read as if it had a summary."""
        digest, problems = self.validate({"schema": ai_stage.DIGEST_SCHEMA_V1,
                                          "headline": "old", "highlights": []})
        self.assertEqual(digest, {})
        self.assertTrue(any("old schema" in p for p in problems))

class SummaryRendering(unittest.TestCase):
    def render(self, digest, categories=None):
        return render_digest.render({"sspai": {"items": [], "status": {}, "day": DAY}},
                                    digest, Path("/tmp/x.html"), False, {}, categories)

    def test_the_summary_and_keywords_are_drawn(self):
        html = self.render({"summary": "今天的面貌。",
                            "keywords": [{"text": "大词", "weight": 90},
                                         {"text": "小词", "weight": 10}]})
        self.assertIn("今天的面貌。", html)
        self.assertIn("大词", html)
        self.assertIn("--w:90", html)
        self.assertIn("--w:10", html)

    def test_the_old_sections_are_gone(self):
        html = self.render({"summary": "s", "keywords": []})
        for dead in ('id="dg-lead"', 'id="dg-items"', "要点", "脉络"):
            self.assertNotIn(dead, html)

    def test_the_rail_lists_the_summary(self):
        html = self.render({"summary": "s", "keywords": [{"text": "甲", "weight": 5}]})
        self.assertIn('data-target="dg-summary"', html)
        self.assertIn("汇总", html)

    def test_keywords_alone_still_produce_the_panel(self):
        html = self.render({"keywords": [{"text": "甲", "weight": 5}]})
        self.assertIn('data-tab="digest"', html)
        self.assertIn('id="dg-summary"', html)

    def test_neither_input_means_no_panel(self):
        self.assertNotIn('data-tab="digest"', self.render({}))

# --------------------------------------------------------------------------
# the digest item summary limit
# --------------------------------------------------------------------------

class ClassifySummaryLimit(unittest.TestCase):
    def test_a_digest_item_keeps_more_of_its_summary(self):
        """简讯 holds several stories in one item, so the normal limit would
        hide most of them from the classifier."""
        # base.item caps summary at 1200, which is above the classify limit but
        # below the digest one, so set the field directly.
        plain = base.item("sspai", "少数派", "标题", "https://sspai.com/1",
                          extra={"collector": "api_v1"})
        plain["summary"] = "甲" * 2000
        digest = base.item("sspai", "少数派", "简讯", "https://sspai.com/2",
                           extra={"collector": "api_v1", "is_digest": True})
        digest["summary"] = "乙" * 2000
        self.assertEqual(len(ai_stage.slim_classify_item(plain)["summary"]),
                         ai_stage.CLASSIFY_SUMMARY_LIMIT)
        self.assertEqual(len(ai_stage.slim_classify_item(digest)["summary"]),
                         ai_stage.DIGEST_ITEM_SUMMARY_LIMIT)

if __name__ == "__main__":
    unittest.main()

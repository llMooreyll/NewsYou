"""Offline tests for the 少数派 split and the digest summary stage.

    ./.venv/bin/python -m unittest discover -s tests -v

No network: the article HTML and the API payloads are hand-written fixtures in
the shape the real ones have, so the splitting rules are exercised without
depending on what 少数派 published today.
"""
from __future__ import annotations

import sys
import unittest
import unittest.mock
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import ai_stage  # noqa: E402
import config  # noqa: E402
import render_digest  # noqa: E402
from collectors import base, registry, sspai  # noqa: E402
from collectors.base import CST  # noqa: E402

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

    def test_a_listing_that_did_not_reach_the_brief_is_not_a_suspension(self):
        """The bug this exists for: 派早报 publishes at 08:31 and the site keeps
        publishing after it, so a window sized for articles can stop above the
        brief. Reporting 今日停稿 then states, as a fact about the publisher,
        something that is really a fact about our request — measured on
        2026-10-15, where a 20-item window missed it and the page said the
        brief had stopped."""
        monday = datetime(2026, 10, 12, 11, 0)
        self.assertEqual(
            sspai.brief_state([self.issue("2026-10-09")], monday, covered=False), "missing")

    def test_covering_the_window_still_reports_a_real_suspension(self):
        monday = datetime(2026, 10, 12, 11, 0)
        self.assertEqual(
            sspai.brief_state([self.issue("2026-10-09")], monday, covered=True), "suspended")

    def test_a_found_brief_wins_over_coverage(self):
        """Coverage only decides what an absence means; a brief in hand is
        published whatever the window did."""
        monday = datetime(2026, 10, 12, 11, 0)
        self.assertEqual(
            sspai.brief_state([self.issue("2026-10-12")], monday, covered=False), "published")


class BriefListing(unittest.TestCase):
    """The listing has to guarantee the brief a place, not compete for one."""

    def _article(self, ident, day, hour, post_type=1):
        return {"id": ident, "title": f"文{ident}", "post_type": post_type,
                "released_at": int(datetime.fromisoformat(
                    f"{day}T{hour:02d}:00:00+08:00").timestamp()),
                "summary": "", "author": {"nickname": "a"}, "tags": [],
                "custom_tags": [], "keywords": [], "views_count": 0}

    def _listing(self, articles):
        return {"list": articles, "total": 999}

    def test_a_brief_below_the_window_is_still_found(self):
        """22 articles published after the brief pushed it to position 23 on
        2026-10-15, outside the 20-item window. The wide window reaches back
        days, so it also settles that the brief really is absent from the small
        one rather than merely out of its reach."""
        articles = [self._article(200 + i, "2026-10-15", 10) for i in range(22)]
        articles.append(self._article(116193, "2026-10-15", 8, post_type=3))
        articles.append(self._article(100, "2026-10-11", 9))    # 4 天前，撑开回溯
        calls = []

        def fetch(url, **kw):
            calls.append(url)
            limit = int(url.split("limit=")[1].split("&")[0])
            return self._listing(articles[:limit])

        now = datetime(2026, 10, 15, 16, 0, tzinfo=CST)
        with unittest.mock.patch.object(sspai.base, "fetch_json", side_effect=fetch):
            items, covered = sspai._from_api(20, now=now)
        self.assertEqual(len([e for e in items if sspai._is_brief(e)]), 1)
        self.assertEqual(len(items), 20)
        self.assertTrue(covered)
        # the first request is the small one; only the miss widens it
        self.assertEqual(len(calls), 2)
        self.assertIn("limit=20", calls[0])
        self.assertIn(f"limit={sspai.API_WIDE_WINDOW}", calls[1])

    def test_one_request_when_the_brief_is_already_there(self):
        articles = [self._article(1, "2026-10-15", 10)]
        articles.append(self._article(116193, "2026-10-15", 8, post_type=3))
        calls = []

        def fetch(url, **kw):
            calls.append(url)
            return self._listing(articles)

        now = datetime(2026, 10, 15, 16, 0, tzinfo=CST)
        with unittest.mock.patch.object(sspai.base, "fetch_json", side_effect=fetch):
            items, _ = sspai._from_api(20, now=now)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len([e for e in items if sspai._is_brief(e)]), 1)

    def test_no_widening_before_the_brief_is_due(self):
        articles = [self._article(1, "2026-10-15", 3)]
        calls = []

        def fetch(url, **kw):
            calls.append(url)
            return self._listing(articles)

        early = datetime(2026, 10, 15, 7, 0, tzinfo=CST)
        with unittest.mock.patch.object(sspai.base, "fetch_json", side_effect=fetch):
            _, covered = sspai._from_api(20, now=early)
        self.assertEqual(len(calls), 1)
        # The window does reach back past the brief hour here; what matters is
        # that nothing was widened, and that the state is still "pending"
        # rather than an absence.
        self.assertEqual(sspai.brief_state([], early, covered=covered), "pending")

    def test_no_widening_on_a_weekend(self):
        """There is no brief on a Saturday, so looking harder would only add a
        request to every weekend run."""
        articles = [self._article(1, "2026-10-17", 3)]
        calls = []

        def fetch(url, **kw):
            calls.append(url)
            return self._listing(articles)

        saturday = datetime(2026, 10, 17, 16, 0, tzinfo=CST)
        with unittest.mock.patch.object(sspai.base, "fetch_json", side_effect=fetch):
            sspai._from_api(20, now=saturday)
        self.assertEqual(len(calls), 1)

    def test_a_full_window_is_reported_as_truncated(self):
        """A full window means more items exist beyond it, so its oldest entry
        is a cut-off rather than the end of the list."""
        articles = [self._article(1, "2026-10-15", 10), self._article(2, "2026-10-15", 9)]
        with unittest.mock.patch.object(sspai.base, "fetch_json",
                                        return_value=self._listing(articles)):
            _, full = sspai._api_window(2)
        self.assertTrue(full)
        with unittest.mock.patch.object(sspai.base, "fetch_json",
                                        return_value=self._listing(articles[:1])):
            _, full = sspai._api_window(2)
        self.assertFalse(full)

    def test_a_window_stopping_above_the_brief_hour_is_not_covered(self):
        items = [{"published_at": "2026-10-15T09:55:00+08:00"}]
        now = datetime(2026, 10, 15, 16, 0, tzinfo=CST)
        self.assertFalse(sspai._reaches_brief_window(items, now))

    def test_a_window_reaching_the_previous_day_is_covered(self):
        items = [{"published_at": "2026-10-14T20:00:00+08:00"}]
        now = datetime(2026, 10, 15, 16, 0, tzinfo=CST)
        self.assertTrue(sspai._reaches_brief_window(items, now))

    def test_an_empty_window_is_not_covered(self):
        self.assertFalse(sspai._reaches_brief_window(
            [], datetime(2026, 10, 15, 16, 0, tzinfo=CST)))

    def test_the_rss_fallback_reports_no_coverage(self):
        """A feed carries no way to tell a missing brief from an unlooked-for
        one, so it must not claim the brief stopped."""
        with unittest.mock.patch.object(sspai.feedmod, "feed_items", return_value=[]):
            items, covered = sspai._from_feed(20, "modern")
        self.assertEqual(items, [])
        self.assertFalse(covered)

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

    def test_a_listing_that_missed_the_brief_says_so_without_blaming_the_site(self):
        """`missing` is not `suspended`: the API answered and did not reach back
        far enough, which is a fact about the request. Saying 今日停稿 there
        would report our own gap as the publisher's decision."""
        html = render_digest.render_sspai_panel(self.others, {"brief_state": "missing"})
        self.assertIn("未能确认", html)
        self.assertNotIn("停稿", html)
        self.assertNotIn("周末", html)

    def test_the_roundup_is_one_block(self):
        """简讯 is drawn as a single block even though each notice is its own
        item: the classification needs them apart, the page reads better
        together."""
        html = render_digest.render_sspai_panel(self.children, {"brief_state": "published"})
        self.assertEqual(html.count('class="digest-item"'), 2)
        self.assertIn("简讯", html)
        self.assertNotIn("三星即将推出的首款智能眼镜规格披露</h4>", html)

    def test_each_notice_keeps_its_own_anchor(self):
        """The chip from the summary has to land on the notice it names."""
        html = render_digest.render_sspai_panel(self.children, {"brief_state": "published"})
        for child in self.children:
            self.assertIn(f'id="it-{child["id"]}"', html)

    def test_the_roundup_is_one_rail_entry(self):
        html = render_digest.render_sspai_panel(self.children, {"brief_state": "published"})
        self.assertIn('data-target="sp-digest-3"', html)

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
        total = sum(len(p["text"]) for p in digest["summary"])
        self.assertEqual(total, ai_stage.DIGEST_SUMMARY_LIMIT)
        self.assertTrue(any("trimmed" in p for p in problems))

    def test_an_unknown_paragraph_id_is_rejected(self):
        digest, problems = self.validate(
            {"summary": [{"text": "s", "source_ids": ["nope"]}]})
        self.assertEqual(digest["summary"][0]["source_ids"], [])
        self.assertTrue(any("unknown source id" in p for p in problems))

    def test_the_summary_survives_a_bad_paragraph_id(self):
        digest, _ = self.validate(
            {"summary": [{"text": "s", "source_ids": ["a:1", "nope"]}]})
        self.assertEqual([p["text"] for p in digest["summary"]], ["s"])
        self.assertEqual(digest["summary"][0]["source_ids"], ["a:1"])

    def test_an_alias_resolves_in_a_paragraph(self):
        digest, problems = ai_stage.validate_digest_decisions(
            {"summary": [{"text": "s", "source_ids": ["old:1"]}]},
            {"a:1"}, {"old:1": "a:1"})
        self.assertEqual(digest["summary"][0]["source_ids"], ["a:1"])
        self.assertEqual(problems, [])

    def test_a_top_level_id_list_is_a_fallback_not_the_mechanism(self):
        """The page draws the per-paragraph ids. A day-wide list from an older
        file still validates, but says so: nothing would render it."""
        digest, problems = self.validate({"summary": "s", "source_ids": ["a:1"]})
        self.assertEqual([p["text"] for p in digest["summary"]], ["s"])
        self.assertTrue(any("per paragraph" in p for p in problems), problems)

    def test_paragraph_ids_are_capped(self):
        digest, problems = self.validate(
            {"summary": [{"text": "s", "source_ids": ["a:1", "b:2", "a:1"]}]})
        self.assertEqual(digest["summary"][0]["source_ids"], ["a:1", "b:2"])

    def test_a_paragraph_with_no_ids_is_fine(self):
        digest, problems = self.validate({"summary": [{"text": "s"}]})
        self.assertEqual(digest["summary"][0]["source_ids"], [])
        self.assertEqual([p for p in problems if "source id" in p], [])

    def test_the_old_schema_is_rejected_outright(self):
        """A /1 file on the same day would otherwise pass the day gate and be
        read as if it had a summary."""
        digest, problems = self.validate({"schema": ai_stage.DIGEST_SCHEMA_V1,
                                          "headline": "old", "highlights": []})
        self.assertEqual(digest, {})
        self.assertTrue(any("old schema" in p for p in problems))

class SummaryParagraphs(unittest.TestCase):
    """`summary` is a list of paragraphs, each naming the words it is about."""

    def validate(self, value, known=("a:1",)):
        return ai_stage.validate_digest_decisions({"summary": value}, set(known))

    def texts(self, digest):
        return [p["text"] for p in digest["summary"]]

    def test_a_plain_string_still_works(self):
        """A hand-written decisions file should not have to change shape."""
        digest, problems = self.validate("一段话。")
        self.assertEqual(self.texts(digest), ["一段话。"])
        self.assertEqual(digest["summary"][0]["keywords"], [])
        self.assertEqual(problems, [])

    def test_paragraphs_keep_their_order(self):
        digest, _ = self.validate([{"text": "第一段。"}, {"text": "第二段。"}])
        self.assertEqual(self.texts(digest), ["第一段。", "第二段。"])

    def test_each_paragraph_keeps_its_own_keywords(self):
        digest, _ = self.validate([{"text": "甲和乙。", "keywords": ["甲", "乙"]},
                                   {"text": "丙。", "keywords": ["丙"]}])
        self.assertEqual(digest["summary"][0]["keywords"], ["甲", "乙"])
        self.assertEqual(digest["summary"][1]["keywords"], ["丙"])

    def test_paragraph_keywords_are_capped_and_deduped(self):
        digest, _ = self.validate([{"text": "t", "keywords":
                                    ["一", "二", "三", "四", "五", "六", "一"]}])
        words = digest["summary"][0]["keywords"]
        self.assertEqual(len(words), ai_stage.DIGEST_PARAGRAPH_KEYWORDS)
        self.assertEqual(len(set(words)), len(words))

    def test_an_empty_paragraph_is_skipped(self):
        digest, problems = self.validate([{"text": "有内容"}, {"text": "   "}])
        self.assertEqual(self.texts(digest), ["有内容"])
        self.assertTrue(any("empty paragraph" in p for p in problems))

    def test_too_many_paragraphs_are_trimmed(self):
        digest, problems = self.validate([{"text": f"第{i}段。"} for i in range(10)])
        self.assertEqual(len(digest["summary"]), ai_stage.DIGEST_PARAGRAPH_LIMIT)
        self.assertTrue(any("paragraphs" in p for p in problems))

    def test_the_total_length_is_bounded_across_paragraphs(self):
        digest, problems = self.validate([{"text": "长" * 900}, {"text": "长" * 900}])
        self.assertLessEqual(sum(len(p["text"]) for p in digest["summary"]),
                             ai_stage.DIGEST_SUMMARY_LIMIT)
        self.assertTrue(any("trimmed" in p for p in problems))

    def test_a_paragraph_that_is_not_an_object_is_rejected(self):
        digest, problems = self.validate([{"text": "好"}, 42])
        self.assertEqual(self.texts(digest), ["好"])
        self.assertTrue(any("not an object" in p for p in problems))

class KeywordMarking(unittest.TestCase):
    """The keywords are marked inside their own paragraph.

    This is what ties the word cloud to the prose; a word the model paraphrased
    away is skipped rather than treated as an error.
    """

    def mark(self, text, words):
        return render_digest._mark_keywords(text, words)

    def test_a_keyword_is_wrapped_where_it_occurs(self):
        self.assertEqual(self.mark("苹果发布了新品。", ["苹果"]),
                         '<mark class="kw">苹果</mark>发布了新品。')

    def test_only_the_first_occurrence_is_marked(self):
        out = self.mark("苹果，又是苹果。", ["苹果"])
        self.assertEqual(out.count('<mark class="kw">'), 1)

    def test_a_missing_keyword_is_skipped_not_an_error(self):
        self.assertEqual(self.mark("苹果发布了新品。", ["安卓"]), "苹果发布了新品。")

    def test_a_longer_keyword_wins_over_a_shorter_one(self):
        out = self.mark("AI 应用增长很快。", ["AI", "AI 应用"])
        self.assertEqual(out.count('<mark class="kw">'), 1)
        self.assertIn(">AI 应用</mark>", out)

    def test_overlapping_keywords_are_not_nested(self):
        out = self.mark("AI 应用和 AI 模型。", ["AI", "AI 应用"])
        self.assertNotIn('<mark class="kw"><mark', out)
        self.assertEqual(out.count("<mark"), out.count("</mark>"))

    def test_html_in_the_text_is_escaped_before_marking(self):
        out = self.mark("a < b & c", ["b"])
        self.assertIn("&lt;", out)
        self.assertIn('<mark class="kw">b</mark>', out)

    def test_a_keyword_with_markup_characters_is_matched_safely(self):
        out = self.mark("A & B 上升", ["A & B"])
        self.assertIn('<mark class="kw">A &amp; B</mark>', out)

    def test_no_keywords_means_no_marking(self):
        self.assertEqual(self.mark("一段话。", []), "一段话。")
        self.assertEqual(self.mark("一段话。", None), "一段话。")

class SummaryRendering(unittest.TestCase):
    def render(self, digest, categories=None):
        return render_digest.render({"sspai": {"items": [], "status": {}, "day": DAY}},
                                    digest, Path("/tmp/x.html"), False, {}, categories)

    def test_the_summary_and_keywords_are_drawn(self):
        html = self.render({"summary": [{"text": "今天的面貌。"}],
                            "keywords": [{"text": "大词", "weight": 90},
                                         {"text": "小词", "weight": 10}]})
        self.assertIn("今天的面貌。", html)
        self.assertIn("大词", html)
        self.assertIn("--w:90", html)
        self.assertIn("--w:10", html)

    def test_the_old_sections_are_gone(self):
        html = self.render({"summary": [{"text": "s"}], "keywords": []})
        for dead in ('id="dg-lead"', 'id="dg-items"', "要点", "脉络"):
            self.assertNotIn(dead, html)

    def test_the_rail_lists_the_summary(self):
        html = self.render({"summary": [{"text": "s"}],
                            "keywords": [{"text": "甲", "weight": 5}]})
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

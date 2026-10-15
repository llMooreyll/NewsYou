"""Offline tests for the V2EX and Reddit collectors.

    ./.venv/bin/python -m unittest discover -s tests -v

No network: both parsers are exercised against hand-written fixtures shaped
like the real responses. The fixtures are trimmed, but the markup they use is
copied from the live pages, because that markup is the thing being parsed.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import config  # noqa: E402
from collectors import reddit, registry, v2ex  # noqa: E402
from collectors import telegram  # noqa: E402

# --------------------------------------------------------------------------
# V2EX fixtures
# --------------------------------------------------------------------------

def _row(topic_id: int, replies: int, title: str, node: str, node_title: str,
         author: str = "someone", when: str = "2026-10-14 19:26:13 +08:00",
         votes: int = 0) -> str:
    return (
        '<div class="cell item"><table><tr>'
        f'<td><a href="/member/{author}"><img class="avatar" /></a></td>'
        '<td><span class="item_title">'
        f'<a href="/t/{topic_id}#reply{replies}" class="topic-link" '
        f'id="topic-link-{topic_id}">{title}</a></span>'
        '<div class="sep5"></div>'
        '<span class="topic_info">'
        f'<div class="votes"><li class="fa fa-chevron-up"></li> &nbsp;{votes} &nbsp;&nbsp; </div>'
        f'<a class="node" href="/go/{node}">{node_title}</a> &nbsp;•&nbsp; '
        f'<strong><a href="/member/{author}">{author}</a></strong> &nbsp;•&nbsp; '
        f'<span title="{when}">18 mins ago</span> &nbsp;•&nbsp; '
        f'Lastly replied by <strong><a href="/member/other">other</a></strong></span>'
        '</td>'
        f'<td><a href="/t/{topic_id}#reply{replies}" class="count_livid">{replies}</a></td>'
        '</tr></table></div>')

V2EX_PAGE = ('<html><head><title>V2EX</title></head><body>'
             + _row(1248653, 160, "今年买到的最值的东西推荐", "share", "分享发现",
                    "eventlooped", votes=3)
             + _row(1248583, 149, "muse 30 亿免费 token", "programmer", "程序员", "frank1256")
             + _row(1248572, 147, "该不该割肉，房子的事", "qna", "问与答", "wuming99911")
             + '</body></html>')

class V2exParsing(unittest.TestCase):
    def setUp(self):
        self.items = v2ex.parse_hot(V2EX_PAGE)

    def test_every_row_is_parsed(self):
        self.assertEqual(len(self.items), 3)

    def test_the_reply_count_comes_from_the_anchor(self):
        self.assertEqual([e["extra"]["replies"] for e in self.items], [160, 149, 147])

    def test_the_node_is_kept_with_its_display_name(self):
        self.assertEqual(self.items[0]["extra"]["node_name"], "share")
        self.assertEqual(self.items[0]["extra"]["node_title"], "分享发现")

    def test_the_url_points_at_the_topic(self):
        self.assertEqual(self.items[0]["url"], "https://www.v2ex.com/t/1248653")

    def test_the_time_is_the_last_reply_not_the_post(self):
        """A reply-count board is about activity, so the timestamp shown has to
        be the last reply and the provenance has to say so."""
        self.assertTrue(self.items[0]["published_at"].startswith("2026-10-14T19:26"))
        self.assertEqual(self.items[0]["date_source"], "last_reply")

    def test_votes_are_read_when_present(self):
        self.assertEqual(self.items[0]["extra"]["votes"], 3)

    def test_a_page_without_rows_yields_nothing(self):
        self.assertEqual(v2ex.parse_hot("<html><body>no topics</body></html>"), [])

    def test_an_empty_title_is_skipped(self):
        page = V2EX_PAGE + _row(1, 5, "", "qna", "问与答")
        self.assertEqual(len(v2ex.parse_hot(page)), 3)

    def test_collect_sorts_by_replies_whatever_the_page_order(self):
        """The page is already sorted; re-sorting makes it an invariant rather
        than something to trust."""
        shuffled = ('<html><body>'
                    + _row(3, 10, "c", "qna", "问与答")
                    + _row(1, 99, "a", "qna", "问与答")
                    + _row(2, 50, "b", "qna", "问与答") + '</body></html>')
        with unittest.mock.patch.object(v2ex.base, "fetch", return_value=shuffled):
            items = v2ex.collect(limit=10)
        self.assertEqual([e["extra"]["replies"] for e in items], [99, 50, 10])

    def test_collect_honours_the_limit(self):
        with unittest.mock.patch.object(v2ex.base, "fetch", return_value=V2EX_PAGE):
            self.assertEqual(len(v2ex.collect(limit=2)), 2)

# --------------------------------------------------------------------------
# Reddit fixtures
# --------------------------------------------------------------------------

def _entry(post_id: str, title: str, link: str, sub: str = "godot",
           author: str = "someone") -> str:
    return (f'<entry><author><name>/u/{author}</name>'
            f'<uri>https://www.reddit.com/user/{author}</uri></author>'
            f'<category term="{sub}" label="r/{sub}"/>'
            f'<content type="html">&#32; submitted by &#32; '
            f'&lt;a href=&quot;https://www.reddit.com/user/{author}&quot;&gt; /u/{author} &lt;/a&gt; '
            f'&lt;br/&gt; &lt;span&gt;&lt;a href=&quot;{link}&quot;&gt;[link]&lt;/a&gt;&lt;/span&gt; '
            f'&lt;span&gt;&lt;a href=&quot;https://www.reddit.com/r/{sub}/comments/{post_id}/&quot;&gt;'
            f'[comments]&lt;/a&gt;&lt;/span&gt;</content>'
            f'<id>t3_{post_id}</id>'
            f'<link href="https://www.reddit.com/r/{sub}/comments/{post_id}/x/" />'
            f'<updated>2026-10-14T10:09:31+00:00</updated>'
            f'<published>2026-10-14T10:09:31+00:00</published>'
            f'<title>{title}</title></entry>')

FEED = ('<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        '<category term="godot" label="r/godot"/>'
        '<updated>2026-10-14T12:02:38+00:00</updated>'
        + _entry("1wtkfvz", "Dev snapshot: Godot 4.8 dev 7",
                 "https://godotengine.org/article/dev-snapshot-godot-4-8-dev-7/")
        + _entry("1x5nmww", "my solo-developed game", "https://v.redd.it/abc")
        + _entry("1x5nfwv", "Sad but true", "https://i.redd.it/x.jpg")
        + _entry("1x5a1qw", "gallery post", "https://www.reddit.com/gallery/1x5a1qw")
        + '</feed>')

class RedditMediaKind(unittest.TestCase):
    def test_a_real_article_is_not_media(self):
        self.assertEqual(reddit._media_kind("https://godotengine.org/article/x"), "")

    def test_reddit_video_and_image_hosts_are_recognised(self):
        self.assertEqual(reddit._media_kind("https://v.redd.it/abc"), "video")
        self.assertEqual(reddit._media_kind("https://i.redd.it/x.jpg"), "image")

    def test_a_reddit_gallery_is_its_own_kind(self):
        self.assertEqual(reddit._media_kind("https://www.reddit.com/gallery/1x"), "gallery")

    def test_a_self_post_is_recognised(self):
        self.assertEqual(reddit._media_kind("https://www.reddit.com/r/godot/comments/1x/"), "self")

class RedditExternalUrl(unittest.TestCase):
    def test_a_link_post_exposes_its_target(self):
        got = reddit._external_url(
            '<span><a href="https://godotengine.org/a">[link]</a></span>')
        self.assertEqual(got, "https://godotengine.org/a")

    def test_reddit_media_is_not_an_external_article(self):
        """v.redd.it is the post's own content, not something to go read."""
        self.assertIsNone(reddit._external_url('<a href="https://v.redd.it/x">[link]</a>'))
        self.assertIsNone(reddit._external_url('<a href="https://i.redd.it/x.jpg">[link]</a>'))

    def test_a_permalink_duplicate_is_dropped(self):
        self.assertIsNone(reddit._external_url(
            '<a href="https://www.reddit.com/r/godot/comments/1x/">[link]</a>'))

    def test_no_link_means_none(self):
        self.assertIsNone(reddit._external_url("submitted by /u/x"))
        self.assertIsNone(reddit._external_url(""))

    def test_a_non_http_target_is_refused(self):
        self.assertIsNone(reddit._external_url('<a href="javascript:x">[link]</a>'))

class RedditFeedParsing(unittest.TestCase):
    def setUp(self):
        self.entries = reddit.base_parse(FEED)

    def test_every_entry_is_parsed(self):
        self.assertEqual(len(self.entries), 4)

    def test_the_body_is_taken_from_content_not_summary(self):
        """Reddit's Atom puts the body in <content>, which the shared parser
        does not read; the collector normalises it rather than teaching the
        shared parser about one site."""
        self.assertTrue(all(e.get("summary_html") for e in self.entries))

    def test_titles_and_links_survive(self):
        self.assertEqual(self.entries[0]["title"], "Dev snapshot: Godot 4.8 dev 7")
        self.assertIn("/r/godot/comments/1wtkfvz/", self.entries[0]["link"])

class RedditCollect(unittest.TestCase):
    def test_a_feed_is_turned_into_items(self):
        with unittest.mock.patch.object(reddit.base, "fetch", return_value=FEED):
            items = reddit.collect(subreddits=["godot"], delay=0)
        self.assertEqual(len(items), 4)
        self.assertEqual(items[0]["extra"]["subreddit"], "godot")
        self.assertEqual(items[0]["extra"]["domain"], "godotengine.org")

    def test_media_posts_carry_their_kind_instead_of_a_domain(self):
        with unittest.mock.patch.object(reddit.base, "fetch", return_value=FEED):
            items = reddit.collect(subreddits=["godot"], delay=0)
        by_title = {e["title"]: e["extra"] for e in items}
        self.assertEqual(by_title["my solo-developed game"]["media_kind"], "video")
        self.assertEqual(by_title["my solo-developed game"]["domain"], "")
        self.assertEqual(by_title["Sad but true"]["media_kind"], "image")
        self.assertEqual(by_title["gallery post"]["media_kind"], "gallery")

    def test_an_empty_feed_is_reported_not_raised(self):
        """A 429 answers with an empty body rather than an error."""
        with unittest.mock.patch.object(reddit.base, "fetch", return_value=""):
            items = reddit.collect(subreddits=["godot"], delay=0)
        self.assertEqual(items, [])
        self.assertFalse(reddit.last_report()["subreddits"]["godot"]["ok"])

    def test_a_fetch_failure_does_not_stop_the_other_subreddits(self):
        def flaky(url, **kw):
            if "/r/broken/" in url:
                raise reddit.base.FetchError("boom")
            return FEED
        with unittest.mock.patch.object(reddit.base, "fetch", side_effect=flaky):
            items = reddit.collect(subreddits=["broken", "godot"], delay=0)
        report = reddit.last_report()["subreddits"]
        self.assertFalse(report["broken"]["ok"])
        self.assertTrue(report["godot"]["ok"])
        self.assertEqual(len(items), 4)

    def test_subreddits_are_fetched_one_after_another(self):
        """Reddit throttles at roughly a request a minute; fetching these in
        parallel would trip the limit and cost the whole source."""
        calls = []

        def record(url, **kw):
            calls.append(url)
            return FEED
        slept = []
        with unittest.mock.patch.object(reddit.base, "fetch", side_effect=record), \
             unittest.mock.patch.object(reddit.time, "sleep", side_effect=slept.append):
            reddit.collect(subreddits=["a", "b", "c"], delay=60)
        self.assertEqual(len(calls), 3)
        # one pause between each pair, not before the first
        self.assertEqual(slept, [60, 60])

class CollectorProgress(unittest.TestCase):
    """A slow source has to say what it is doing while it does it.

    These two are the ones that can sit silent for a minute or more, so they are
    the only collectors that take the callback.
    """

    def test_reddit_reports_each_subreddit_and_says_why_it_waits(self):
        seen = []
        with unittest.mock.patch.object(reddit.base, "fetch", return_value=FEED), \
             unittest.mock.patch.object(reddit.time, "sleep"):
            reddit.collect(subreddits=["a", "b"], delay=60, progress=seen.append)
        self.assertTrue(any("r/a" in line for line in seen), seen)
        self.assertTrue(any("r/b" in line for line in seen), seen)
        # The minute of waiting is the whole reason this source is slow, so the
        # line names the rate limit rather than just looking stuck.
        self.assertTrue(any("速率限制" in line for line in seen), seen)

    def test_reddit_works_without_the_callback(self):
        with unittest.mock.patch.object(reddit.base, "fetch", return_value=FEED), \
             unittest.mock.patch.object(reddit.time, "sleep"):
            self.assertTrue(reddit.collect(subreddits=["a"], delay=0))

    def test_telegram_counts_every_channel_and_names_the_failures(self):
        seen = []

        def walk(channel, pages, cursor):
            if channel == "bad":
                raise telegram.PreviewError("boom")
            return []

        with unittest.mock.patch.object(telegram, "walk_channel", side_effect=walk):
            telegram.collect(day="2026-10-15", channels=["a", "bad", "b"], workers=1,
                             progress=seen.append)
        self.assertEqual(len(seen), 3)
        self.assertTrue(any("3/3" in line for line in seen), seen)
        self.assertTrue(any("1 失败" in line for line in seen), seen)

    def test_a_failed_channel_is_kept_in_the_report_not_printed(self):
        """Eighteen identical failures is one problem; the caller folds them."""
        import io
        import contextlib

        def walk(channel, pages, cursor):
            raise telegram.PreviewError("same error")

        log = io.StringIO()
        with unittest.mock.patch.object(telegram, "walk_channel", side_effect=walk), \
             contextlib.redirect_stderr(log):
            telegram.collect(day="2026-10-15", channels=["a", "b"], workers=1)
        self.assertNotIn("same error", log.getvalue())
        report = telegram.last_report()
        self.assertEqual([r["error"] for r in report["channels"].values()],
                         ["same error", "same error"])

    def test_only_collectors_that_accept_progress_receive_it(self):
        import collect
        self.assertTrue(collect._accepts_progress(telegram))
        self.assertTrue(collect._accepts_progress(reddit))
        # v2ex is one request for the whole board; a progress line would never
        # be read before it finished.
        self.assertFalse(collect._accepts_progress(v2ex))


class NewSourcesRegistered(unittest.TestCase):
    def test_both_are_registered_and_ordered(self):
        for name in ("v2ex", "reddit"):
            self.assertIn(name, registry.SOURCES)
            self.assertIn(name, registry.ORDER)
            self.assertIn(name, registry.SOURCE_LABELS)

    def test_both_are_discussion(self):
        cfg = config.load(ROOT / "config" / "config.toml")
        discussion = cfg.get("sources.nature.discussion")
        self.assertIn("v2ex", discussion)
        self.assertIn("reddit", discussion)

    def test_neither_is_a_fulltext_source(self):
        """V2EX hot rows are titles; Reddit feeds carry no selftext."""
        self.assertNotIn("v2ex", registry.FULLTEXT_SOURCES)
        self.assertNotIn("reddit", registry.FULLTEXT_SOURCES)

    def test_the_shipped_config_matches_the_defaults(self):
        cfg = config.load(ROOT / "config" / "config.toml")
        self.assertEqual(cfg.get("v2ex.sort"), config.defaults()["v2ex"]["sort"])
        self.assertEqual(cfg.get("reddit.subreddits"),
                         config.defaults()["reddit"]["subreddits"])
        self.assertEqual(cfg.get("reddit.delay"), config.defaults()["reddit"]["delay"])

    def test_reddit_is_switched_off_but_still_registered(self):
        """It works; it is just two minutes of waiting per run. The collector
        and its board both stay, so removing one name turns it back on."""
        cfg = config.load(ROOT / "config" / "config.toml")
        self.assertIn("reddit", cfg.get("sources.disabled"))
        self.assertIn("weibo", cfg.get("sources.disabled"))
        self.assertNotIn("v2ex", cfg.get("sources.disabled"))
        self.assertIn("reddit", registry.SOURCES)

    def test_the_day_choice_and_quiet_channels_live_in_the_report(self):
        """Telegram used to print both as it went, which put raw text on top of
        the live progress line. The caller phrases them from the report now."""
        import contextlib
        import io

        def walk(channel, pages, cursor):
            return []

        log = io.StringIO()
        with unittest.mock.patch.object(telegram, "walk_channel", side_effect=walk), \
             contextlib.redirect_stderr(log):
            telegram.collect(day="2026-10-15", channels=["a", "b"], workers=1)
        self.assertEqual(log.getvalue(), "")
        report = telegram.last_report()
        self.assertEqual(report["mode"], "fixed")
        self.assertEqual(report["channels"]["a"]["kept"], 0)


if __name__ == "__main__":
    unittest.main()

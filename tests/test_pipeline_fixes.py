"""Offline regression tests for collector / AI-stage / archive fixes.

    ./.venv/bin/python -m unittest discover -s tests -v

No network: every fetch is replaced with synthetic input.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import ssl
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from collectors import base, linuxdo, telegram as tg  # noqa: E402
import ai_apply  # noqa: E402
import ai_model  # noqa: E402
import ai_stage  # noqa: E402
import archive  # noqa: E402
import render_digest  # noqa: E402


# --------------------------------------------------------------------------
# collectors
# --------------------------------------------------------------------------

class LinuxdoHeadings(unittest.TestCase):
    def test_section_label_comes_from_the_page_heading(self):
        page = ('<section id="top-stories"><h2 class="t">头条</h2><span class="count">3 条</span>'
                '<a class="title" href="https://linux.do/t/1">A</a></section>'
                '<section id="ai"><h2>AI 前线</h2></section>')
        spans = linuxdo._section_spans(page)
        self.assertEqual([(s[0], s[1]) for s in spans], [("top-stories", "头条"), ("ai", "AI 前线")])


def _tg_page(channel: str, posts: list[tuple[int, str, str]]) -> str:
    """Minimal t.me/s page: (message id, iso time, inner text html)."""
    out = ['<div class="tgme_channel_info"></div>']
    for mid, when, inner in posts:
        out.append(
            f'<div class="tgme_widget_message" data-post="{channel}/{mid}">'
            f'<div class="tgme_widget_message_text js-message_text" dir="auto">{inner}</div>'
            f'<div class="tgme_widget_message_footer"><time datetime="{when}"></time></div></div>')
    return "".join(out)


class TelegramParsing(unittest.TestCase):
    def test_text_html_is_the_inside_of_the_text_div(self):
        nested = '<div class="tgme_widget_message_text js-message_text" dir="auto"><b>Title</b><br/>Body</div>'
        posts = tg.parse_posts(_tg_page("c", [(1, "2026-10-13T08:00:00+08:00", nested)]))
        self.assertFalse(posts[0]["text_html"].lstrip().startswith("<div"))
        self.assertIn("<b>Title</b>", posts[0]["text_html"])


class TelegramDay(unittest.TestCase):
    def setUp(self):
        now = datetime.now(base.CST).replace(hour=10, minute=0, second=0, microsecond=0)
        self.today = now.date().isoformat()
        self.yesterday = (now - timedelta(days=1)).date().isoformat()
        t, y = now.isoformat(), (now - timedelta(days=1)).isoformat()

        def fake_walk(channel, pages, before, **_):
            if channel == "broken":
                raise tg.PreviewError("no public preview content")
            data = {"busy": [(3, t, "<b>new</b><br/>x"), (2, y, "<b>old</b><br/>x")],
                    "quiet": [(9, y, "<b>yesterday only</b><br/>x")]}[channel]
            return tg.parse_posts(_tg_page(channel, data))
        self.patch = mock.patch.object(tg, "walk_channel", side_effect=fake_walk)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_auto_uses_one_day_for_every_channel(self):
        items = tg.collect(day="auto", channels=["busy", "quiet", "broken"])
        self.assertEqual({i["extra"]["day"] for i in items}, {self.today})
        self.assertEqual([i["extra"]["channel"] for i in items], ["busy"])

    def test_failures_and_empty_channels_are_reported(self):
        tg.collect(day="auto", channels=["busy", "quiet", "broken"])
        report = tg.last_report()
        self.assertEqual(report["day"], self.today)
        self.assertFalse(report["channels"]["broken"]["ok"])
        self.assertIn("no public preview", report["channels"]["broken"]["error"])
        self.assertEqual(report["channels"]["quiet"]["kept"], 0)
        self.assertEqual(report["channels"]["quiet"]["newest_day"], self.yesterday)


class TelegramImagePaths(unittest.TestCase):
    def test_paths_are_project_relative_whatever_the_cwd(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp, tempfile.TemporaryDirectory() as elsewhere:
            media = Path(tmp)
            posts = [{"message_id": 7, "photos": ["https://cdn.example/a.jpg"]}]
            cwd = os.getcwd()
            os.chdir(elsewhere)
            try:
                with mock.patch.object(tg, "fetch_bytes", return_value=(b"\xff\xd8\xff", "image/jpeg")), \
                     mock.patch.object(tg.time, "sleep"):
                    tg.download_images(posts, "chan", media)
            finally:
                os.chdir(cwd)
            path = posts[0]["local_images"][0]
            self.assertFalse(Path(path).is_absolute())
            self.assertTrue((ROOT / path).is_file())


class Tls(unittest.TestCase):
    def test_extra_trust_keeps_verification_on(self):
        ctx = base.extra_trust_context()
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)
        subjects = [dict(x[0] for x in c["subject"]).get("commonName") for c in ctx.get_ca_certs()]
        self.assertIn("Root YR", subjects)

    def test_no_unverified_context_left(self):
        self.assertFalse(hasattr(base, "RELAXED_TLS"))


# --------------------------------------------------------------------------
# archive
# --------------------------------------------------------------------------

class ArchiveSwap(unittest.TestCase):
    def test_replaces_existing_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "day").mkdir(); (root / "day" / "old").write_text("old")
            (root / "stage").mkdir(); (root / "stage" / "new").write_text("new")
            archive.install_snapshot(root / "stage", root / "day")
            self.assertTrue((root / "day" / "new").exists())
            self.assertEqual(sorted(p.name for p in root.iterdir()), ["day"])

    def test_failed_install_keeps_the_old_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "day").mkdir(); (root / "day" / "old").write_text("old")
            (root / "stage").mkdir()
            real = Path.rename

            def flaky(self, target):
                if self.name == "stage":
                    raise OSError("disk full")
                return real(self, target)
            with mock.patch.object(Path, "rename", flaky):
                with self.assertRaises(OSError):
                    archive.install_snapshot(root / "stage", root / "day")
            self.assertEqual((root / "day" / "old").read_text(), "old")


# --------------------------------------------------------------------------
# AI stage
# --------------------------------------------------------------------------

def _item(source, title, url, **extra):
    return base.item(source, source, title, url, extra=extra)


def _tg(channel, mid, text, images=()):
    entry = base.item("telegram", f"Telegram · @{channel}", text.split("\n")[0],
                      f"https://t.me/{channel}/{mid}", kind="message", summary=text,
                      extra={"channel": channel, "message_id": mid, "text": text,
                             "local_images": list(images), "photos": [], "media": []})
    return entry


class ItemIds(unittest.TestCase):
    def test_same_title_different_url_gets_different_ids(self):
        a = _item("geekpark", "早知道", "https://www.geekpark.net/news/1")
        b = _item("geekpark", "早知道", "https://www.geekpark.net/news/2")
        self.assertNotEqual(ai_stage.item_id(a), ai_stage.item_id(b))

    def test_title_edit_keeps_the_id(self):
        a = _item("sspai", "Old title", "https://sspai.com/post/1?utm_source=rss")
        b = _item("sspai", "New title", "https://sspai.com/post/1")
        self.assertEqual(ai_stage.item_id(a), ai_stage.item_id(b))

    def test_old_ids_still_resolve(self):
        entry = _item("zhihu", "问题", "https://www.zhihu.com/question/1")
        index, aliases = ai_stage.build_item_index({"zhihu": [entry]})
        old = ai_stage.legacy_item_id(entry)
        digest, problems = ai_stage.validate_digest_decisions(
            {"highlights": [{"item_ids": [old], "title": "t", "summary": "s"}]}, set(index), aliases)
        self.assertEqual(problems, [])
        self.assertEqual(digest["highlights"][0]["item_ids"], [ai_stage.item_id(entry)])


class TelegramMerge(unittest.TestCase):
    def setUp(self):
        self.a = _tg("a", 1, "Title A\n\nbody a", images=["data/x/1.jpg"])
        self.b = _tg("b", 2, "Title B\n\nbody b", images=["data/x/2.jpg"])
        self.c = _tg("c", 3, "Title C")
        self.ids = [ai_stage.item_id(e) for e in (self.a, self.b, self.c)]

    def test_merge_rejects_a_member_that_was_dropped(self):
        a, b, c = self.ids
        actions, problems = ai_stage.validate_telegram_decisions({"actions": [
            {"op": "drop", "id": c, "canonical_id": a},
            {"op": "merge", "primary_id": a, "merge_ids": [b, c], "title": "T", "summary": "S"},
        ]}, set(self.ids))
        merge = [x for x in actions if x["op"] == "merge"][0]
        self.assertEqual(merge["merge_ids"], [b])
        self.assertTrue(any("dropped" in p for p in problems))

    def test_merged_item_keeps_paragraphs_and_every_image(self):
        a, b, _ = self.ids
        actions, _ = ai_stage.validate_telegram_decisions({"actions": [
            {"op": "merge", "primary_id": a, "merge_ids": [b], "title": "T",
             "summary": "first   para\n\nsecond\npara"}]}, set(self.ids))
        out, _ = ai_stage.apply_telegram_decisions([self.a, self.b, self.c], actions)
        merged = [e for e in out if e.get("ai_merged")][0]
        self.assertEqual(merged["summary"], "first para\n\nsecond para")
        self.assertEqual(merged["extra"]["local_images"], ["data/x/1.jpg", "data/x/2.jpg"])
        self.assertEqual([m["channel"] for m in merged["extra"]["merged_sources"]], ["a", "b"])

        parts = render_digest.telegram_parts(merged)
        self.assertEqual(parts["body"].count("<p>"), 2)

    def test_an_id_of_the_wrong_type_is_rejected_not_raised(self):
        a, b, _ = self.ids
        actions, problems = ai_stage.validate_telegram_decisions({"actions": [
            {"op": "drop", "id": [a, b]},
            {"op": "spam", "id": [a]},
            {"op": "drop", "id": b, "canonical_id": {"id": a}},
            {"op": "merge", "primary_id": [a], "merge_ids": [b], "title": "T", "summary": "S"},
        ]}, set(self.ids))
        self.assertEqual([x["op"] for x in actions], ["drop"])
        self.assertIsNone(actions[0]["canonical_id"])
        self.assertEqual(len(problems), 4)


# --------------------------------------------------------------------------
# stage 2 end to end: only this run's data, only this day's decisions
# --------------------------------------------------------------------------

DAY, DAY_BEFORE, DAY_AFTER = "2026-10-13", "2026-10-12", "2026-10-14"


class StageTwoRun(unittest.TestCase):
    """data/ outlives a run, so stage 2 has to tell this run's files from the
    ones an earlier day left behind. Nothing here calls a model."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.collected, self.ai, self.out = (self.root / n for n in ("collected", "ai", "processed"))
        self.story = _item("hackernews", "Still on the front page", "https://example.com/a")
        self.collect("hackernews", [self.story])
        self.write(self.collected / "index.json", {"day": DAY, "sources": {}})

    def write(self, path: Path, payload) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def collect(self, name: str, items: list[dict], on: str = DAY) -> None:
        self.write(self.collected / f"{name}.json", {
            "source": name, "collected_at": f"{on}T08:00:00+08:00", "count": len(items),
            "status": {"updated_today": True}, "items": items})

    def decide(self, day: str) -> None:
        self.write(self.ai / "digest_decisions.json", {
            "schema": ai_stage.DIGEST_SCHEMA, "day": day, "headline": f"headline of {day}",
            "highlights": [{"item_ids": [ai_stage.item_id(self.story)], "title": "T",
                            "summary": "S", "importance": 90}]})

    def run_stage(self, backend: str = "none") -> str:
        argv = ["ai_apply.py", "--collected", str(self.collected), "--ai", str(self.ai),
                "--out", str(self.out), "--ai-backend", backend]
        log = io.StringIO()
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stderr(log):
            self.assertEqual(ai_apply.main(), 0)
        return log.getvalue()

    def panel(self):
        path = self.out / "digest.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def test_decisions_for_this_day_are_applied(self):
        self.decide(DAY)
        self.run_stage()
        self.assertEqual(self.panel()["headline"], f"headline of {DAY}")

    def test_decisions_from_another_day_are_not_applied(self):
        self.decide(DAY_BEFORE)
        log = self.run_stage()
        self.assertIsNone(self.panel())
        self.assertIn("not applied", log)

    def test_a_failed_model_call_does_not_fall_back_to_old_decisions(self):
        self.decide(DAY_BEFORE)
        with mock.patch.object(ai_model.shutil, "which", return_value="/fake/pi"), \
             mock.patch.object(ai_model, "_invoke", side_effect=ai_model.ModelError("timed out")) as call:
            self.run_stage(backend="pi")
        self.assertTrue(call.called)
        self.assertIsNone(self.panel())

    def test_a_fresh_answer_is_applied_whatever_day_the_model_claims(self):
        answer = json.dumps({"day": DAY_BEFORE, "headline": "fresh", "highlights": [
            {"item_ids": [ai_stage.item_id(self.story)], "title": "T", "summary": "S"}]})
        with mock.patch.object(ai_model.shutil, "which", return_value="/fake/pi"), \
             mock.patch.object(ai_model, "_invoke", return_value=answer):
            self.run_stage(backend="pi")
        self.assertEqual(self.panel()["headline"], "fresh")
        self.assertEqual(self.panel()["day"], DAY)

    def test_a_source_file_from_an_earlier_day_is_skipped(self):
        self.collect("weibo", [_item("weibo", "昨天的热搜", "https://s.weibo.com/x")], on=DAY_BEFORE)
        log = self.run_stage()
        self.assertFalse((self.out / "weibo.json").exists())
        self.assertTrue((self.out / "hackernews.json").exists())
        self.assertIn("weibo.json was collected on", log)

    def test_a_run_that_crosses_midnight_keeps_its_own_files(self):
        self.collect("weibo", [_item("weibo", "热搜", "https://s.weibo.com/x")], on=DAY_AFTER)
        self.run_stage()
        self.assertTrue((self.out / "weibo.json").exists())

    def test_output_of_an_earlier_run_is_removed(self):
        self.write(self.out / "weibo.json", {"source": "weibo", "items": []})
        self.write(self.out / "digest.json", {"cards": [{"title": "old"}]})
        self.write(self.out / "notes.json", {"mine": True})
        self.run_stage()
        self.assertEqual(sorted(p.name for p in self.out.glob("*.json")),
                         ["hackernews.json", "index.json", "notes.json"])

    def test_a_configured_feed_reaches_the_processed_data(self):
        post = base.item("my-blog", "示例博客", "A post", "https://blog.example/1")
        self.collect("my-blog", [post])
        self.run_stage()
        written = json.loads((self.out / "my-blog.json").read_text(encoding="utf-8"))
        self.assertEqual(written["source_name"], "示例博客")
        self.assertEqual(len(written["items"]), 1)
        sent = json.loads((self.ai / "digest_input.json").read_text(encoding="utf-8"))
        self.assertEqual(list(sent["sources"]), ["hackernews", "my-blog"])
        self.assertEqual(sent["sources"]["my-blog"]["source_name"], "示例博客")


class RendererReport(unittest.TestCase):
    def test_failed_and_empty_channels_show_in_the_rail(self):
        status = {"collector_report": {"channels": {
            "ok1": {"ok": True, "kept": 3}, "quiet": {"ok": True, "kept": 0, "newest_day": "2026-10-12"},
            "broken": {"ok": False, "error": "boom"}}}}
        note = render_digest._channel_note(status)
        self.assertIn("@broken", note)
        self.assertIn("@quiet", note)
        self.assertNotIn("@ok1", note)


if __name__ == "__main__":
    unittest.main()

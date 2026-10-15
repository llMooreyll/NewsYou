"""Offline tests for the run's progress output.

The live region moves the cursor and clears lines, so the only honest way to
test it is to interpret what it writes. `Screen` below is a terminal model that
understands exactly the sequences console.py emits — relative cursor moves, line
clears and newlines — and the assertions read the final screen. That is what a
person is left looking at when a run ends, and it is where the bugs live: a
region cleared from the wrong end leaves a blank line behind, and a region
redrawn without moving up first duplicates itself.

    ./.venv/bin/python -m unittest discover -s tests -v
"""
from __future__ import annotations

import re
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import ai_model                                             # noqa: E402
import collect                                              # noqa: E402
import console                                              # noqa: E402

_CURSOR = re.compile(r"\x1b\[(\d*)([AB])")
_CLEAR = re.compile(r"\x1b\[2?K")
_COLOR = re.compile(r"\x1b\[[0-9;]*m")


class Screen:
    """A terminal that understands the sequences console.py emits."""

    def __init__(self) -> None:
        self.rows = [""]
        self.row = 0
        self.col = 0

    # --- the stream interface Reporter needs
    def write(self, text: str) -> None:
        self._feed(text)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False

    # --- the model
    def _feed(self, text: str) -> None:
        i = 0
        while i < len(text):
            char = text[i]
            if char == "\x1b":
                match = _CURSOR.match(text, i) or _CLEAR.match(text, i) or _COLOR.match(text, i)
                if match:
                    token = match.group(0)
                    if token.endswith("A"):
                        self.row = max(0, self.row - int(match.group(1) or 1))
                    elif token.endswith("B"):
                        self.row += int(match.group(1) or 1)
                        self._grow()
                    elif token.endswith("K"):
                        self.rows[self.row] = ""
                    i = match.end()
                    continue
            if char == "\n":
                self.row += 1
                self.col = 0
                self._grow()
                i += 1
                continue
            self._grow()
            line = self.rows[self.row]
            if len(line) < self.col:
                line += " " * (self.col - len(line))
            self.rows[self.row] = line[:self.col] + char + line[self.col + 1:]
            self.col += 1
            i += 1

    def _grow(self) -> None:
        while len(self.rows) <= self.row:
            self.rows.append("")

    def lines(self) -> list[str]:
        """The non-empty lines left on screen, in order."""
        return [line.rstrip() for line in self.rows if line.strip()]

    def raw(self) -> str:
        return "\n".join(self.rows)


def build(live: bool = True, width: int = 100) -> tuple[console.Reporter, Screen]:
    screen = Screen()
    return console.Reporter(stream=screen, color=False, live=live, width=width), screen


class StepRendering(unittest.TestCase):
    def test_a_step_leaves_exactly_one_line(self):
        reporter, screen = build()
        step = console.Step(reporter, "a", 1, 3, "sspai")
        step.progress("7/18 频道 · 45s")
        step.done("ok", "24 条 · 今日 · 全文 16")
        reporter.close()
        self.assertEqual(len(screen.lines()), 1)
        self.assertIn("sspai", screen.lines()[0])
        self.assertIn("24 条", screen.lines()[0])

    def test_the_intermediate_states_are_gone(self):
        reporter, screen = build()
        step = console.Step(reporter, "a", 1, 1, "telegram")
        step.progress("7/18 频道 · 45s")
        step.done("ok", "0 条")
        reporter.close()
        self.assertNotIn("45s", screen.raw())
        self.assertNotIn("7/18", screen.raw())

    def test_a_permanent_line_between_steps_does_not_shift_the_next_one(self):
        """The bug this guards: clearing the live block used to leave the cursor
        at its bottom, so the next permanent line was written underneath the
        region and every step left a blank gap behind it."""
        reporter, screen = build()
        console.Step(reporter, "a", 1, 3, "sspai").done("ok", "24 条")
        reporter.note("一条普通说明")
        console.Step(reporter, "b", 2, 3, "telegram").done("ok", "59 条")
        reporter.close()
        lines = screen.lines()
        self.assertEqual(len(lines), 3)
        self.assertIn("sspai", lines[0])
        self.assertIn("说明", lines[1])
        self.assertIn("telegram", lines[2])

    def test_concurrent_steps_keep_separate_lines(self):
        """2c and 2d run together; each owns one line and neither erases the
        other when it finishes."""
        reporter, screen = build()
        steps = [console.Step(reporter, key, index, 3, key)
                 for index, key in enumerate(("分类·新闻", "分类·讨论", "生成汇总"), 1)]
        for step in steps:
            step.progress("pi 运行中 30s · 上限 15m00s")
        steps[0].done("ok", "44 条 → 10 类")
        steps[1].done("ok", "70 条 →  8 类")
        steps[2].done("ok", "15 关键词")
        reporter.close()
        lines = screen.lines()
        self.assertEqual(len(lines), 3)
        self.assertIn("新闻", lines[0])
        self.assertIn("讨论", lines[1])
        self.assertIn("汇总", lines[2])

    def test_a_shrinking_region_leaves_nothing_behind(self):
        reporter, screen = build()
        steps = [console.Step(reporter, key, index, 3, key)
                 for index, key in enumerate(("a", "b", "c"), 1)]
        for step in steps:
            step.progress("运行中")
        for step in steps:
            step.done("ok", "完成")
        reporter.close()
        self.assertEqual(len(screen.lines()), 3)

    def test_the_three_states_are_distinguishable(self):
        reporter, screen = build()
        for index, state in enumerate(("ok", "warn", "fail"), 1):
            console.Step(reporter, state, index, 3, state).done(state, state)
        reporter.close()
        self.assertIn(console.OK, screen.raw())
        self.assertIn(console.WARN, screen.raw())
        self.assertIn(console.BAD, screen.raw())

    def test_redirected_output_has_no_escape_sequences(self):
        """A log with cursor arithmetic in it is worse than no progress at all."""
        reporter, screen = build(live=False)
        step = console.Step(reporter, "a", 1, 1, "sspai")
        step.progress("7/18 频道")
        step.done("ok", "24 条")
        reporter.close()
        self.assertNotIn("\x1b", screen.raw())
        self.assertEqual(len(screen.lines()), 1)

    def test_a_live_line_is_truncated_to_the_terminal_width(self):
        """A wrapped live line would break the cursor arithmetic that redraws
        it, so it is cut instead."""
        reporter, screen = build(width=40)
        step = console.Step(reporter, "a", 1, 1, "x")
        step.progress("很长的进度信息" * 30)
        step.done("ok", "完成")
        reporter.close()
        self.assertEqual(len(screen.lines()), 1)


class TextMeasurement(unittest.TestCase):
    def test_cjk_counts_as_two_columns(self):
        self.assertEqual(console.display_width("少数派"), 6)
        self.assertEqual(console.display_width("sspai"), 5)

    def test_colour_codes_do_not_count_as_columns(self):
        reporter = console.Reporter(stream=Screen(), color=True, live=False)
        self.assertEqual(console.display_width(reporter.style("abc", "bold")), 3)

    def test_padding_lines_up_styled_and_plain_cells(self):
        self.assertEqual(console.pad("abc", 6), "abc   ")
        self.assertEqual(console.pad("42", 4, right=True), "  42")
        self.assertEqual(console.pad("少数派", 8), "少数派  ")

    def test_overflow_is_marked_rather_than_dropped_silently(self):
        self.assertTrue(console.fit("a" * 40, 10).endswith("…"))
        self.assertEqual(console.fit("短", 10), "短")

    def test_durations_read_as_durations(self):
        self.assertEqual(console.fmt_duration(9.94), "9.9s")
        self.assertEqual(console.fmt_duration(62), "1m02s")
        self.assertEqual(console.fmt_duration(3661), "1h01m")

    def test_wrapping_keeps_a_hanging_indent(self):
        """A continuation that is not indented reads as a new entry, which is
        what an exception message used to do when it ran past the terminal."""
        lines = console.wrap("word " * 30, 30, "  label  ", "         ")
        self.assertGreater(len(lines), 1)
        self.assertTrue(lines[0].startswith("  label  "))
        self.assertTrue(all(line.startswith("         ") for line in lines[1:]))
        self.assertTrue(all(console.display_width(line) <= 30 for line in lines))

    def test_a_token_longer_than_the_line_is_broken(self):
        lines = console.wrap("x" * 100, 20)
        self.assertEqual(len(lines), 5)
        self.assertEqual("".join(lines), "x" * 100)

    def test_cjk_wraps_by_display_width(self):
        lines = console.wrap("中文" * 20, 21)
        self.assertTrue(all(console.display_width(line) <= 21 for line in lines))

    def test_short_text_stays_on_one_line(self):
        self.assertEqual(console.wrap("短说明", 40), ["短说明"])

    def test_wrapping_nothing_produces_the_label_line(self):
        self.assertEqual(console.wrap("", 40, "label: ", "       "), ["label: "])


class FailureGrouping(unittest.TestCase):
    def test_identical_messages_become_one_row(self):
        """Eighteen channels failing the same way is one problem, and printing
        it eighteen times is what made a broken run unreadable."""
        rows = collect._group_failures(
            [(f"telegram/ch{i}", "SSL EOF") for i in range(18)]
            + [("v2ex", "FetchError: timeout")])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], 18)
        self.assertEqual(rows[0][1], "SSL EOF")
        self.assertEqual(rows[1][2], ["v2ex"])

    def test_only_failed_units_are_collected(self):
        report = {"channels": {"a": {"ok": True}, "b": {"ok": False, "error": "boom"}},
                  "subreddits": {"x": {"ok": False, "error": "429"}}}
        self.assertEqual(collect._collector_failures(report),
                         [("b", "boom"), ("x", "429")])

    def test_the_unit_total_is_the_group_the_collector_actually_uses(self):
        """"2 of 3 failed" only means something with the 3. A collector groups
        by channels or by subreddits, never both, so this reads the one that is
        there instead of adding them together."""
        self.assertEqual(collect._unit_total({"channels": {"a": 1, "b": 2}}), 2)
        self.assertEqual(collect._unit_total({"subreddits": {"x": 1}}), 1)
        self.assertEqual(collect._unit_total({}), 0)

    def test_a_failure_without_a_message_still_names_the_unit(self):
        self.assertEqual(collect._collector_failures({"channels": {"a": {"ok": False}}}),
                         [("a", "failed")])

    def test_a_report_with_no_units_does_not_divide_by_zero(self):
        self.assertEqual(collect._unit_total({}), 0)
        self.assertEqual(collect._collector_failures({}), [])


class SourceLine(unittest.TestCase):
    def test_a_source_with_nothing_says_so_instead_of_none_days(self):
        """`latest: Noned ago` is what a source with no items used to print: the
        template was f"{staleness_days}d ago" and the value was None."""
        line = collect._source_detail({"updated_today": False, "staleness_days": None,
                                       "fulltext_fetched": 0}, 0, [])
        self.assertIn("无内容", line)
        self.assertNotIn("None", line)

    def test_freshness_and_fulltext_are_reported(self):
        line = collect._source_detail({"updated_today": True, "staleness_days": 0,
                                       "fulltext_fetched": 16}, 24, [])
        self.assertIn("24 条", line)
        self.assertIn("今日", line)
        self.assertIn("全文 16", line)

    def test_an_undated_source_is_not_called_stale(self):
        line = collect._source_detail({"updated_today": False, "staleness_days": None,
                                       "fulltext_fetched": 0}, 15, [])
        self.assertIn("无日期", line)

    def test_notes_are_appended_to_the_line(self):
        line = collect._source_detail({"updated_today": False, "staleness_days": 3,
                                       "fulltext_fetched": 0}, 59, ["2 个重复 id"])
        self.assertIn("3 天前", line)
        self.assertIn("2 个重复 id", line)


class Heartbeat(unittest.TestCase):
    """A model call is the longest wait in the run and used to be the quietest.

    The default budget is fifteen minutes, and the only thing that used to be
    printed was the failure at the end of it.
    """

    def _generate(self, seen: list, **overrides):
        def slow(*args, **kwargs):
            time.sleep(0.06)
            return '{"model": "x", "day": "2026-10-15"}'

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "in.json"
            source.write_text("{}", encoding="utf-8")
            with unittest.mock.patch.object(ai_model, "_invoke", side_effect=slow), \
                 unittest.mock.patch.object(ai_model, "HEARTBEAT_SECONDS", 0.01):
                return ai_model.generate("pi", source, Path(tmp) / "out.json",
                                         model="m", progress=seen.append,
                                         verbose=False, **overrides)

    def test_a_running_call_says_it_is_still_running(self):
        seen: list[str] = []
        report = self._generate(seen)
        self.assertTrue(report["called"])
        self.assertTrue(any("运行中" in line for line in seen), seen)

    def test_the_budget_is_stated_before_the_first_heartbeat(self):
        """Otherwise a fifteen-minute wait has no stated end."""
        seen: list[str] = []
        self._generate(seen, timeout=900)
        self.assertIn("上限", seen[0])
        self.assertIn("15m", seen[0])

    def test_the_elapsed_time_is_recorded_even_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "in.json"
            source.write_text("{}", encoding="utf-8")
            boom = ai_model.ModelError("timed out after 900s")
            with unittest.mock.patch.object(ai_model, "_invoke", side_effect=boom):
                report = ai_model.generate("pi", source, Path(tmp) / "out.json",
                                           model="m", verbose=False)
        self.assertIn("timed out", report["error"])
        self.assertIn("seconds", report)

    def test_no_progress_callback_still_produces_decisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "in.json"
            source.write_text("{}", encoding="utf-8")
            with unittest.mock.patch.object(ai_model, "_invoke",
                                            return_value='{"keywords": []}'), \
                 unittest.mock.patch.object(ai_model, "HEARTBEAT_SECONDS", 0.01):
                report = ai_model.generate("pi", source, Path(tmp) / "out.json",
                                           model="m", verbose=False)
        self.assertTrue(report["called"])


class Capture(unittest.TestCase):
    """A collector's own stderr must not land on the live line."""

    def test_a_collector_print_is_captured_rather_than_printed(self):
        reporter, screen = build()
        with console.capture() as buffer:
            print("  ! sspai api failed, falling back to RSS", file=sys.stderr)
        reporter.write("after")
        reporter.close()
        self.assertEqual(console.captured_lines(buffer),
                         ["sspai api failed, falling back to RSS"])
        self.assertEqual(screen.lines(), ["after"])

    def test_the_reporter_keeps_working_inside_the_block(self):
        """The reporter captured its stream when it was built, so swapping
        sys.stderr must not silence the live line with it."""
        reporter, screen = build()
        step = console.Step(reporter, "a", 1, 1, "sspai")
        with console.capture():
            step.progress("7/18 频道")
            step.done("ok", "24 条")
        reporter.close()
        self.assertEqual(len(screen.lines()), 1)
        self.assertIn("24 条", screen.lines()[0])

    def test_stderr_is_restored_even_when_the_block_raises(self):
        original = sys.stderr
        with self.assertRaises(RuntimeError):
            with console.capture():
                raise RuntimeError("boom")
        self.assertIs(sys.stderr, original)

    def test_blank_lines_are_dropped(self):
        with console.capture() as buffer:
            print("\n\n  a  \n\n", file=sys.stderr)
        self.assertEqual(console.captured_lines(buffer), ["a"])


class CollectorNotes(unittest.TestCase):
    def test_quiet_units_are_counted_rather_than_listed(self):
        notes = collect._collector_notes(
            {"day": "2026-10-15", "mode": "auto",
             "channels": {"a": {"ok": True, "kept": 5},
                          "b": {"ok": True, "kept": 0},
                          "c": {"ok": True, "kept": 0}}})
        self.assertIn("2/3 个频道今日无内容", notes)
        self.assertIn("自动选定日期 2026-10-15", notes)

    def test_a_fixed_day_is_not_announced_as_a_choice(self):
        self.assertEqual(
            collect._collector_notes({"day": "2026-10-15", "mode": "fixed",
                                      "channels": {"a": {"ok": True, "kept": 1}}}), [])

    def test_a_failed_channel_is_not_called_quiet(self):
        self.assertEqual(
            collect._collector_notes({"channels": {"a": {"ok": False, "error": "boom"}}}),
            [])

    def test_a_report_with_nothing_to_say_says_nothing(self):
        self.assertEqual(collect._collector_notes({}), [])


if __name__ == "__main__":
    unittest.main()
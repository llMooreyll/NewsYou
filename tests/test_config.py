"""Offline tests for the configuration layer and backend profiles.

    ./.venv/bin/python -m unittest discover -s tests -v

No network, no model calls: the backends are exercised through `build_command`,
which is exactly the part that differs between them.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import archive  # noqa: E402

import ai_model  # noqa: E402
import ai_apply  # noqa: E402
import config  # noqa: E402

class ConfigLoading(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "config.toml"
        self._env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def write(self, text: str) -> Path:
        self.path.write_text(text, encoding="utf-8")
        return self.path

    def test_builtin_defaults_match_the_shipped_file(self):
        """The committed config.toml must not drift from the built-in defaults.

        It is documented as a no-op, so a difference is a bug in one of them.
        """
        shipped = config.load(ROOT / "config" / "config.toml")
        builtin = config.load(path=None)
        for key, value in builtin._values.items():
            if key == "feeds":
                continue
            self.assertEqual(shipped.get(key), value,
                             f"{key} differs between config.toml and defaults()")

    def test_unknown_key_is_rejected(self):
        with self.assertRaises(config.ConfigError):
            config.load(self.write('[ai]\nbakcend = "x"\n'))

    def test_wrong_type_is_rejected(self):
        with self.assertRaises(config.ConfigError):
            config.load(self.write('[ai]\ntimeout = "soon"\n'))

    def test_broken_toml_is_rejected(self):
        with self.assertRaises(config.ConfigError):
            config.load(self.write("[ai\nbackend = 1\n"))

    def test_missing_explicit_path_is_rejected(self):
        with self.assertRaises(config.ConfigError):
            config.load(self.dir / "nope.toml")

    def test_open_tables_accept_new_source_names(self):
        cfg = config.load(self.write(
            '[sources.labels]\nmy-feed = "My Feed"\n\n'
            '[sources.limits]\nmy-feed = 7\n'))
        self.assertEqual(cfg.get("sources.labels")["my-feed"], "My Feed")
        self.assertEqual(cfg.get("sources.limits")["my-feed"], 7)

    def test_open_table_still_type_checks(self):
        with self.assertRaises(config.ConfigError):
            config.load(self.write('[sources.labels]\nmy-feed = 3\n'))

    def test_feeds_validation(self):
        with self.assertRaises(config.ConfigError):
            config.load(self.write('[[feeds]]\nid = "x"\n'))       # no url
        with self.assertRaises(config.ConfigError):
            config.load(self.write('[[feeds]]\nid="x"\nurl="u"\ntypo=1\n'))
        cfg = config.load(self.write('[[feeds]]\nid="x"\nurl="https://e/f"\n'))
        self.assertEqual(cfg.get("feeds")[0]["id"], "x")

    def test_precedence_cli_beats_env_beats_file(self):
        self.write('[ai]\nbackend = "from-file"\n')
        os.environ["NEWSLETTER_AI_BACKEND"] = "from-env"
        cfg = config.load(self.path)
        self.assertEqual(cfg.get("ai.backend"), "from-env")
        self.assertEqual(cfg.origin("ai.backend"), "env")

        cfg = config.load(self.path, cli={"ai.backend": "from-cli"})
        self.assertEqual(cfg.get("ai.backend"), "from-cli")
        self.assertEqual(cfg.origin("ai.backend"), "cli")

        # A None override means "the flag was not given" and must not win.
        cfg = config.load(self.path, cli={"ai.backend": None})
        self.assertEqual(cfg.get("ai.backend"), "from-env")

    def test_env_type_coercion(self):
        os.environ["NEWSLETTER_AI_TIMEOUT"] = "120"
        os.environ["NEWSLETTER_COLLECT_IMAGES"] = "false"
        cfg = config.load(path=None)
        self.assertEqual(cfg.get("ai.timeout"), 120)
        self.assertIs(cfg.get("collect.images"), False)

    def test_bad_env_value_is_rejected(self):
        os.environ["NEWSLETTER_AI_TIMEOUT"] = "soon"
        with self.assertRaises(config.ConfigError):
            config.load(path=None)

    def test_get_returns_nested_tables(self):
        """`sources.limits` is a table, not a leaf, and must still resolve."""
        cfg = config.load(path=None)
        limits = cfg.get("sources.limits")
        self.assertIsInstance(limits, dict)
        self.assertEqual(limits["telegram"], 400)
        self.assertEqual(cfg.get("sources.labels")["sspai"], "少数派")

    def test_dump_round_trips_through_toml(self):
        cfg = config.load(ROOT / "config" / "config.toml")
        import tomllib
        again = tomllib.loads(cfg.dump(show_origin=False))
        self.assertEqual(again["ai"]["model"], cfg.get("ai.model"))
        self.assertEqual(again["telegram"]["channels"], cfg.get("telegram.channels"))
        self.assertEqual(len(again["sources"]["order"]), len(cfg.get("sources.order")))

    def test_dump_annotates_origins(self):
        out = config.load(ROOT / "config" / "config.toml").dump(show_origin=True)
        self.assertIn("# from file", out)
        self.assertIn("# from default", out)

class BackendProfiles(unittest.TestCase):
    def test_pi_command_shape(self):
        cmd = ai_model.build_command("pi", "deepseek-v4.1-flash")
        self.assertTrue(cmd[0].endswith("pi"))
        self.assertIn("--no-context-files", cmd)   # without it, AGENTS.md leaks in
        self.assertIn("--no-session", cmd)
        self.assertIn("--no-tools", cmd)
        self.assertEqual(cmd[cmd.index("--model") + 1], "deepseek-v4.1-flash")
        self.assertEqual(cmd[-1], ai_model.PROMPT_ARG)

    def test_model_can_be_omitted(self):
        cmd = ai_model.build_command("pi", None)
        self.assertNotIn("--model", cmd)

    def test_codex_command_differs_from_pi(self):
        """The whole point of profiles: same two fields, a different command."""
        pi_cmd = ai_model.build_command("pi", "m")
        codex_cmd = ai_model.build_command("codex", "m")
        self.assertTrue(codex_cmd[0].endswith("codex"))
        self.assertIn("exec", codex_cmd)
        self.assertNotIn("--no-context-files", codex_cmd)
        self.assertNotEqual(pi_cmd[1:], codex_cmd[1:])

    def test_extra_args_land_before_the_prompt(self):
        cmd = ai_model.build_command("pi", "m", ["--thinking", "high"])
        self.assertEqual(cmd[-3:], ["--thinking", "high", ai_model.PROMPT_ARG])

    def test_unknown_backend_has_no_command(self):
        with self.assertRaises(ai_model.ModelError):
            ai_model.build_command("bogus", "m")

    def test_none_and_unknown_backends_resolve(self):
        self.assertEqual(ai_model.resolve_backend("none"), ("none", ""))
        backend, warning = ai_model.resolve_backend("bogus")
        self.assertEqual(backend, "none")
        self.assertIn("unknown", warning)

    def test_experimental_backend_warns(self):
        with mock.patch("ai_model.shutil.which", return_value="/usr/bin/codex"):
            backend, warning = ai_model.resolve_backend("codex")
        self.assertEqual(backend, "codex")
        self.assertIn("experimental", warning)

class Provenance(unittest.TestCase):
    def test_self_reported_model_is_overridden(self):
        """A model claiming to be something else must not reach the digest."""
        import json
        out = Path(tempfile.mkdtemp()) / "decisions.json"
        inp = out.with_name("input.json")
        inp.write_text("{}", encoding="utf-8")
        raw = json.dumps({"schema": "x", "model": "some-other-model", "actions": []})
        with mock.patch.object(ai_model, "_invoke", return_value=raw):
            ai_model.generate("pi", inp, out, model="deepseek-v4.1-flash",
                              verbose=False)
        written = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(written["model"], "pi:deepseek-v4.1-flash")
        self.assertEqual(written["model_self_reported"], "some-other-model")

    def test_recorded_day_is_the_day_of_the_input(self):
        """The prompts show an example day; a model may echo it back."""
        import json
        out = Path(tempfile.mkdtemp()) / "decisions.json"
        inp = out.with_name("input.json")
        inp.write_text("{}", encoding="utf-8")
        raw = json.dumps({"schema": "x", "day": "2026-10-12", "actions": []})
        with mock.patch.object(ai_model, "_invoke", return_value=raw):
            ai_model.generate("pi", inp, out, day="2026-10-14", verbose=False)
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["day"], "2026-10-14")

class ReusePolicy(unittest.TestCase):
    """A decisions file is only valid for the day it was made for.

    Reuse keyed on the file merely existing is unsound across days: for
    URL-keyed sources an item that stays on the list keeps its id, so
    yesterday's judgement would be applied to today's data with no error.
    """

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def write(self, day: str) -> Path:
        path = self.dir / "telegram_decisions.json"
        path.write_text(json.dumps({"schema": "x", "day": day, "actions": []}),
                        encoding="utf-8")
        return path

    def test_missing_file_is_not_stale(self):
        self.assertFalse(ai_apply.decisions_are_stale(self.dir / "nope.json", "2026-10-14"))

    def test_same_day_is_not_stale(self):
        self.assertFalse(ai_apply.decisions_are_stale(self.write("2026-10-14"), "2026-10-14"))

    def test_other_day_is_stale(self):
        self.assertTrue(ai_apply.decisions_are_stale(self.write("2026-10-13"), "2026-10-14"))

    def test_unreadable_file_is_not_treated_as_current(self):
        path = self.dir / "broken.json"
        path.write_text("not json", encoding="utf-8")
        self.assertFalse(ai_apply.decisions_are_stale(path, "2026-10-14"))

    def test_reuse_defaults_to_off(self):
        """The shipped default must call the model on every run."""
        self.assertIs(config.load(path=None).get("ai.reuse"), False)


class ArchiveIsACopy(unittest.TestCase):
    def test_install_keeps_versions_and_force_replaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "day").mkdir()
            self.assertEqual(archive.final_name(root, "day", force=False).name, "day-v2")
            self.assertEqual(archive.final_name(root, "day", force=True).name, "day")
            self.assertEqual(archive.final_name(root, "other", force=False).name, "other")


if __name__ == "__main__":
    unittest.main()

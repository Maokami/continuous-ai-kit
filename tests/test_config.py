"""Exercise real Git snapshots without executing any consumer repository code."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cai import config
from cai.common import digest_json, neutralize, quote_at_line, reject_secrets


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        old = Path.cwd()
        os.chdir(self.temp.name)
        self.addCleanup(os.chdir, old)
        self.run_git("init", "-q")
        self.run_git("config", "user.name", "Fixture")
        self.run_git("config", "user.email", "fixture@example.invalid")
        self.run_git("config", "core.hooksPath", "/dev/null")
        Path("docs").mkdir()
        Path("docs/guide.md").write_text("# Quick start\nRun the documented checks.\n")
        Path("prompt.md").write_text("Review documentation consistency.\n")
        self.cfg = {"version": 1, "lane": "docs", "source_globs": ["docs/*.md"],
                    "prompt_file": "prompt.md"}
        self.save()

    def run_git(self, *args):
        subprocess.run(["git", *args], check=True, capture_output=True)

    def save(self):
        Path("scan.json").write_text(json.dumps(self.cfg))
        self.run_git("add", ".")
        self.run_git("commit", "-qm", "fixture", "--allow-empty")

    def test_non_lean_sources_without_core_changes(self):
        cfg = config.load("scan.json")
        self.assertEqual(cfg["adapter"], "text")
        self.assertEqual(list(config.source_files(cfg)), ["docs/guide.md"])

    def test_unknown_version_and_fields_rejected(self):
        for change in ({"version": 2}, {"version": True}, {"shell": "echo unsafe"}):
            self.cfg = {"version": 1, "lane": "docs", "source_globs": ["docs/*.md"],
                        "prompt_file": "prompt.md", **change}
            self.save()
            with self.assertRaises(ValueError):
                config.load("scan.json")

    def test_path_escape_and_symlinks_rejected(self):
        for path in ("../secret", "/tmp/secret", "./prompt.md", ".git/config", "a//b"):
            with self.assertRaises(ValueError):
                config.path_name(path)
        Path("docs/link.md").symlink_to("guide.md")
        self.save()
        with self.assertRaises(ValueError):
            config.source_files(config.load("scan.json"))

    def test_dirty_source_and_untracked_input_rejected(self):
        Path("docs/guide.md").write_text("changed after checkout")
        with self.assertRaises(ValueError):
            config.source_files(config.load("scan.json"))
        Path("untracked.md").write_text("not pinned")
        with self.assertRaises(ValueError):
            config.tracked_bytes("untracked.md")

    def test_boolean_and_flag_injection_rejected(self):
        for key, bad in (("batch_size", True), ("max_turns", 65), ("model", "sonnet --tools Bash"),
                         ("lane", "x\nrun=true"), ("default_branch", "$(bad)")):
            previous = dict(self.cfg)
            self.cfg[key] = bad
            self.save()
            with self.assertRaises(ValueError):
                config.load("scan.json")
            self.cfg = previous

    def test_digest_canonical_and_content_sensitive(self):
        self.assertEqual(digest_json({"a": 1, "b": 2}), digest_json({"b": 2, "a": 1}))
        self.assertNotEqual(digest_json({"source": "a"}), digest_json({"source": "b"}))

    def test_output_safety_and_exact_quotes(self):
        self.assertEqual(neutralize("@user <!-- marker -->"), "＠user &lt;!-- marker --&gt;")
        with self.assertRaises(ValueError):
            reject_secrets({"summary": "a private string"}, ["private string"])
        quote_at_line({"a": "one\ntwo\n"}, "a", 2, "two")
        for line in (1, True, -1):
            with self.assertRaises(ValueError):
                quote_at_line({"a": "one\ntwo\n"}, "a", line, "two")

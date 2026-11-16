"""Exercise the preview/write CLI against real committed Git fixtures."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cai import config
from cai.triage import TriageConfig


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "core.hooksPath", "/dev/null")
        (self.root / "docs").mkdir()
        (self.root / "docs/guide.md").write_text("# Setup\nRun the documented checks.\n")
        (self.root / "AGENTS.md").write_text("Keep existing public contracts.\n")
        (self.root / "Proofs").mkdir()
        (self.root / "Proofs/Example.lean").write_text("theorem example_true : True := by trivial\n")
        self.commit()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], stderr=subprocess.PIPE)

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")

    def cli(self, kind, *args):
        common = [] if kind == "inspect" else ["--ref", "a" * 40, "--default-branch", "main"]
        return subprocess.run([sys.executable, "-I", str(ROOT / "onboard.py"), kind,
                               str(self.root), *common, *args], capture_output=True, text=True)

    def scan(self, *args):
        return self.cli("scan", "--source-glob", "docs/*.md", *args)

    def test_inspect_and_preview_do_not_write(self):
        view = self.cli("inspect")
        self.assertEqual(view.returncode, 0, view.stderr)
        self.assertEqual(json.loads(view.stdout)["policy_candidates"], ["AGENTS.md"])
        result = self.scan("--profile", "docs", "--policy-file", "AGENTS.md")
        self.assertEqual(result.returncode, 0, result.stderr)
        preview = json.loads(result.stdout)
        self.assertEqual(preview["mode"], "preview")
        self.assertEqual(preview["scope"]["files"], ["docs/guide.md"])
        self.assertEqual(preview["scope"]["batches"], 1)
        self.assertFalse((self.root / ".github").exists())
        self.assertEqual(self.git("status", "--porcelain"), b"")

    def test_every_scan_profile_loads_in_runtime(self):
        for profile in ("general", "docs", "core", "lean"):
            with self.subTest(profile=profile):
                source = "Proofs/Example.lean" if profile == "lean" else "docs/guide.md"
                result = self.cli("scan", "--source-glob", source, "--profile", profile,
                                  "--lane", profile, "--policy-file", "AGENTS.md", "--write")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.commit()
                old = Path.cwd()
                try:
                    os.chdir(self.root)
                    cfg = config.load(f".github/continuous-ai/{profile}.json")
                    self.assertEqual(cfg["adapter"], "lean" if profile == "lean" else "text")
                    self.assertEqual(cfg["policy_files"], ["AGENTS.md"])
                    self.assertEqual(list(config.source_files(cfg)), [source])
                    self.assertTrue(config.tracked_bytes(cfg["prompt_file"]))
                finally:
                    os.chdir(old)
        workflow = (self.root / ".github/workflows/continuous-ai-docs.yml").read_text()
        self.assertIn("- cron: '17 * * * *'", workflow)
        self.assertIn("recovery_only: ${{ github.event_name == 'schedule' && github.event.schedule == '17 * * * *' }}", workflow)
        self.assertIn("vars.CAI_QUOTA_RECOVERY_DISABLED", workflow)

    def test_triage_requires_explicit_checks_and_round_trips(self):
        missing = self.cli("triage", "--write")
        self.assertNotEqual(missing.returncode, 0)
        self.assertFalse((self.root / ".github").exists())
        result = self.cli("triage", "--required-check", "Unit tests", "--required-check", "Lint",
                          "--default-branch", "1.0", "--write")
        self.assertEqual(result.returncode, 0, result.stderr)
        cfg = TriageConfig.from_mapping(json.loads(
            (self.root / ".github/continuous-ai/pr-triage.json").read_text()))
        self.assertEqual(cfg.required_checks, ("Unit tests", "Lint"))
        workflow = (self.root / ".github/workflows/continuous-ai-pr-triage.yml").read_text()
        self.assertIn("actions: read", workflow)
        self.assertIn('default_branch: "1.0"', workflow)
        self.assertNotIn("secrets: inherit", workflow)

    def test_invalid_triage_checks_leave_no_files(self):
        for checks in (("A", "A"), (" A",), ("A\nB",)):
            with self.subTest(checks=checks):
                args = [arg for check in checks for arg in ("--required-check", check)]
                result = self.cli("triage", *args, "--write")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertFalse((self.root / ".github").exists())

    def test_empty_and_dirty_sources_leave_no_files(self):
        empty = self.cli("scan", "--source-glob", "missing/*.md", "--write")
        self.assertEqual(empty.returncode, 1)
        (self.root / "docs/guide.md").write_text("not the committed source")
        dirty = self.scan("--write")
        self.assertEqual(dirty.returncode, 1)
        self.assertFalse((self.root / ".github").exists())

    def test_uncommitted_and_symlink_sources_rejected(self):
        (self.root / "docs/new.md").write_text("staged only")
        self.git("add", "docs/new.md")
        self.assertEqual(self.scan("--write").returncode, 1)
        self.commit()
        (self.root / "docs/link.md").symlink_to("guide.md")
        self.commit()
        self.assertEqual(self.scan("--write").returncode, 1)
        self.assertFalse((self.root / ".github").exists())

    def test_policy_overlap_and_dirty_policy_rejected(self):
        overlap = self.cli("scan", "--source-glob", "*.md", "--policy-file", "AGENTS.md", "--write")
        self.assertEqual(overlap.returncode, 1)
        (self.root / "AGENTS.md").write_text("dirty policy")
        self.assertEqual(self.scan("--policy-file", "AGENTS.md", "--write").returncode, 1)
        self.assertFalse((self.root / ".github").exists())

    def test_scope_must_remain_disjoint_after_installation(self):
        (self.root / "data.json").write_text("{}")
        self.commit()
        for pattern in ("*.md", "*.json"):
            with self.subTest(pattern=pattern):
                result = self.cli("scan", "--source-glob", pattern, "--write")
                self.assertEqual(result.returncode, 1)
                self.assertIn("generated config/prompt", result.stderr)
                self.assertFalse((self.root / ".github").exists())

    def test_late_destination_conflict_preserves_all_files(self):
        workflow = self.root / ".github/workflows/continuous-ai-improvements.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text("owner's existing workflow")
        for args in ((), ("--write",)):
            result = self.scan(*args)
            self.assertEqual(result.returncode, 1)
        self.assertEqual(workflow.read_text(), "owner's existing workflow")
        self.assertFalse((self.root / ".github/continuous-ai").exists())

    def test_non_directory_parent_is_detected_before_any_write(self):
        workflows = self.root / ".github/workflows"
        workflows.parent.mkdir()
        workflows.write_text("existing file")
        self.assertEqual(self.scan("--write").returncode, 1)
        self.assertFalse((self.root / ".github/continuous-ai").exists())

    def test_symlink_destination_is_rejected(self):
        with tempfile.TemporaryDirectory() as outside:
            (self.root / ".github").symlink_to(outside, target_is_directory=True)
            self.assertEqual(self.scan("--write").returncode, 1)
            self.assertEqual(list(Path(outside).iterdir()), [])

    def test_invalid_runtime_configuration_is_rejected_before_write(self):
        for args in (("--batch-size", "0"), ("--max-turns", "65"), ("--ref", "main"),
                     ("--default-branch", "a' || true"), ("--model", "x --tools Bash"),
                     ("--source-glob", "../*.md")):
            with self.subTest(args=args):
                self.assertEqual(self.scan(*args, "--write").returncode, 1)
                self.assertFalse((self.root / ".github").exists())

    def test_whole_scope_checked_not_only_first_batch(self):
        (self.root / "docs/z.md").write_text("last source\n")
        self.commit()
        preview = json.loads(self.scan("--batch-size", "1").stdout)
        self.assertEqual(preview["scope"]["batches"], 2)
        (self.root / "docs/z.md").write_text("dirty last source")
        self.assertEqual(self.scan("--batch-size", "1", "--write").returncode, 1)
        self.assertFalse((self.root / ".github").exists())

    def test_repository_errors_are_actionable(self):
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other)
            for step in ("plain", "empty"):
                if step == "empty":
                    subprocess.run(["git", "init", "-q", str(outside)], check=True)
                result = subprocess.run([sys.executable, "-I", str(ROOT / "onboard.py"),
                                         "inspect", str(outside)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 1)
                self.assertIn("at least one commit", result.stderr)
        result = subprocess.run([sys.executable, "-I", str(ROOT / "onboard.py"),
                                 "inspect", str(self.root / "docs")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("not a subdirectory", result.stderr)

    def test_non_utf8_source_names_the_file(self):
        (self.root / "docs/binary.md").write_bytes(b"\xff\xfe")
        self.commit()
        result = self.scan("--write")
        self.assertEqual(result.returncode, 1)
        self.assertIn("UTF-8 text: docs/binary.md", result.stderr)
        self.assertFalse((self.root / ".github").exists())

    def test_triage_lane_errors_name_the_option(self):
        for lane in ("x" * 33, "a--b", "x-"):
            result = self.cli("triage", "--lane", lane, "--required-check", "CI", "--write")
            self.assertEqual(result.returncode, 1)
            self.assertIn("Triage --lane", result.stderr)
        self.assertFalse((self.root / ".github").exists())

    def test_git_inventory_does_not_execute_fsmonitor(self):
        hook = self.root / "fsmonitor.sh"
        marker = self.root / "fsmonitor-executed"
        hook.write_text('#!/bin/sh\nprintf called > fsmonitor-executed\nprintf "token\\0"\n')
        hook.chmod(0o755)
        self.git("config", "core.fsmonitor", str(hook))
        # Demonstrate that the fixture's local Git configuration really invokes the hook.
        self.git("ls-files")
        self.assertTrue(marker.exists())
        marker.unlink()
        self.assertEqual(self.cli("inspect").returncode, 0)
        self.assertEqual(self.scan().returncode, 0)
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()

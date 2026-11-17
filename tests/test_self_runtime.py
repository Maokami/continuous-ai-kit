"""A squash-merged runtime must use the published SHA rather than the PR SHA."""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from check_self_runtime import check


class RuntimePinTests(unittest.TestCase):
    def test_squash_side_branch_pin_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
            env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
            def git(*args):
                return subprocess.check_output(["git", "-C", str(root), *args], env=env,
                                               stderr=subprocess.PIPE).decode().strip()
            git("init", "-q", "-b", "main")
            git("config", "user.name", "Fixture")
            git("config", "user.email", "fixture@example.invalid")
            git("config", "core.hooksPath", "/dev/null")
            workflows = root / ".github/workflows"
            workflows.mkdir(parents=True)
            runtime = workflows / "scan-report.yml"
            runtime.write_text("name: original\n")
            git("add", ".")
            git("commit", "-qm", "base")
            git("switch", "-qc", "feature")
            runtime.write_text("name: updated runtime\n")
            git("commit", "-qam", "feature runtime")
            side_sha = git("rev-parse", "HEAD")
            git("switch", "-q", "main")
            git("merge", "--squash", "feature")
            git("commit", "-qm", "squashed runtime")
            main_sha = git("rev-parse", "HEAD")
            git("update-ref", "refs/remotes/origin/main", main_sha)
            caller = workflows / "continuous-ai-kit-maintenance.yml"
            def pin(sha):
                caller.write_text("    uses: Maokami/continuous-ai-kit/.github/workflows/scan-report.yml@" + sha + "\n")
            pin(side_sha)
            with self.assertRaisesRegex(ValueError, "post-merge SHA"):
                check(root)
            pin(main_sha)
            check(root)
            caller.write_text(caller.read_text().replace(main_sha, "main"))
            caller.write_text(caller.read_text() + "# uses: Maokami/continuous-ai-kit/.github/workflows/scan-report.yml@" + main_sha + "\n")
            with self.assertRaisesRegex(ValueError, "full runtime SHA"):
                check(root)

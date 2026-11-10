"""Check generated caller installation and refusal of unsafe destinations."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("installer", ROOT / "install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

class InstallTests(unittest.TestCase):
    def test_isolated_cli_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            command = [sys.executable, "-I", str(ROOT / "install.py"), directory,
                       "--ref", "a" * 40, "--source-glob", "docs/*.md"]
            first = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            workflow = Path(directory, ".github/workflows/continuous-ai-improvements.yml").read_text()
            self.assertIn("cancel-in-progress: false", workflow)
            self.assertIn("scan-report.yml@" + "a" * 40, workflow)
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)

    def test_yaml_ambiguous_branch_is_quoted(self):
        with tempfile.TemporaryDirectory() as directory:
            installer.install(Path(directory), "a" * 40, "docs", ["docs/*.md"], "text", "1.0")
            workflow = Path(directory, ".github/workflows/continuous-ai-docs.yml").read_text()
            self.assertIn('default_branch: "1.0"', workflow)
            self.assertIn("if: github.ref == 'refs/heads/1.0'", workflow)

    def test_symlink_parent_and_bad_glob(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            (root / ".github").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                installer.install(root, "a" * 40, "docs", ["docs/*.md"], "text")
            self.assertEqual(list(Path(outside).iterdir()), [])
            (root / ".github").unlink()
            with self.assertRaises(ValueError):
                installer.install(root, "a" * 40, "docs", ["../secret"], "text")
            self.assertEqual(list(root.iterdir()), [])

"""An optional missing label read must never suppress other API failures."""

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cai.github import GitHub


class MissingReadTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, GITHUB_REPOSITORY="o/r", GITHUB_TOKEN="never-print-token")
        env.start()
        self.addCleanup(env.stop)
        self.api = GitHub()

    def test_only_explicit_404_get_is_optional(self):
        error = HTTPError("https://api.github.com", 404, "missing", {}, None)
        with patch("cai.github.urlopen", side_effect=error) as call:
            self.assertIsNone(self.api.request("/repos/o/r/labels/absent", missing_ok=True))
        self.assertEqual(call.call_count, 1)
        with patch("cai.github.urlopen", side_effect=error), self.assertRaises(RuntimeError):
            self.api.request("/repos/o/r/labels/absent")

    def test_missing_ok_cannot_hide_failed_writes(self):
        error = HTTPError("https://api.github.com", 404, "missing", {}, None)
        with patch("cai.github.urlopen", side_effect=error) as call, self.assertRaises(RuntimeError):
            self.api.request("/repos/o/r/labels", {"name": "new"}, missing_ok=True)
        self.assertEqual(call.call_count, 1)

    def test_optional_read_does_not_hide_permission_failure(self):
        error = HTTPError("https://api.github.com", 403, "forbidden", {}, None)
        with patch("cai.github.urlopen", side_effect=error), self.assertRaises(RuntimeError) as caught:
            self.api.request("/repos/o/r/labels/absent", missing_ok=True)
        self.assertNotIn("never-print-token", str(caught.exception))

"""Failure diagnostics must explain the category without forwarding provider text."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("diagnostics", ROOT / "cai/diagnostics.py")
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)


class DiagnosticsTests(unittest.TestCase):
    def test_token_checks_do_not_normalize_bad_values(self):
        self.assertEqual(D.token_category("opaque-token"), "token_present")
        self.assertEqual(D.token_category(""), "missing_oauth_token")
        for value in (" token", "token\n", "to\rken", "to\tken", "token\x00"):
            self.assertEqual(D.token_category(value), "oauth_token_whitespace")

    def test_known_provider_errors_and_hidden_result(self):
        for error, expected in (("Invalid API key · Please run /login", "authentication_error"),
                                ("You've hit your limit · resets later", "usage_limit"),
                                ("429 rate_limit_error", "usage_limit"),
                                ("model_not_found", "model_unavailable")):
            result = D.classify([{"type": "result", "is_error": True, "result": error}])
            self.assertEqual(result["category"], expected)
        hidden = {"type": "result", "subtype": "success", "is_error": True,
                  "duration_ms": 224, "num_turns": 1, "modelUsage": {}}
        self.assertEqual(D.classify([hidden])["category"], "unknown_analysis_error")

    def test_ignore_source_and_tool_text_when_classifying(self):
        data = [{"type": "user", "content": "invalid_api_key"},
                {"type": "assistant", "message": {"content": "usage limit"}},
                {"type": "result", "is_error": True, "errors": ["other failure"]}]
        self.assertEqual(D.classify(data)["category"], "unknown_analysis_error")

    def test_sdk_bounds_and_missing_structured_output(self):
        self.assertEqual(D.classify([{"type": "result", "subtype": "error_max_turns"}])["category"],
                         "turn_limit")
        self.assertEqual(D.classify([{"type": "result", "subtype": "success", "is_error": False}])["category"],
                         "structured_output_missing")
        for data in ({}, [], ["bad"], [{"type": "user"}]):
            self.assertEqual(D.classify(data)["category"], "execution_record_unavailable")

    def test_cli_emits_only_fixed_fields_not_raw_or_escaped_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            secret = "sensitive-value-never-print"
            data = [{"type": "result", "is_error": True, "num_turns": 1,
                     "result": "usage limit " + secret + "\n::error::injected", "extra": secret}]
            Path(tmp, "claude-execution-output.json").write_text(json.dumps(data, ensure_ascii=True))
            result = subprocess.run([sys.executable, "-I", str(ROOT / "cai/diagnostics.py"), "failure"],
                                    env=dict(os.environ, RUNNER_TEMP=tmp), text=True, capture_output=True)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stdout), {"category": "usage_limit"})
            self.assertNotIn(secret, result.stdout + result.stderr)
            self.assertNotIn("::error::", result.stdout + result.stderr)

    def test_unavailable_malformed_oversized_and_symlink_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "claude-execution-output.json")
            self.assertEqual(D.read_failure(path), {"category": "execution_record_unavailable"})
            for value in ("not json", "x" * (D.MAX_BYTES + 1)):
                path.write_text(value)
                self.assertEqual(D.read_failure(path), {"category": "execution_record_unavailable"})
            path.unlink()
            target = Path(tmp, "other.json")
            target.write_text('[{"type":"result","result":"usage limit","is_error":true}]')
            path.symlink_to(target)
            self.assertEqual(D.read_failure(path), {"category": "execution_record_unavailable"})

    def test_cli_token_failure_never_echoes_input(self):
        value = "private-value\n"
        result = subprocess.run([sys.executable, "-I", str(ROOT / "cai/diagnostics.py"), "check-token"],
                                env=dict(os.environ, CAI_OAUTH_TOKEN=value), text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout), {"category": "oauth_token_whitespace"})
        self.assertNotIn("private-value", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()

"""Failure diagnostics must explain the category without forwarding provider text."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("diagnostics", ROOT / "cai/diagnostics.py")
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)


class DiagnosticsTests(unittest.TestCase):
    def test_token_checks_do_not_normalize_bad_values(self):
        self.assertEqual(D.token_category("opaque-token"), "token_present")
        self.assertEqual(D.token_category(""), "missing_oauth_token")
        for value in (" token", "token\n", "to\rken", "to\tken", "token\x00", "x\x7f",
                      "x\u00a0", "x\u200b", "x\ufeff", "x\x80"):
            self.assertEqual(D.token_category(value), "oauth_token_invalid_characters")

    def test_known_provider_errors_and_hidden_result(self):
        for error, expected in (("Invalid API key · Please run /login", "authentication_error"),
                                ("You've hit your limit · resets later", "usage_limit"),
                                ("429 rate_limit_error", "usage_limit"),
                                ("API Error: 401", "authentication_error"),
                                ("HTTP 429", "usage_limit"),
                                ("invalid_api_key while checking quota", "authentication_error"),
                                ("Credit balance is too low", "billing_error"),
                                ("model_not_found", "model_unavailable")):
            result = D.classify([{"type": "result", "is_error": True, "result": error}])
            self.assertEqual(result["category"], expected)
        hidden = {"type": "result", "subtype": "success", "is_error": True,
                  "duration_ms": 224, "num_turns": 1, "modelUsage": {}}
        self.assertEqual(D.classify([hidden])["category"], "unknown_analysis_error")
        self.assertEqual(D.classify([{"type": "result", "is_error": True,
                                     "result": "request ID abc429xyz failed"}])["category"],
                         "unknown_analysis_error")

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

    def test_structured_rate_limit_reset_and_credit_rejection(self):
        now = 1_800_000_000
        event = {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": now + 7200}}
        with patch.object(D.time, "time", return_value=now):
            self.assertEqual(D.classify([event]), {"category": "usage_limit", "reset_at": now + 7200})
            self.assertEqual(D.classify([event, {"type": "result", "subtype": "success", "is_error": False,
                                                "structured_output": {"ok": True}}]),
                             {"category": "upstream_action_failure"})
            for reset in (True, "tomorrow", now - 1, 10**50, now + 14 * 86400):
                event["rate_limit_info"]["resetsAt"] = reset
                self.assertEqual(D.classify([event]), {"category": "usage_limit"})
            event["rate_limit_info"]["errorCode"] = "credits_required"
            self.assertEqual(D.classify([event]), {"category": "billing_error"})

    def test_warning_or_nested_events_do_not_authorize_quota_retry(self):
        for status in ("allowed", "allowed_warning"):
            self.assertEqual(D.classify([{"type": "rate_limit_event", "rate_limit_info": {"status": status}}]),
                             D.UNAVAILABLE)
        nested = {"type": "tool_result", "content": {"type": "rate_limit_event",
                  "rate_limit_info": {"status": "rejected"}}}
        self.assertEqual(D.classify([nested]), D.UNAVAILABLE)

    def test_cli_exports_only_sanitized_job_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp, "outputs")
            Path(tmp, "claude-execution-output.json").write_text(json.dumps([
                {"type": "result", "is_error": True, "result": "usage limit\nrun=true\nsecret=hidden"}]))
            result = subprocess.run([sys.executable, "-I", str(ROOT / "cai/diagnostics.py"), "failure"],
                                    env=dict(os.environ, RUNNER_TEMP=tmp, GITHUB_OUTPUT=str(output)),
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_text(), "category=usage_limit\n")

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
        self.assertEqual(json.loads(result.stdout), {"category": "oauth_token_invalid_characters"})
        self.assertNotIn("private-value", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()

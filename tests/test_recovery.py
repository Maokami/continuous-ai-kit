"""Exercise persisted quota waits, clock-driven resumption and workflow write gates."""

import copy
import json
import os
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cai import recovery as R
import test_scan as S
from test_scan import API, BATCHES, DIGEST, SOURCES, m, state, issue

NOW = 1_800_000_000


class PolicyTests(unittest.TestCase):
    def waiting(self, **kwargs):
        return R.record({}, "usage_limit", "123", now=NOW, **kwargs)

    def test_empty_and_success_are_not_recovery_work(self):
        for value in ({}, {"status": "ready", "run_id": "122"}):
            self.assertEqual(R.gate(value, recovery_only=True)["run"], "false")
            self.assertEqual(R.gate(value)["run"], "true")

    def test_wait_and_due_boundary(self):
        value = self.waiting()
        self.assertTrue(NOW + 3600 <= value["next_attempt_at"] <= NOW + 4500)
        self.assertEqual(R.gate(value, now=value["next_attempt_at"] - 1)["run"], "false")
        self.assertEqual(R.gate(value, now=value["next_attempt_at"], recovery_only=True)["run"], "true")

    def test_reset_time_with_safety_margin_and_invalid_hints(self):
        value = self.waiting(reset_at=NOW + 7 * 86400)
        self.assertTrue(NOW + 7 * 86400 + 300 <= value["next_attempt_at"] <= NOW + 7 * 86400 + 1200)
        for invalid in (True, "tomorrow", NOW - 1, NOW + R.MAX_WAIT, 10**100):
            self.assertEqual(self.waiting(reset_at=invalid), self.waiting())

    def test_backoff_is_capped_and_quota_budget_is_finite(self):
        value = {}
        for count in range(1, R.MAX_DEFERRALS + 1):
            value = R.record(value, "usage_limit", str(count), now=NOW)
            self.assertEqual(value["deferrals"], count)
            self.assertLessEqual(value["next_attempt_at"] - NOW, R.MAX_BACKOFF + 900)
            R.validate(value)
        self.assertEqual(value["status"], "exhausted")
        self.assertEqual(R.gate(value, now=NOW + 86400)["run"], "false")

    def test_deadline_does_not_move_with_later_failures(self):
        value = self.waiting()
        later = R.record(value, "usage_limit", "124", now=NOW + R.MAX_WAIT - 100)
        self.assertEqual(later["first_limited_at"], NOW)
        self.assertEqual(later["next_attempt_at"], NOW + R.MAX_WAIT)
        self.assertEqual(R.gate(later, now=NOW + R.MAX_WAIT)["reason"], "quota_recovery_exhausted")

    def test_duplicate_and_old_runs_cannot_extend_or_clear_wait(self):
        value = self.waiting()
        for run in ("122", "123"):
            for category in ("usage_limit", "success"):
                self.assertEqual(R.record(value, category, run, now=NOW + 100), value)
            self.assertEqual(R.gate(value, now=NOW + 86400, run_id=run)["run"], "false")

    def test_non_quota_result_ends_recovery(self):
        for category in ("success", "authentication_error", "billing_error", "turn_limit", ""):
            result = R.record(self.waiting(), category, "124", now=NOW)
            self.assertEqual(result, {"status": "ready", "run_id": "124"})
            self.assertEqual(R.gate(result, recovery_only=True)["run"], "false")

    def test_corrupt_state_fails_closed(self):
        for value in ([], None, True, {"status": "ready"}, {"status": "ready", "run_id": "x"},
                      {**self.waiting(), "deferrals": True}, {**self.waiting(), "deferrals": 100},
                      {**self.waiting(), "next_attempt_at": NOW - 1},
                      {**self.waiting(), "next_attempt_at": NOW + R.MAX_WAIT + 1},
                      {**self.waiting(), "extra": "untrusted"}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                R.gate(value)


class ScanRecoveryTests(unittest.TestCase):
    setUp = S.Tests.setUp

    def defer(self, api):
        with patch.dict(os.environ, {"CAI_FAILURE_CATEGORY": "usage_limit"}), patch.object(R.time, "time", return_value=NOW):
            m.error(api)
        return m.load_state(api.issues)[1]

    def test_full_wait_resume_publish_cycle_and_success_noop(self):
        api = API()
        self.assertEqual(m.prepare(api)["run"], "true")
        saved = self.defer(api)
        self.assertEqual(saved["batches"]["0"]["status"], "waiting_for_quota")
        self.assertEqual(saved["batches"]["0"]["attempts"], 1)
        due = saved["quota"]["next_attempt_at"]
        writes = len(api.writes)
        with patch.dict(os.environ, {"GITHUB_RUN_ID": "124", "RECOVERY_ONLY": "true"}):
            with patch.object(R.time, "time", return_value=due - 1):
                self.assertEqual(m.prepare(api)["run"], "false")
                self.assertEqual(len(api.writes), writes)
            with patch.object(R.time, "time", return_value=due):
                self.assertEqual(m.prepare(api)["run"], "true")
                m.publish(api)
        saved = m.load_state(api.issues)[1]
        self.assertEqual(saved["batches"]["0"]["status"], "success")
        self.assertEqual(saved["batches"]["0"]["attempts"], 1)
        self.assertEqual(saved["quota"]["status"], "ready")
        writes = len(api.writes)
        with patch.dict(os.environ, {"GITHUB_RUN_ID": "125", "RECOVERY_ONLY": "true"}):
            self.assertEqual(m.prepare(api)["run"], "false")
        self.assertEqual(len(api.writes), writes)
        self.assertEqual(len([i for i in api.issues if i["body"].startswith(m.REPORT)]), 1)

    def test_repeated_quota_limits_do_not_spend_task_attempts(self):
        api = API(state())
        saved = self.defer(api)
        with patch.dict(os.environ, {"GITHUB_RUN_ID": "124", "CAI_FAILURE_CATEGORY": "usage_limit"}), \
                patch.object(R.time, "time", return_value=saved["quota"]["next_attempt_at"]):
            self.assertEqual(m.prepare(api)["run"], "true")
            m.error(api)
        saved = m.load_state(api.issues)[1]
        self.assertEqual(saved["quota"]["deferrals"], 2)
        self.assertEqual(saved["batches"]["0"]["attempts"], 1)

    def test_quota_on_last_manual_attempt_preserves_failure_budget(self):
        api = API(state(attempts=2))
        saved = self.defer(api)
        with patch.dict(os.environ, {"GITHUB_RUN_ID": "124"}), \
                patch.object(R.time, "time", return_value=saved["quota"]["next_attempt_at"]):
            self.assertEqual(m.prepare(api)["run"], "true")
            m.error(api)
        saved = m.load_state(api.issues)[1]
        self.assertEqual(saved["batches"]["0"]["attempts"], 2)
        self.assertEqual(saved["batches"]["0"]["status"], "error")
        self.assertIsNone(m.select_batch(BATCHES, saved, DIGEST, "0", True))

    def test_source_change_cannot_bypass_cooldown_and_resume_uses_current_source(self):
        api = API(state())
        saved = self.defer(api)
        due = saved["quota"]["next_attempt_at"]
        with patch.dict(os.environ, {"GITHUB_RUN_ID": "124", "RECOVERY_ONLY": "true"}), \
                patch.object(m, "source", return_value=(SOURCES, BATCHES, "c" * 64)):
            with patch.object(R.time, "time", return_value=due - 1):
                self.assertEqual(m.prepare(api)["run"], "false")
            with patch.object(R.time, "time", return_value=due):
                self.assertEqual(m.prepare(api)["run"], "true")
        self.assertEqual(json.loads((self.out / "plan.json").read_text())["digest"], "c" * 64)
        self.assertEqual(m.load_state(api.issues)[1]["quota"]["deferrals"], 1)

    def test_deferred_batch_has_priority_over_unvisited_work(self):
        saved = state("waiting_for_quota")
        self.assertEqual(m.select_batch([[], []], saved, DIGEST, "auto", False), (0, 1))

    def test_recovery_only_does_not_start_fresh_or_generic_failed_work(self):
        for original in (None, state("error"), state("success")):
            api = API(original)
            with patch.dict(os.environ, {"RECOVERY_ONLY": "true", "GITHUB_RUN_ID": "124"}):
                self.assertEqual(m.prepare(api)["run"], "false")
            self.assertEqual(api.writes, [])

    def test_duplicate_failure_is_idempotent_and_cannot_overwrite_other_run(self):
        api = API(state())
        saved = self.defer(api)
        self.assertEqual(self.defer(api), saved)
        with patch.dict(os.environ, {"GITHUB_RUN_ID": "124"}):
            self.assertEqual(self.defer(api), saved)

    def test_exhausted_wait_can_only_be_reset_explicitly(self):
        api = API(state())
        saved = self.defer(api)
        saved["quota"]["status"] = "exhausted"
        api.issues[0] = issue(saved)
        with patch.dict(os.environ, {"GITHUB_RUN_ID": "124"}):
            self.assertEqual(m.prepare(api)["run"], "false")
            with patch.dict(os.environ, {"RESET_QUOTA": "true"}):
                self.assertEqual(m.prepare(api)["run"], "true")

    def test_unknown_and_billing_errors_do_not_create_waits(self):
        for category in ("authentication_error", "billing_error", "unknown_analysis_error"):
            api = API(state())
            with patch.dict(os.environ, {"CAI_FAILURE_CATEGORY": category}):
                m.error(api)
            saved = m.load_state(api.issues)[1]
            self.assertEqual(saved["batches"]["0"]["status"], "error")
            self.assertNotIn("quota", saved)


class TriageAPI(API):
    def __init__(self):
        super().__init__()
        self.labels = {}

    def request(self, path, data=None, method=None, *, missing_ok=False):
        if "/labels/" in path:
            return self.labels.get(path.rsplit("/", 1)[1])
        if path.endswith("/labels") and data:
            self.labels[data["name"]] = data
            self.writes.append((path, data, method))
            return data
        if data is None and "/issues?" in path:
            label = path.split("labels=", 1)[1].split("&", 1)[0]
            return [i for i in self.issues if label in i.get("labels", [])]
        return super().request(path, data, method)


class TriageRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.api = TriageAPI()
        self.env = patch.dict(os.environ, {"GITHUB_RUN_ID": "123", "RECOVERY_ONLY": "false"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def update(self, operation, **kwargs):
        return R.triage_update(self.api, "triage", operation, **kwargs)

    def test_no_quota_history_has_no_write_and_recovery_only_skips(self):
        self.assertEqual(self.update("check")["run"], "true")
        self.update("record", category="success")
        self.assertEqual(self.api.writes, [])
        with patch.dict(os.environ, {"RECOVERY_ONLY": "true"}):
            self.assertEqual(self.update("check")["run"], "false")

    def test_persist_wait_resume_clear_and_no_duplicate_update(self):
        with patch.object(R.time, "time", return_value=NOW):
            self.update("record", category="usage_limit")
        saved = R.triage_state(self.api, "triage")[1]
        count = len(self.api.writes)
        self.update("record", category="usage_limit")
        self.assertEqual(len(self.api.writes), count)
        with patch.dict(os.environ, {"GITHUB_RUN_ID": "124", "RECOVERY_ONLY": "true"}):
            with patch.object(R.time, "time", return_value=saved["next_attempt_at"] - 1):
                self.assertEqual(self.update("check")["run"], "false")
            with patch.object(R.time, "time", return_value=saved["next_attempt_at"]):
                self.assertEqual(self.update("check")["run"], "true")
            self.update("record", category="success")
        self.assertEqual(len(self.api.issues), 1)
        with patch.dict(os.environ, {"GITHUB_RUN_ID": "125", "RECOVERY_ONLY": "true"}):
            self.assertEqual(self.update("check")["run"], "false")

    def test_spoof_duplicate_and_corrupt_ledgers(self):
        self.update("record", category="usage_limit")
        self.api.issues[0]["user"]["login"] = "attacker"
        self.assertEqual(R.triage_state(self.api, "triage")[1], {})
        self.api.issues[0]["user"]["login"] = "github-actions[bot]"
        self.api.issues.append(copy.deepcopy(self.api.issues[0]))
        with self.assertRaises(ValueError):
            self.update("check")
        self.api.issues.pop()
        self.api.issues[0]["body"] = self.api.issues[0]["body"].split("\n", 1)[0] + '\n{"status":"garbage"}'
        with self.assertRaises(ValueError):
            self.update("check")

    def test_lane_isolation_and_explicit_reset(self):
        self.update("record", category="usage_limit")
        self.assertEqual(R.triage_state(self.api, "other")[1], {})
        self.update("reset")
        self.assertEqual(R.triage_state(self.api, "triage")[1], {})

    def test_large_repository_uses_only_label_scoped_history(self):
        class LargeAPI(TriageAPI):
            def request(self, path, data=None, method=None, *, missing_ok=False):
                if data is None and "/issues?" in path:
                    if "labels=cai-quota-" in path:
                        return []
                    return [{"pull_request": {}, "number": i} for i in range(100)]
                return super().request(path, data, method, missing_ok=missing_ok)
        self.assertEqual(R.triage_update(LargeAPI(), "triage", "check")["run"], "true")

    def test_label_is_created_once_and_reused_without_touching_existing_label(self):
        label = R.triage_label("triage")
        for existing in (False, True):
            with self.subTest(existing=existing):
                self.api = TriageAPI()
                if existing:
                    self.api.labels[label] = {"name": label, "color": "abcdef"}
                with patch.object(R.time, "time", return_value=NOW):
                    self.update("record", category="usage_limit")
                self.assertEqual(self.api.issues[0]["labels"], [label])
                with patch.dict(os.environ, {"GITHUB_RUN_ID": "124"}), \
                        patch.object(R.time, "time", return_value=NOW + 86400):
                    self.update("record", category="usage_limit")
                self.assertEqual(len(self.api.issues), 1)
                self.assertEqual(len([w for w in self.api.writes if w[0].endswith("/labels")]),
                                 0 if existing else 1)
                self.assertEqual(self.api.writes[-1][2], "PATCH")
                if existing:
                    self.assertEqual(self.api.labels[label]["color"], "abcdef")


def job_condition(path, job):
    block = path.read_text().split("\n  " + job + ":\n", 1)[1]
    value = block.split("    if: ", 1)[1].split("\n    runs-on:", 1)[0]
    return " ".join(value.removeprefix(">-\n").split())


def condition_value(expression, values):
    expression = expression.replace("always()", "True").replace("&&", " and ").replace("||", " or ")
    expression = re.sub(r"needs\.[\w.-]+", lambda m: repr(values.get(m[0], "")), expression)
    return eval(expression, {"__builtins__": {}}, {})


class WorkflowRecoveryTests(unittest.TestCase):
    def test_scan_deferred_success_routes_to_record_not_publish(self):
        path = ROOT / ".github/workflows/scan-report.yml"
        values = {"needs.prepare.outputs.run": "true", "needs.scan.result": "success",
                  "needs.scan.outputs.failure_category": "usage_limit", "needs.publish.result": "skipped"}
        self.assertFalse(condition_value(job_condition(path, "publish"), values))
        self.assertTrue(condition_value(job_condition(path, "record-failure"), values))
        values.update({"needs.scan.outputs.failure_category": "", "needs.publish.result": "success"})
        self.assertTrue(condition_value(job_condition(path, "publish"), values))
        self.assertFalse(condition_value(job_condition(path, "record-failure"), values))

    def test_triage_quota_wait_cannot_publish_missing_ai(self):
        path = ROOT / ".github/workflows/pr-triage.yml"
        values = {"needs.collect.result": "success", "needs.collect.outputs.run": "true",
                  "needs.collect.outputs.facts_changed": "true", "needs.collect.outputs.pr_count": "2",
                  "needs.summarize.result": "success", "needs.summarize.outputs.failure_category": "usage_limit"}
        self.assertFalse(condition_value(job_condition(path, "publish"), values))
        self.assertTrue(condition_value(job_condition(path, "record-quota"), values))
        values.update({"needs.collect.outputs.run": "false", "needs.summarize.result": "skipped"})
        self.assertFalse(condition_value(job_condition(path, "publish"), values))
        self.assertFalse(condition_value(job_condition(path, "record-quota"), values))

    def test_triage_no_longer_needed_work_clears_wait(self):
        values = {"needs.collect.result": "success", "needs.collect.outputs.run": "true",
                  "needs.collect.outputs.facts_changed": "false", "needs.collect.outputs.pr_count": "2",
                  "needs.summarize.result": "skipped"}
        self.assertTrue(condition_value(job_condition(ROOT / ".github/workflows/pr-triage.yml", "record-quota"), values))


if __name__ == "__main__":
    unittest.main()

"""후보 조사에서 오래된 결과·위조 인용·중복 게시·반복 실패를 거부하는지 검사한다."""

import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("improvement", Path(__file__).resolve().parents[1] / "cai" / "scan.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
SHA = "a" * 40
DIGEST = "b" * 64
SOURCES = {"Kuratowski/A.lean": "namespace K\ntheorem foo : True := by trivial\nend K\n",
           "Kuratowski/B.lean": "theorem bar : True := by trivial\n"}
BATCHES = [list(SOURCES)]
PLAN = {"sha": SHA, "batch": 0, "digest": DIGEST, "required_files": BATCHES[0],
        "run_id": "123", "requested_model": "sonnet", "policy": "read-only-v1"}
CANDIDATE = {"path": "Kuratowski/A.lean", "declaration": "K.foo", "kind": "duplicate",
             "risk": "proof-only", "title": "중복 증명 검토", "reason": "두 증명을 대조할 필요",
             "proposal": "공개 진술을 보존하고 재사용 가능성 검토",
             "evidence": [{"path": "Kuratowski/A.lean", "line": 2,
                           "quote": "theorem foo : True := by trivial"}]}
RESULT = {"sha": SHA, "batch": 0, "reviewed_files": BATCHES[0], "summary": "미검증 후보 한 건",
          "candidates": [CANDIDATE]}


def state(status="pending", attempts=1):
    return {"version": 1, "batches": {"0": {"digest": DIGEST, "attempts": attempts,
            "status": status, "run_id": "123"}}, "seen": []}


def issue(data, number=1, **extra):
    return {"number": number, "title": m.TITLE, "user": {"login": "github-actions[bot]"},
            "body": m.MARKER + "\n" + json.dumps(data), **extra}


class API:
    repo = "owner/repo"

    def __init__(self, data=None):
        self.issues = [] if data is None else [issue(data)]
        self.writes = []
        self.head = SHA
        self.fail_state = False

    def request(self, path, data=None, method=None):
        if data is None:
            return {"object": {"sha": self.head}} if "/git/ref/" in path else self.issues
        self.writes.append((path, data, method))
        if method == "PATCH":
            if self.fail_state:
                raise OSError("게시 뒤 상태 쓰기 실패")
            self.issues[0]["body"] = data["body"]
            return self.issues[0]
        new = {"number": len(self.issues) + 1, "user": {"login": "github-actions[bot]"}, **data}
        self.issues.append(new)
        return new


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name)
        schema = Path(__file__).resolve().parent.parent / m.SCHEMA
        for obj, name, value in ((m, "SCHEMA", schema), (m, "OUT", self.out),
                                 (m, "source", lambda: (SOURCES, BATCHES, DIGEST)),
                                 (m, "git", lambda *args: SHA)):
            p = patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.dict(os.environ, {"GITHUB_RUN_ID": "123", "REVIEW_BATCH": "auto",
                                    "RETRY_FAILED": "false"})
        p.start()
        self.addCleanup(p.stop)
        (self.out / "plan.json").write_text(json.dumps(PLAN))
        (self.out / "review.json").write_text(json.dumps(RESULT))

    def validate(self, data=None, **kw):
        return m.validate(json.dumps(RESULT if data is None else data), PLAN, "success", **kw)

    def test_valid_and_noop(self):
        self.assertEqual(self.validate(), RESULT)
        data = copy.deepcopy(RESULT)
        data["candidates"] = []
        self.assertEqual(self.validate(data)["candidates"], [])

    def test_wrong_sha_batch_and_extra_fields(self):
        for field, value in (("sha", "c" * 40), ("batch", True), ("batch", 1), ("extra", "x")):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.validate({**RESULT, field: value})

    def test_failed_agent(self):
        with self.assertRaises(ValueError):
            m.validate(json.dumps(RESULT), PLAN, "failure")

    def test_missing_and_unsafe_review_files(self):
        for files in ([BATCHES[0][0]], [*BATCHES[0], "../secret"], [*BATCHES[0], BATCHES[0][0]]):
            with self.subTest(files=files), self.assertRaises(ValueError):
                self.validate({**RESULT, "reviewed_files": files})

    def test_forged_evidence(self):
        for field, value in (("quote", "not in source"), ("line", 1), ("line", True),
                             ("path", "../secret")):
            data = copy.deepcopy(RESULT)
            data["candidates"][0]["evidence"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.validate(data)

    def test_bad_declaration_and_candidate_scope(self):
        for field, value in (("declaration", "missing"), ("path", "../secret"),
                             ("risk", "automatic"), ("kind", "rename")):
            data = copy.deepcopy(RESULT)
            data["candidates"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.validate(data)

    def test_qualified_lean_declaration(self):
        line = "theorem RotationSystem.exists_addEdge : True := by trivial"
        sources = {**SOURCES, "Kuratowski/A.lean": "namespace Kuratowski\n" + line + "\n"}
        with patch.object(m, "source", return_value=(sources, BATCHES, DIGEST)):
            for name in ("exists_addEdge", "RotationSystem.exists_addEdge",
                         "Kuratowski.RotationSystem.exists_addEdge"):
                data = copy.deepcopy(RESULT)
                data["candidates"][0]["declaration"] = name
                data["candidates"][0]["evidence"][0]["quote"] = line
                with self.subTest(name=name):
                    self.assertEqual(self.validate(data), data)

    def test_qualified_declaration_still_requires_exact_leaf_and_kind(self):
        for declaration in ("theorem RotationSystem.foobar", "theorem RotationSystem.foo_extra",
                            "instance RotationSystem.foo", "theorem RotationSystem.bar"):
            line = declaration + " : True := by trivial"
            sources = {**SOURCES, "Kuratowski/A.lean": "namespace K\n" + line + "\n"}
            data = copy.deepcopy(RESULT)
            data["candidates"][0]["evidence"][0]["quote"] = line
            with self.subTest(declaration=declaration), \
                    patch.object(m, "source", return_value=(sources, BATCHES, DIGEST)), \
                    self.assertRaises(ValueError):
                self.validate(data)

    def test_duplicate_and_excess_candidates(self):
        for candidates in ([CANDIDATE] * 2, [CANDIDATE] * 4):
            with self.assertRaises(ValueError):
                self.validate({**RESULT, "candidates": candidates})

    def test_encoded_secret(self):
        secret = "private-token-value"
        data = {**RESULT, "summary": secret}
        raw = json.dumps(data).replace(secret, "".join("\\u%04x" % ord(c) for c in secret))
        with self.assertRaises(ValueError):
            m.validate(raw, PLAN, "success", (secret,))

    def test_source_change(self):
        with patch.object(m, "source", return_value=(SOURCES, BATCHES, "c" * 64)):
            with self.assertRaises(ValueError):
                self.validate()

    def test_no_repeat_and_bounded_retry(self):
        for status in ("pending", "success", "error"):
            self.assertIsNone(m.select_batch(BATCHES, state(status), DIGEST, "auto", False))
        self.assertEqual(m.select_batch(BATCHES, state("error"), DIGEST, "0", True), (0, 2))
        self.assertIsNone(m.select_batch(BATCHES, state("error", 2), DIGEST, "0", True))
        self.assertIsNone(m.select_batch(BATCHES, state("success"), DIGEST, "0", True))
        self.assertEqual(m.select_batch(BATCHES, state(), "c" * 64, "auto", False), (0, 1))

    def test_invalid_prepare_input_does_not_write(self):
        for batch in ("$(bad)", "0\nrun=true", "999", "-1"):
            api = API()
            with patch.dict(os.environ, {"REVIEW_BATCH": batch}), self.assertRaises(ValueError):
                m.prepare(api)
            self.assertEqual(api.writes, [])

    def test_auto_rotation_does_not_starve_later_batches(self):
        history = state()
        self.assertEqual(m.select_batch([[], []], history, "c" * 64, "auto", False), (1, 1))
        history["batches"]["1"] = {**history["batches"]["0"], "run_id": "124"}
        self.assertEqual(m.select_batch([[], []], history, "c" * 64, "auto", False), (0, 1))

    def test_stale_prepare_skips_without_reservation(self):
        api = API()
        api.head = "c" * 40
        self.assertEqual(m.prepare(api), {"run": "false"})
        self.assertEqual(api.writes, [])

    def test_prepare_reserves_before_model(self):
        api = API()
        with patch.object(m.SCHEMA.__class__, "read_text", return_value='{"type":"object"}'):
            self.assertEqual(m.prepare(api)["run"], "true")
        _, saved = m.load_state(api.issues)
        self.assertEqual(saved["batches"]["0"]["status"], "pending")
        self.assertEqual(m.prepare(api), {"run": "false"})

    def test_state_spoof_and_corruption(self):
        spoof = issue(state(), user={"login": "attacker"})
        self.assertIsNone(m.load_state([spoof])[0])
        with self.assertRaises(ValueError):
            m.load_state([issue(state()), issue(state(), number=2)])
        bad = state()
        bad["batches"]["0"]["attempts"] = 99
        with self.assertRaises(ValueError):
            m.load_state([issue(bad)])

    def test_publish_and_dedup(self):
        api = API(state())
        m.publish(api)
        reports = [i for i in api.issues if (i.get("body") or "").startswith(m.REPORT)]
        self.assertEqual(len(reports), 1)
        with self.assertRaises(ValueError):
            m.publish(api)
        saved = m.load_state(api.issues)[1]
        saved["batches"]["0"]["status"] = "pending"
        api.issues[0] = issue(saved)
        m.publish(api)
        self.assertEqual(len(api.issues), 2)

    def test_publication_recovery_does_not_duplicate(self):
        api = API(state())
        api.fail_state = True
        with self.assertRaises(OSError):
            m.publish(api)
        api.fail_state = False
        m.publish(api)
        self.assertEqual(len(api.issues), 2)

    def test_recovery_with_different_candidates_tracks_only_posted_ids(self):
        api = API(state())
        api.fail_state = True
        with self.assertRaises(OSError):
            m.publish(api)
        api.fail_state = False
        m.error(api)
        data = copy.deepcopy(RESULT)
        candidate = data["candidates"][0]
        candidate["path"] = "Kuratowski/B.lean"
        candidate["declaration"] = "bar"
        candidate["evidence"] = [{"path": "Kuratowski/B.lean", "line": 1,
                                  "quote": "theorem bar : True := by trivial"}]
        (self.out / "review.json").write_text(json.dumps(data))
        with patch.dict(os.environ, {"GITHUB_RUN_ID": "124", "REVIEW_BATCH": "0", "RETRY_FAILED": "true"}):
            m.prepare(api)
            m.publish(api)
        saved = m.load_state(api.issues)[1]
        self.assertEqual(set(saved["seen"]), {m.candidate_id(CANDIDATE), m.candidate_id(candidate)})
        self.assertEqual(m.posted_ids(api.issues), set(saved["seen"]))
        self.assertEqual(len(api.issues), 3)

    def test_malformed_publication_ledger_fails_closed(self):
        api = API(state())
        api.issues.append({"number": 2, "user": {"login": "github-actions[bot]"},
                           "body": m.REPORT + "broken", "title": "old report"})
        with self.assertRaises(ValueError):
            m.publish(api)
        self.assertEqual(api.writes, [])

    def test_stale_head_and_other_run_do_not_publish(self):
        api = API(state())
        api.head = "c" * 40
        with self.assertRaises(ValueError):
            m.publish(api)
        self.assertEqual(api.writes, [])
        api.head = SHA
        with patch.dict(os.environ, {"GITHUB_RUN_ID": "124"}), self.assertRaises(ValueError):
            m.publish(api)
        self.assertEqual(api.writes, [])

    def test_failure_does_not_overwrite_success_or_other_run(self):
        for status in ("pending", "success"):
            api = API(state(status))
            m.error(api)
            self.assertEqual(m.load_state(api.issues)[1]["batches"]["0"]["status"],
                             "error" if status == "pending" else "success")
        api = API(state())
        with patch.dict(os.environ, {"GITHUB_RUN_ID": "124"}):
            m.error(api)
        self.assertEqual(m.load_state(api.issues)[1]["batches"]["0"]["status"], "pending")

    def test_mentions_and_html_are_neutralized(self):
        self.assertEqual(m.safe("@user <!-- x -->"), "＠user &lt;!-- x --&gt;")


if __name__ == "__main__":
    unittest.main()

"""Compatibility tests: cai/triage.py with the default config behaves like scripts/pr_triage.py in
smarandache-wellin-lean. Copied from the original tests/test_pr_triage.py (sha256 47dab9a7...); only the
import path changed.
Run: python3 -I -m unittest discover -s tests -p 'test_triage*.py' -v

The fixture is a reduced copy of the three open PRs of 2026-11-06 (real `gh pr list --json` shape):
  #2 Dependabot, green CI, review pending / #3 Dependabot, CI failing / #4 hunter PR, green CI, review pending
"""
import copy
import importlib.util
import pathlib
import unittest
from typing import Optional

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("triage", ROOT / "cai" / "triage.py")
assert spec is not None and spec.loader is not None
T = importlib.util.module_from_spec(spec)
spec.loader.exec_module(T)


def run(name: str, workflow: str, conclusion: Optional[str] = "SUCCESS", status: str = "COMPLETED"):
    return {"__typename": "CheckRun", "name": name, "workflowName": workflow, "status": status, "conclusion": conclusion}


GREEN = [run("Build blueprint", "Blueprint"), run("Lean build and audits", "CI"),
         run("Build blueprint and deploy", "Blueprint", "SKIPPED")]
DEP_LABELS = [{"name": "dependencies"}, {"name": "github_actions"}]


def raw_pr(number, head, author, checks, review: Optional[str] = "REVIEW_REQUIRED", mergeable="MERGEABLE",
           state="BLOCKED", draft=False, labels=None, title=None):
    return {"number": number, "title": title or f"PR {number}", "url": f"https://example/{number}",
            "headRefOid": head, "isDraft": draft, "mergeable": mergeable, "mergeStateStatus": state,
            "reviewDecision": review, "author": {"login": author},
            "labels": list(labels or []), "statusCheckRollup": checks}


def current_prs():
    return [
        raw_pr(2, "e4d5c8800000", "app/dependabot", GREEN, labels=DEP_LABELS),
        raw_pr(3, "5d27a2200000", "app/dependabot",
               [run("Lean build and audits", "CI", "FAILURE"), run("review", "Claude proof review", "FAILURE"),
                run("Build blueprint", "Blueprint"), run("Build blueprint and deploy", "Blueprint", "SKIPPED")],
               labels=DEP_LABELS),
        raw_pr(4, "a17c05000000", "app/claude", GREEN),
    ]


class FakeGH:
    """gh test double. Writes are recorded, not executed. Only the methods used by publish are mimicked."""

    def __init__(self, prs, issues=None, existing_labels=()):
        self.repo = "o/r"
        self.prs = prs
        self.issues = issues or []
        self.existing = set(existing_labels)
        self.mutations = []

    def list_prs(self):
        return copy.deepcopy(self.prs)

    def list_report_issues(self):
        return copy.deepcopy(self.issues)

    def label_exists(self, name):
        return name in self.existing

    def create_label(self, name):
        self.mutations.append(("create_label", name))
        self.existing.add(name)

    def add_labels(self, number, names):
        self.mutations.append(("add", number, tuple(names)))
        for pr in self.prs:
            if pr["number"] == number:
                pr["labels"] += [{"name": n} for n in names]

    def remove_label(self, number, name):
        self.mutations.append(("remove", number, name))
        for pr in self.prs:
            if pr["number"] == number:
                pr["labels"] = [l for l in pr["labels"] if l["name"] != name]

    def update_issue(self, number, body):
        self.mutations.append(("update_issue", number))
        for i in self.issues:
            if i["number"] == number:
                i["body"] = body

    def create_issue(self, body):
        self.mutations.append(("create_issue",))
        self.issues.append(bot_report(99, body))


def bot_report(number, body, title=None):
    return {"number": number, "title": title or T.REPORT_TITLE, "author": {"login": "app/github-actions"},
            "labels": [{"name": T.REPORT_LABEL}], "body": body, "state": "OPEN"}


def ai_for(snapshot, **override):
    items = [{"number": p["number"], "priority": "보통", "next_action": f"#{p['number']} 확인"} for p in snapshot["prs"]]
    out = {"digest": snapshot["digest"], "items": items}
    out.update(override)
    return out


class Classification(unittest.TestCase):
    def labels(self, raw):
        return T.classify(T.normalize_pr(raw))

    def test_current_prs(self):
        got = {r["number"]: self.labels(r) for r in current_prs()}
        self.assertEqual(got[2], {"pr-triage/needs-review"})
        self.assertEqual(got[3], {"pr-triage/ci-failing", "pr-triage/needs-review"})
        self.assertEqual(got[4], {"pr-triage/needs-review"})

    def test_green_ci_without_approval_is_not_ready(self):
        for review in ("REVIEW_REQUIRED", "CHANGES_REQUESTED", None):
            labels = self.labels(raw_pr(5, "abc", "u", GREEN, review=review, state="CLEAN"))
            self.assertNotIn("pr-triage/ready", labels, review)

    def test_ready_needs_every_fact(self):
        self.assertEqual(self.labels(raw_pr(5, "abc", "u", GREEN, review="APPROVED", state="CLEAN")),
                         {"pr-triage/ready"})

    def test_unresolved_merge_state_is_blocked_not_empty(self):
        for state in ("BLOCKED", "BEHIND", "UNSTABLE"):
            self.assertEqual(self.labels(raw_pr(5, "abc", "u", GREEN, review="APPROVED", state=state)),
                             {"pr-triage/blocked"}, state)

    def test_classification_is_never_empty(self):
        ci_variants = {
            "pass": GREEN,
            "fail": [run("Lean build and audits", "CI", "FAILURE")],
            "pending": [run("Lean build and audits", "CI", None, "IN_PROGRESS")],
            "required skipped": [run("Lean build and audits", "CI", "SKIPPED")],
            "none": [],
        }
        for ci, checks in ci_variants.items():
            for review in ("APPROVED", "REVIEW_REQUIRED", "CHANGES_REQUESTED", None):
                for mergeable in ("MERGEABLE", "CONFLICTING", "UNKNOWN", None):
                    for state in ("CLEAN", "HAS_HOOKS", "BLOCKED", "BEHIND", "UNSTABLE", "UNKNOWN", None):
                        for draft in (True, False, None):
                            raw = raw_pr(5, "abc", "u", checks, review=review, mergeable=mergeable,
                                         state=state, draft=draft)
                            self.assertTrue(self.labels(raw), (ci, review, mergeable, state, draft))

    def test_required_check_must_actually_succeed(self):
        deploy = run("Build blueprint and deploy", "Blueprint", "SKIPPED")
        ok = [run("Lean build and audits", "CI"), deploy]
        self.assertEqual(T.normalize_pr(raw_pr(5, "abc", "u", ok))["ci"], "pass")  # optional deploy SKIPPED is fine
        for conclusion in ("SKIPPED", "NEUTRAL"):
            checks = [run("Lean build and audits", "CI", conclusion), deploy]
            pr = T.normalize_pr(raw_pr(5, "abc", "u", checks, review="APPROVED", state="CLEAN"))
            self.assertEqual(pr["ci"], "unknown", conclusion)
            self.assertNotIn("pr-triage/ready", T.classify(pr), conclusion)

    def test_missing_or_unknown_facts_are_unknown(self):
        cases = {
            "mergeable UNKNOWN": raw_pr(5, "abc", "u", GREEN, review="APPROVED", mergeable="UNKNOWN", state="CLEAN"),
            "review 없음": raw_pr(5, "abc", "u", GREEN, review=None, state="CLEAN"),
            "필수 검사 없음": raw_pr(5, "abc", "u", [run("Build blueprint", "Blueprint")], review="APPROVED", state="CLEAN"),
            "검사 없음": raw_pr(5, "abc", "u", [], review="APPROVED", state="CLEAN"),
            "취소된 검사": raw_pr(5, "abc", "u", [run("Lean build and audits", "CI", "CANCELLED")],
                             review="APPROVED", state="CLEAN"),
            "head 없음": raw_pr(5, None, "u", GREEN, review="APPROVED", state="CLEAN"),
            "draft 없음": dict(raw_pr(5, "abc", "u", GREEN, review="APPROVED", state="CLEAN"), isDraft=None),
        }
        for name, raw in cases.items():
            labels = self.labels(raw)
            self.assertIn("pr-triage/unknown", labels, name)
            self.assertNotIn("pr-triage/ready", labels, name)

    def test_pending_draft_conflict(self):
        labels = self.labels(raw_pr(5, "abc", "u", [run("Lean build and audits", "CI", None, "IN_PROGRESS")],
                                    draft=True, mergeable="CONFLICTING"))
        self.assertEqual(labels, {"pr-triage/ci-pending", "pr-triage/draft", "pr-triage/conflict",
                                  "pr-triage/needs-review"})


class Labels(unittest.TestCase):
    def test_unmanaged_labels_are_preserved(self):
        raw = raw_pr(2, "e4d", "app/dependabot", GREEN,
                     labels=DEP_LABELS + [{"name": "pr-triage/ready"}, {"name": "pr-triage-other"}])
        add, remove = T.plan_labels(T.normalize_pr(raw))
        self.assertEqual(add, ["pr-triage/needs-review"])
        self.assertEqual(remove, ["pr-triage/ready"])
        gh = FakeGH([raw], existing_labels=T.LABELS)
        snap = T.collect(gh)
        T.publish(gh, snap, ai_for(snap), "run", "now")
        names = {l["name"] for l in gh.prs[0]["labels"]}
        self.assertTrue({"dependencies", "github_actions", "pr-triage-other"} <= names)
        self.assertNotIn(("remove", 2, "dependencies"), gh.mutations)


class GhArguments(unittest.TestCase):
    """Real gh argument level. The "/" in label names must be encoded in REST paths."""

    def setUp(self):
        self.calls = []

        def runner(args, stdin):
            self.calls.append(args)
            return 0, "[]", ""
        self.gh = T.GH("o/r", runner=runner)

    def test_label_paths_are_encoded(self):
        self.gh.label_exists("pr-triage/needs-review")
        self.gh.remove_label(4, "pr-triage/ready")
        self.assertEqual(self.calls[0], ["api", "repos/o/r/labels/pr-triage%2Fneeds-review"])
        self.assertEqual(self.calls[1], ["api", "-X", "DELETE", "repos/o/r/issues/4/labels/pr-triage%2Fready"])

    def test_add_labels_sends_names_in_body(self):
        self.gh.add_labels(4, ["pr-triage/needs-review"])
        self.assertEqual(self.calls[0], ["api", "-X", "POST", "repos/o/r/issues/4/labels",
                                         "-f", "labels[]=pr-triage/needs-review"])

    def test_lists_ask_for_one_more_than_the_limit(self):
        self.gh.list_prs()
        self.gh.list_report_issues()
        self.assertIn(str(T.PR_LIMIT + 1), self.calls[0])
        self.assertIn(str(T.REPORT_LIMIT + 1), self.calls[1])
        self.assertIn("number,title,author,labels,body,state", self.calls[1])

    def test_dry_run_does_not_call_mutations(self):
        gh = T.GH("o/r", dry_run=True, runner=lambda a, s: self.calls.append(a) or (0, "", ""))
        gh.add_labels(4, ["pr-triage/x"])
        gh.remove_label(4, "pr-triage/x")
        gh.update_issue(9, "body")
        gh.create_issue("body")
        gh.create_label("pr-triage/ready")
        self.assertEqual(self.calls, [])


class Limits(unittest.TestCase):
    def test_truncated_pr_list_is_refused(self):
        prs = [raw_pr(n, f"h{n}", "u", GREEN) for n in range(1, T.PR_LIMIT + 2)]
        with self.assertRaises(T.TriageError):
            T.collect(FakeGH(prs))

    def test_too_many_report_issues_is_refused(self):
        issues = [bot_report(n, "") for n in range(1, T.REPORT_LIMIT + 2)]
        with self.assertRaises(T.TriageError):
            T.collect(FakeGH(current_prs(), issues=issues))


class Publishing(unittest.TestCase):
    def test_first_publish_then_quiet(self):
        gh = FakeGH(current_prs())
        snap = T.collect(gh)
        self.assertTrue(snap["facts_changed"])
        self.assertEqual(T.publish(gh, snap, ai_for(snap), "run", "now"), "published")
        self.assertIn(("create_issue",), gh.mutations)
        # The next run sees no fact change or label drift: no LLM call, no republish.
        again = T.collect(gh)
        self.assertFalse(again["facts_changed"])
        self.assertFalse(again["label_drift"])
        before = len(gh.mutations)
        self.assertEqual(T.publish(gh, again, None, "run", "now"), "already-published")
        self.assertEqual(len(gh.mutations), before)

    def test_label_drift_is_restored_without_llm_or_body_rewrite(self):
        gh = FakeGH(current_prs())
        snap = T.collect(gh)
        T.publish(gh, snap, ai_for(snap), "run", "now")
        gh.prs[2]["labels"] = []  # a person removed the managed label of #4
        drift = T.collect(gh)
        self.assertFalse(drift["facts_changed"])
        self.assertTrue(drift["label_drift"])
        gh.mutations.clear()
        self.assertEqual(T.publish(gh, drift, None, "run", "now"), "labels-only")
        self.assertEqual(gh.mutations, [("add", 4, ("pr-triage/needs-review",))])

    def test_stale_snapshot_is_refused_before_any_write(self):
        gh = FakeGH(current_prs())
        snap = T.collect(gh)
        gh.prs[2]["headRefOid"] = "ffffffffffff"  # the head of #4 changed just before publishing
        with self.assertRaises(T.Stale):
            T.publish(gh, snap, ai_for(snap), "run", "now")
        self.assertEqual(gh.mutations, [])

    def test_two_owned_reports_are_an_error_not_a_guess(self):
        gh = FakeGH(current_prs())
        snap = T.collect(gh)
        T.publish(gh, snap, ai_for(snap), "run", "now")
        gh.issues.append(bot_report(100, gh.issues[0]["body"]))
        with self.assertRaises(T.TriageError):
            T.collect(gh)

    def test_bot_issue_without_marker_or_title_is_not_reused(self):
        others = [bot_report(5, "다른 봇 이슈"), bot_report(6, T.REPORT_MARKER, title="다른 제목")]
        gh = FakeGH(current_prs(), issues=others)
        snap = T.collect(gh)
        self.assertIsNone(snap["report_issue"])
        T.publish(gh, snap, ai_for(snap), "run", "now")
        self.assertIn(("create_issue",), gh.mutations)
        self.assertFalse([m for m in gh.mutations if m[0] == "update_issue"])

    def test_human_issue_with_report_label_is_not_reused(self):
        human = {"number": 7, "author": {"login": "someone"}, "labels": [{"name": T.REPORT_LABEL}],
                 "body": "", "state": "OPEN"}
        gh = FakeGH(current_prs(), issues=[human])
        snap = T.collect(gh)
        T.publish(gh, snap, ai_for(snap), "run", "now")
        self.assertIn(("create_issue",), gh.mutations)
        self.assertNotIn(("update_issue", 7), gh.mutations)


class AIOutput(unittest.TestCase):
    def setUp(self):
        self.snap = T.collect(FakeGH(current_prs()))

    def test_valid(self):
        self.assertEqual(set(T.validate_ai(ai_for(self.snap), self.snap)), {2, 3, 4})

    def test_rejections(self):
        good = ai_for(self.snap)
        bad = {
            "digest 다름": dict(good, digest="0" * 64),
            "PR 빠짐": dict(good, items=good["items"][:2]),
            "PR 중복": dict(good, items=good["items"] + good["items"][:1]),
            "모르는 PR": dict(good, items=good["items"] + [{"number": 9, "priority": "높음", "next_action": "x"}]),
            "우선순위 허용 밖": dict(good, items=[dict(good["items"][0], priority="urgent")] + good["items"][1:]),
            "빈 다음 조치": dict(good, items=[dict(good["items"][0], next_action="  \n ")] + good["items"][1:]),
            "객체 아님": None,
        }
        for name, ai in bad.items():
            with self.assertRaises(ValueError, msg=name):
                T.validate_ai(ai, self.snap)

    def test_text_is_neutralized(self):
        ai = ai_for(self.snap)
        ai["items"][0]["next_action"] = "@owner 확인 | <!-- pr-triage-digest: " + "0" * 64 + " --> <b>x</b>"
        action = T.validate_ai(ai, self.snap)[2]["next_action"]
        self.assertNotIn("<!--", action)
        self.assertNotIn("@owner", action)
        self.assertNotIn("<b>", action)
        body = T.render(self.snap, T.validate_ai(ai, self.snap), "run", "now")
        self.assertEqual(T.parse_digest(body), self.snap["digest"])
        self.assertEqual(len(T.DIGEST_RE.findall(body)), 1)

    def test_facts_changed_requires_ai(self):
        gh = FakeGH(current_prs())
        snap = T.collect(gh)
        with self.assertRaises(ValueError):
            T.publish(gh, snap, None, "run", "now")
        self.assertEqual(gh.mutations, [])


if __name__ == "__main__":
    unittest.main()

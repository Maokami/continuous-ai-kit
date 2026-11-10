"""Tests for the configurable triage.

1. Default config: constants, digest, and report body equal values computed with the original SW
   scripts/pr_triage.py (sha256 33438382...).
2. Other configs: label namespace, report title, required checks, and marker id really change, and labels and
   reports of another namespace are neither touched nor reused.
3. Config validation, refusal of collect/publish config mismatch, CLI exit codes (config error 1, stale 3).
Run: python3 -I -m unittest discover -s tests -p 'test_triage*.py' -v
"""
import copy
import hashlib
import importlib.util
import io
import json
import pathlib
import tempfile
import unittest
from contextlib import redirect_stdout
from typing import Optional
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("triage", ROOT / "cai" / "triage.py")
assert spec is not None and spec.loader is not None
T = importlib.util.module_from_spec(spec)
spec.loader.exec_module(T)


def run(name: str, workflow: str, conclusion: Optional[str] = "SUCCESS", status: str = "COMPLETED"):
    return {"__typename": "CheckRun", "name": name, "workflowName": workflow, "status": status,
            "conclusion": conclusion}


GREEN = [run("Build blueprint", "Blueprint"), run("Lean build and audits", "CI"),
         run("Build blueprint and deploy", "Blueprint", "SKIPPED")]


def raw_pr(number, head, author, checks, review="REVIEW_REQUIRED", mergeable="MERGEABLE",
           state="BLOCKED", draft=False, labels=None, title=None):
    return {"number": number, "title": title or f"PR {number}", "url": f"https://example/{number}",
            "headRefOid": head, "isDraft": draft, "mergeable": mergeable, "mergeStateStatus": state,
            "reviewDecision": review, "author": {"login": author},
            "labels": list(labels or []), "statusCheckRollup": checks}


def golden_prs():
    return [raw_pr(2, "e4d5c8800000", "app/dependabot", GREEN),
            raw_pr(3, "5d27a2200000", "app/dependabot", [run("Lean build and audits", "CI", "FAILURE")]),
            raw_pr(4, "a17c05000000", "app/claude", GREEN, review="APPROVED", state="CLEAN")]


# Values computed with the original SW script (same fixture, now="2026-11-10 00:00 UTC", run_url="https://run").
GOLDEN_DIGEST = "a6505229ae6c14a269f1b7e639ae171086d1973b63676f9452c0adfd84776a95"
GOLDEN_BODY_SHA256 = "2f8873f78a5bdfcc188086622cb3b358c4262897a66533b97cb3feec5955cf7f"
GOLDEN_ITEMS = {2: {"priority": "높음", "next_action": "확인"}, 3: {"priority": "보통", "next_action": "CI 수정"},
                4: {"priority": "낮음", "next_action": "병합 검토"}}

CUSTOM = T.TriageConfig(label_namespace="kt-triage/", report_title="Kuratowski PR triage (auto)",
                        required_checks=("Lean CI", "AI review"), marker_id="kt-triage")


class FakeGH:
    """gh test double. When cfg is set, collect/publish check that they use the same config."""

    def __init__(self, prs, issues=None, existing_labels=(), cfg=None):
        self.repo = "o/r"
        self.prs = prs
        self.issues = issues or []
        self.existing = set(existing_labels)
        self.mutations = []
        if cfg is not None:
            self.cfg = cfg

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
        cfg = getattr(self, "cfg", T.DEFAULT)
        self.issues.append({"number": 99, "title": cfg.report_title, "author": {"login": "app/github-actions"},
                            "labels": [{"name": cfg.report_label}], "body": body, "state": "OPEN"})


def ai_for(snapshot):
    return {"digest": snapshot["digest"],
            "items": [{"number": p["number"], "priority": "보통", "next_action": "확인"} for p in snapshot["prs"]]}


class DefaultIsOriginal(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(T.PREFIX, "pr-triage/")
        self.assertEqual(T.REPORT_LABEL, "pr-triage/report")
        self.assertEqual(T.REPORT_TITLE, "PR 현황 보고서 (자동)")
        self.assertEqual(T.REPORT_MARKER, "<!-- pr-triage-report:v1 -->")
        self.assertEqual(T.DIGEST_RE.pattern, r"<!-- pr-triage-digest: ([0-9a-f]{64}) -->")
        self.assertEqual(T.REQUIRED_CHECKS, ("Lean build and audits",))
        self.assertEqual(set(T.LABELS), {"pr-triage/" + s for s in (
            "ready", "needs-review", "changes-requested", "ci-failing", "ci-pending", "conflict", "blocked",
            "draft", "unknown", "report")})
        self.assertEqual(T.LABELS["pr-triage/ready"][0], "0e8a16")
        self.assertEqual(T.PR_LABELS, set(T.LABELS) - {"pr-triage/report"})

    def test_digest_and_report_match_original(self):
        prs = [T.normalize_pr(r) for r in golden_prs()]
        self.assertEqual(T.facts_digest(prs), GOLDEN_DIGEST)
        body = T.render({"digest": GOLDEN_DIGEST, "prs": prs}, GOLDEN_ITEMS, "https://run", "2026-11-10 00:00 UTC")
        self.assertEqual(hashlib.sha256(body.encode()).hexdigest(), GOLDEN_BODY_SHA256)
        self.assertEqual([sorted(T.classify(p)) for p in prs],
                         [["pr-triage/needs-review"], ["pr-triage/ci-failing", "pr-triage/needs-review"],
                          ["pr-triage/ready"]])

    def test_snapshot_without_config_id_is_default(self):
        """A snapshot made by the original script (no config_id) can be published with the default config."""
        gh = FakeGH(golden_prs())
        snap = T.collect(gh)
        snap.pop("config_id")
        self.assertEqual(T.publish(gh, snap, ai_for(snap), "run", "now"), "published")


class CustomConfig(unittest.TestCase):
    def test_names_follow_config(self):
        self.assertEqual(CUSTOM.report_label, "kt-triage/report")
        self.assertEqual(CUSTOM.report_marker, "<!-- kt-triage-report:v1 -->")
        self.assertEqual(CUSTOM.digest_marker("a" * 64), "<!-- kt-triage-digest: " + "a" * 64 + " -->")
        pr = T.normalize_pr(raw_pr(5, "abc", "u", [run("Lean CI", "CI"), run("AI review", "PR review")],
                                   review="APPROVED", state="CLEAN"), CUSTOM)
        self.assertEqual(pr["ci"], "pass")
        self.assertEqual(T.classify(pr, CUSTOM), {"kt-triage/ready"})

    def test_required_checks_are_configurable(self):
        only_lean = [run("Lean CI", "CI")]
        pr = T.normalize_pr(raw_pr(5, "abc", "u", only_lean, review="APPROVED", state="CLEAN"), CUSTOM)
        self.assertEqual(pr["ci"], "unknown")  # the configured required check "AI review" is missing
        default_pr = T.normalize_pr(raw_pr(5, "abc", "u", only_lean, review="APPROVED", state="CLEAN"))
        self.assertEqual(default_pr["ci"], "unknown")  # the default required check name is missing too

    def test_other_namespace_labels_are_unmanaged(self):
        raw = raw_pr(2, "e4d", "u", [run("Lean CI", "CI"), run("AI review", "PR review")],
                     labels=[{"name": "pr-triage/ready"}, {"name": "kt-triage/ready"}])
        add, remove = T.plan_labels(T.normalize_pr(raw, CUSTOM), CUSTOM)
        self.assertEqual((add, remove), (["kt-triage/needs-review"], ["kt-triage/ready"]))
        gh = FakeGH([raw], cfg=CUSTOM)
        snap = T.collect(gh, CUSTOM)
        T.publish(gh, snap, ai_for(snap), "run", "now", CUSTOM)
        self.assertNotIn(("remove", 2, "pr-triage/ready"), gh.mutations)
        self.assertIn(("create_label", "kt-triage/report"), gh.mutations)
        self.assertEqual(gh.issues[-1]["title"], "Kuratowski PR triage (auto)")
        self.assertIn("<!-- kt-triage-report:v1 -->", gh.issues[-1]["body"])
        self.assertEqual(T.parse_digest(gh.issues[-1]["body"], CUSTOM), snap["digest"])
        self.assertIsNone(T.parse_digest(gh.issues[-1]["body"]))  # not readable with the default marker

    def test_lanes_do_not_reuse_each_others_report(self):
        gh = FakeGH(golden_prs())
        snap = T.collect(gh)
        T.publish(gh, snap, ai_for(snap), "run", "now")
        default_report = gh.issues[0]
        self.assertIsNone(T.find_report_issue([default_report], CUSTOM))
        self.assertEqual(T.find_report_issue([default_report])["number"], 99)

    def test_idempotent_and_stale_under_custom_config(self):
        prs = [raw_pr(5, "abc", "u", [run("Lean CI", "CI"), run("AI review", "PR review")])]
        gh = FakeGH(prs, cfg=CUSTOM)
        snap = T.collect(gh, CUSTOM)
        self.assertEqual(T.publish(gh, snap, ai_for(snap), "run", "now", CUSTOM), "published")
        again = T.collect(gh, CUSTOM)
        self.assertFalse(again["facts_changed"])
        self.assertFalse(again["label_drift"])
        before = len(gh.mutations)
        self.assertEqual(T.publish(gh, again, None, "run", "now", CUSTOM), "already-published")
        self.assertEqual(len(gh.mutations), before)
        gh.prs[0]["headRefOid"] = "fff"
        with self.assertRaises(T.Stale):
            T.publish(gh, again, ai_for(again), "run", "now", CUSTOM)
        self.assertEqual(len(gh.mutations), before)


class ConfigGuards(unittest.TestCase):
    def test_invalid_configs(self):
        bad = [
            dict(label_namespace="pr-triage"), dict(label_namespace="/x/"), dict(label_namespace="a b/"),
            dict(label_namespace="x" * 33 + "/"),
            dict(report_title=""), dict(report_title=" t"), dict(report_title="a\nb"), dict(report_title="t" * 257),
            dict(required_checks=()), dict(required_checks=("a", "a")), dict(required_checks=["a"]),
            dict(required_checks=("a\nb",)), dict(required_checks=(" a",)),
            dict(marker_id="Pr-triage"), dict(marker_id="pr--triage"), dict(marker_id="-x"), dict(marker_id="a b"),
        ]
        for kwargs in bad:
            with self.assertRaises(T.ConfigError, msg=kwargs):
                T.TriageConfig(**kwargs)

    def test_namespace_length_boundary(self):
        T.TriageConfig(label_namespace="x" * 32 + "/")  # 33 + "changes-requested" 17 = 50

    def test_from_mapping(self):
        cfg = T.TriageConfig.from_mapping({"version": 1, "label_namespace": "kt-triage/",
                                           "report_title": "Kuratowski PR triage (auto)",
                                           "required_checks": ["Lean CI", "AI review"], "marker_id": "kt-triage"})
        self.assertEqual(cfg, CUSTOM)
        self.assertEqual(T.TriageConfig.from_mapping({"version": 1}), T.DEFAULT)
        self.assertEqual(T.TriageConfig.from_mapping(T.DEFAULT.as_dict()), T.DEFAULT)
        for data in ({}, {"version": 2}, {"version": 1, "extra": 1}, {"version": 1, "required_checks": "Lean CI"},
                     [], None):
            with self.assertRaises(T.ConfigError, msg=data):
                T.TriageConfig.from_mapping(data)

    def test_config_id_mismatch_is_refused_before_writes(self):
        gh = FakeGH(golden_prs(), cfg=CUSTOM)
        snap = T.collect(gh, CUSTOM)
        plain = FakeGH(golden_prs())
        with self.assertRaises(ValueError):
            T.publish(plain, snap, ai_for(snap), "run", "now")
        self.assertEqual(plain.mutations, [])
        legacy = dict(snap)
        legacy.pop("config_id")
        with self.assertRaises(ValueError):
            T.publish(gh, legacy, ai_for(snap), "run", "now", CUSTOM)  # old snapshot: default config only
        self.assertEqual(gh.mutations, [])

    def test_wrapper_and_config_must_agree(self):
        with self.assertRaises(ValueError):
            T.collect(FakeGH(golden_prs(), cfg=CUSTOM))
        with self.assertRaises(ValueError):
            T.collect(T.GH("o/r", runner=lambda a, s: (0, "[]", ""), cfg=CUSTOM))


class CommandLine(unittest.TestCase):
    """CLI tests using FakeGH instead of GitHub. Exit codes: config error 1, stale 3, success 0."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = pathlib.Path(self.tmp.name)
        self.prs = golden_prs()
        self.made = []

        def factory(repo, dry_run=False, runner=None, cfg=T.DEFAULT):
            gh = FakeGH(self.prs, cfg=cfg)
            self.made.append(gh)
            return gh
        patcher = mock.patch.object(T, "GH", factory)
        patcher.start()
        self.addCleanup(patcher.stop)

    def main(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = T.main(list(argv))
        return code, out.getvalue()

    def test_collect_with_config_file_and_flags(self):
        conf = self.dir / "triage.json"
        conf.write_text(json.dumps(CUSTOM.as_dict()))
        snap_a, snap_b, gho = self.dir / "a.json", self.dir / "b.json", self.dir / "gho"
        code, _ = self.main("collect", "--repo", "o/r", "--out", str(snap_a), "--config", str(conf),
                            "--github-output", str(gho))
        self.assertEqual(code, 0)
        code, _ = self.main("collect", "--repo", "o/r", "--out", str(snap_b), "--label-namespace", "kt-triage/",
                            "--report-title", "Kuratowski PR triage (auto)", "--required-check", "Lean CI",
                            "--required-check", "AI review", "--marker-id", "kt-triage")
        self.assertEqual(code, 0)
        a, b = json.loads(snap_a.read_text()), json.loads(snap_b.read_text())
        self.assertEqual(a, b)
        self.assertEqual(a["config_id"], CUSTOM.config_id())
        keys = [line.split("=", 1)[0] for line in gho.read_text().splitlines()]
        self.assertEqual(keys, ["facts_changed", "label_drift", "pr_count", "digest"])

    def test_config_errors_exit_1(self):
        conf = self.dir / "bad.json"
        conf.write_text(json.dumps({"version": 1, "unknown": True}))
        out = str(self.dir / "s.json")
        self.assertEqual(self.main("collect", "--repo", "o/r", "--out", out, "--config", str(conf))[0], 1)
        self.assertEqual(self.main("collect", "--repo", "o/r", "--out", out, "--config", str(conf),
                                   "--marker-id", "x")[0], 1)
        self.assertEqual(self.main("collect", "--repo", "o/r", "--out", out, "--label-namespace", "bad")[0], 1)
        self.assertEqual(self.made, [])  # on a config error no GitHub wrapper is created

    def test_stale_exits_3_and_writes_nothing(self):
        snap = self.dir / "s.json"
        self.assertEqual(self.main("collect", "--repo", "o/r", "--out", str(snap))[0], 0)
        ai = self.dir / "ai.json"
        ai.write_text(json.dumps(ai_for(json.loads(snap.read_text()))))
        self.prs[0]["headRefOid"] = "changed"
        code, out = self.main("publish", "--repo", "o/r", "--snapshot", str(snap), "--ai", str(ai))
        self.assertEqual(code, 3)
        self.assertIn("::warning::", out)
        self.assertEqual(self.made[-1].mutations, [])

    def test_publish_with_other_config_exit_1(self):
        snap = self.dir / "s.json"
        self.main("collect", "--repo", "o/r", "--out", str(snap))
        ai = self.dir / "ai.json"
        ai.write_text(json.dumps(ai_for(json.loads(snap.read_text()))))
        code, out = self.main("publish", "--repo", "o/r", "--snapshot", str(snap), "--ai", str(ai),
                              "--label-namespace", "kt-triage/")
        self.assertEqual(code, 1)
        self.assertIn("설정", out)


if __name__ == "__main__":
    unittest.main()

"""Tests for review-thread facts in triage: GraphQL parsing, review_bots, labels, digest, report, gh arguments.

No network calls. Raw PRs carry reviewData as GH.list_prs attaches it; raw PRs without it keep the original
behavior (covered by test_triage_sw_compat.py and test_triage_config.py).
Run: python3 -I -m unittest discover -s tests -p 'test_triage*.py' -v
"""
import copy
import importlib.util
import io
import json
import pathlib
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("triage", ROOT / "cai" / "triage.py")
assert spec is not None and spec.loader is not None
T = importlib.util.module_from_spec(spec)
spec.loader.exec_module(T)

HEAD = "a" * 40
OLD = "b" * 40
CODEX = "chatgpt-codex-connector[bot]"
BOTS = T.TriageConfig(required_checks=("CI",), review_bots=(CODEX,))
PLAIN = T.TriageConfig(required_checks=("CI",))
GREEN = [{"__typename": "CheckRun", "name": "CI", "workflowName": "CI", "status": "COMPLETED",
          "conclusion": "SUCCESS"}]


def bot(login):
    return {"__typename": "Bot", "login": login}  # GraphQL bot logins have no "[bot]" suffix


def user(login):
    return {"__typename": "User", "login": login}


def thread(resolved, path="src/a.rs", author=None):
    return {"isResolved": resolved, "path": path,
            "comments": {"nodes": [{"author": author if author is not None else bot("chatgpt-codex-connector")}]}}


def review(commit, author=None):
    return {"author": author or bot("chatgpt-codex-connector"), "commit": {"oid": commit}}


def data(threads=(), reviews=(), head=HEAD, number=7, thread_total=None, review_total=None):
    threads, reviews = list(threads), list(reviews)
    return {"number": number, "headRefOid": head,
            "reviewThreads": {"totalCount": len(threads) if thread_total is None else thread_total, "nodes": threads},
            "reviews": {"totalCount": len(reviews) if review_total is None else review_total, "nodes": reviews}}


def raw_pr(review_data, number=7, head=HEAD, labels=()):
    """An otherwise ready PR, so only review facts decide ready."""
    return {"number": number, "title": f"PR {number}", "url": f"https://example/{number}", "headRefOid": head,
            "isDraft": False, "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "reviewDecision": "APPROVED",
            "author": {"login": "u"}, "labels": [{"name": n} for n in labels], "statusCheckRollup": GREEN,
            "reviewData": review_data}


def labels(raw, cfg=BOTS):
    return {l.replace(cfg.label_namespace, "") for l in T.classify(T.normalize_pr(raw, cfg), cfg)}


class ReviewFacts(unittest.TestCase):
    def test_no_unresolved_threads(self):
        facts = T.review_facts(data([thread(True)], [review(HEAD)]), HEAD, BOTS)
        self.assertEqual(facts, {"threads_complete": True, "unresolved": 0, "unresolved_bot": 0, "threads": [],
                                 "head_reviewed": {CODEX: True}})

    def test_unresolved_threads_by_bot_and_person(self):
        facts = T.review_facts(data([thread(False, "src/a.rs"), thread(True, "src/b.rs"),
                                     thread(False, "docs/c.md", user("alice"))]), HEAD, PLAIN)
        self.assertEqual(facts["unresolved"], 2)
        self.assertEqual(facts["unresolved_bot"], 1)
        self.assertEqual(facts["threads"], [{"path": "src/a.rs", "author": CODEX, "bot": True},
                                            {"path": "docs/c.md", "author": "alice", "bot": False}])
        self.assertTrue(facts["threads_complete"])

    def test_deleted_author_and_long_path(self):
        t = thread(False, "x" * 500)
        t["comments"]["nodes"][0]["author"] = None
        facts = T.review_facts(data([t]), HEAD, PLAIN)
        self.assertEqual(facts["threads"], [{"path": "x" * T.REVIEW_PATH_MAX, "author": "", "bot": False}])

    def test_truncated_or_missing_threads_are_incomplete(self):
        for d in (data([thread(True)], thread_total=101), None, {"number": 7}, data([thread(None)])):
            with self.subTest(d=str(d)[:40]):
                self.assertFalse(T.review_facts(d, HEAD, PLAIN)["threads_complete"])

    def test_head_review_states(self):
        cases = {
            "head reviewed": (data(reviews=[review(OLD), review(HEAD)]), True),
            "only an older commit": (data(reviews=[review(OLD)]), False),
            "no reviews": (data(), False),
            "a person on head": (data(reviews=[review(HEAD, user("chatgpt-codex-connector"))]), False),
            "older reviews not fetched": (data(reviews=[review(OLD)], review_total=150), None),
            "head moved since the PR list": (data(reviews=[review(HEAD)], head=OLD), None),
            "no PR object": (None, None),
        }
        for name, (d, expected) in cases.items():
            with self.subTest(name):
                self.assertEqual(T.review_facts(d, HEAD, BOTS)["head_reviewed"], {CODEX: expected})

    def test_unknown_head_and_unconfigured_bots(self):
        self.assertEqual(T.review_facts(data(reviews=[review(HEAD)]), None, BOTS)["head_reviewed"], {CODEX: None})
        self.assertEqual(T.review_facts(data(reviews=[review(OLD)]), HEAD, PLAIN)["head_reviewed"], {})

    def test_rest_style_login_in_graphql_is_not_doubled(self):
        facts = T.review_facts(data(reviews=[review(HEAD, bot(CODEX))]), HEAD, BOTS)
        self.assertEqual(facts["head_reviewed"], {CODEX: True})


class Labels(unittest.TestCase):
    def test_all_clear_is_ready(self):
        self.assertEqual(labels(raw_pr(data([thread(True)], [review(HEAD)]))), {"ready"})
        self.assertEqual(labels(raw_pr(data()), PLAIN), {"ready"})  # no bots configured: no head requirement

    def test_unresolved_thread_blocks_ready(self):
        self.assertEqual(labels(raw_pr(data([thread(False)], [review(HEAD)]))), {"unresolved-review"})

    def test_bot_not_on_head_blocks_ready(self):
        self.assertEqual(labels(raw_pr(data([], [review(OLD)]))), {"bot-review-pending"})
        self.assertEqual(labels(raw_pr(data([thread(False)], [review(OLD)]))),
                         {"unresolved-review", "bot-review-pending"})

    def test_unknown_review_facts(self):
        self.assertEqual(labels(raw_pr(data(reviews=[review(OLD)], review_total=150))), {"unknown"})
        self.assertEqual(labels(raw_pr(None)), {"unknown"})
        self.assertEqual(labels(raw_pr(data([thread(False)], [review(HEAD)], thread_total=120))),
                         {"unresolved-review", "unknown"})

    def test_review_labels_join_other_labels(self):
        raw = dict(raw_pr(data([thread(False)], [review(OLD)])), reviewDecision="REVIEW_REQUIRED", isDraft=True)
        self.assertEqual(labels(raw), {"draft", "needs-review", "unresolved-review", "bot-review-pending"})

    def test_labels_follow_namespace(self):
        cfg = T.TriageConfig(label_namespace="kt/", required_checks=("CI",), review_bots=(CODEX,))
        pr = T.normalize_pr(raw_pr(data([thread(False)], [review(OLD)])), cfg)
        self.assertEqual(T.classify(pr, cfg), {"kt/unresolved-review", "kt/bot-review-pending"})
        self.assertIn("kt/unresolved-review", cfg.pr_labels)


class Digest(unittest.TestCase):
    def test_review_facts_change_the_digest(self):
        base = T.normalize_pr(raw_pr(data([thread(False)], [review(HEAD)])), BOTS)
        resolved = T.normalize_pr(raw_pr(data([thread(True)], [review(HEAD)])), BOTS)
        legacy = T.normalize_pr({k: v for k, v in raw_pr(None).items() if k != "reviewData"}, BOTS)
        self.assertNotIn("review", legacy)
        digests = {T.facts_digest([p]) for p in (base, resolved, legacy)}
        self.assertEqual(len(digests), 3)


class FakeGH:
    def __init__(self, prs, cfg):
        self.repo = "o/r"
        self.cfg = cfg
        self.prs = prs
        self.issues = []
        self.existing = set()
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
        self.issues[0]["body"] = body

    def create_issue(self, body):
        self.mutations.append(("create_issue",))
        self.issues.append({"number": 99, "title": self.cfg.report_title, "author": {"login": "app/github-actions"},
                            "labels": [{"name": self.cfg.report_label}], "body": body, "state": "OPEN"})


def ai_for(snapshot):
    return {"digest": snapshot["digest"],
            "items": [{"number": p["number"], "priority": "보통", "next_action": "확인"} for p in snapshot["prs"]]}


class Publishing(unittest.TestCase):
    def test_labels_and_report_follow_review_facts(self):
        gh = FakeGH([raw_pr(data([thread(False, "src/a.rs")], [review(OLD)]))], BOTS)
        snap = T.collect(gh, BOTS)
        self.assertEqual(T.publish(gh, snap, ai_for(snap), "run", "now", BOTS), "published")
        self.assertIn(("add", 7, ("pr-triage/bot-review-pending", "pr-triage/unresolved-review")), gh.mutations)
        self.assertIn(("create_label", "pr-triage/unresolved-review"), gh.mutations)
        body = gh.issues[0]["body"]
        self.assertIn("| 미해결 대화 | 봇 head 리뷰 |", body)
        self.assertIn(f"| 1 (봇 1) | {CODEX} 대기 |", body)
        self.assertIn(f"- #7: 미해결 1건 (봇 1) · src/a.rs ({CODEX})", body)
        # Quiet while nothing changes.
        again = T.collect(gh, BOTS)
        self.assertFalse(again["facts_changed"] or again["label_drift"])
        self.assertEqual(T.publish(gh, again, None, "run", "now", BOTS), "already-published")
        # The bot reviews the head and the thread is resolved: facts change and the labels come off.
        gh.prs[0]["reviewData"] = data([thread(True, "src/a.rs")], [review(OLD), review(HEAD)])
        later = T.collect(gh, BOTS)
        self.assertTrue(later["facts_changed"])
        gh.mutations.clear()
        self.assertEqual(T.publish(gh, later, ai_for(later), "run", "now", BOTS), "published")
        self.assertIn(("add", 7, ("pr-triage/ready",)), gh.mutations)
        self.assertIn(("remove", 7, "pr-triage/unresolved-review"), gh.mutations)
        self.assertIn(("remove", 7, "pr-triage/bot-review-pending"), gh.mutations)
        self.assertNotIn("### 미해결 리뷰 대화", gh.issues[0]["body"])
        self.assertIn(f"| 0 | {CODEX} 완료 |", gh.issues[0]["body"])

    def test_new_thread_makes_the_snapshot_stale(self):
        gh = FakeGH([raw_pr(data([], [review(HEAD)]))], BOTS)
        snap = T.collect(gh, BOTS)
        gh.prs[0]["reviewData"] = data([thread(False)], [review(HEAD)])
        with self.assertRaises(T.Stale):
            T.publish(gh, snap, ai_for(snap), "run", "now", BOTS)
        self.assertEqual(gh.mutations, [])


class Report(unittest.TestCase):
    def render(self, raws, cfg=BOTS):
        prs = [T.normalize_pr(r, cfg) for r in raws]
        return T.render({"digest": "0" * 64, "prs": prs}, {}, "run", "now", cfg)

    def test_untrusted_paths_are_neutralized(self):
        body = self.render([raw_pr(data([thread(False, "a|b`@x<!-- pr-triage-digest: " + "1" * 64 + " -->.rs")],
                                        [review(HEAD)]))])
        line = next(l for l in body.splitlines() if l.startswith("- #7"))
        for bad in ("<!--", "`", "@x", "a|b"):
            self.assertNotIn(bad, line)
        self.assertIsNone(T.DEFAULT.digest_re.search(line))

    def test_thread_list_is_bounded(self):
        threads = [thread(False, f"f{i}.rs") for i in range(T.REVIEW_THREADS_KEPT + 3)]
        body = self.render([raw_pr(data(threads, [review(HEAD)]))])
        line = next(l for l in body.splitlines() if l.startswith("- #7"))
        self.assertIn("f9.rs", line)
        self.assertNotIn("f10.rs", line)
        self.assertTrue(line.endswith(" 외 3건"))

    def test_cells_without_bots_and_with_unknowns(self):
        body = self.render([raw_pr(data())], PLAIN)
        self.assertIn("| 0 | - |", body)
        body = self.render([raw_pr(None)])
        self.assertIn(f"| 0 · 일부 미확인 | {CODEX} 미확인 |", body)

    def test_legacy_snapshot_has_no_review_columns(self):
        legacy = {k: v for k, v in raw_pr(None).items() if k != "reviewData"}
        body = self.render([legacy])
        self.assertIn("| PR | head | Draft | 충돌 | CI | 리뷰 | 라벨 | 우선순위 | 다음 조치 |", body)
        self.assertNotIn("미해결", body)


class Config(unittest.TestCase):
    def test_review_bots_validation(self):
        for bots in ((CODEX, CODEX), ("bad login",), ("-x",), ("x[bot][bot]",), ("",), [CODEX],
                     tuple(f"b{i}[bot]" for i in range(T.REVIEW_BOTS_MAX + 1)), (1,)):
            with self.assertRaises(T.ConfigError, msg=bots):
                T.TriageConfig(review_bots=bots)
        T.TriageConfig(review_bots=("dependabot[bot]", "octo-cat"))

    def test_mapping_and_config_id(self):
        self.assertNotIn("review_bots", T.DEFAULT.as_dict())  # existing configs keep their config_id
        cfg = T.TriageConfig.from_mapping({"version": 1, "required_checks": ["CI"], "review_bots": [CODEX]})
        self.assertEqual(cfg, BOTS)
        self.assertEqual(T.TriageConfig.from_mapping(cfg.as_dict()), cfg)
        self.assertNotEqual(cfg.config_id(), PLAIN.config_id())
        with self.assertRaises(T.ConfigError):
            T.TriageConfig.from_mapping({"version": 1, "review_bots": CODEX})

    def test_cli_flag_equals_config_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            conf = pathlib.Path(tmp) / "c.json"
            conf.write_text(json.dumps(BOTS.as_dict()))
            made = []

            def factory(repo, dry_run=False, runner=None, cfg=T.DEFAULT):
                made.append(cfg)
                return FakeGH([raw_pr(data([thread(False)], [review(OLD)]))], cfg)
            with mock.patch.object(T, "GH", factory), redirect_stdout(io.StringIO()) as out:
                a, b = pathlib.Path(tmp) / "a.json", pathlib.Path(tmp) / "b.json"
                self.assertEqual(T.main(["collect", "--repo", "o/r", "--out", str(a), "--config", str(conf)]), 0)
                self.assertEqual(T.main(["collect", "--repo", "o/r", "--out", str(b), "--required-check", "CI",
                                         "--review-bot", CODEX]), 0)
            self.assertEqual(made, [BOTS, BOTS])
            self.assertEqual(json.loads(a.read_text()), json.loads(b.read_text()))
            self.assertIn("unresolved=1", out.getvalue())


class GraphQLArguments(unittest.TestCase):
    """GH.list_prs with a fake gh runner: one read-only GraphQL request per chunk of PR numbers."""

    def gh(self, numbers, reply=None):
        self.calls = []

        def runner(args, stdin):
            self.calls.append(args)
            if args[:2] == ["pr", "list"]:
                return 0, json.dumps([{"number": n} for n in numbers]), ""
            asked = [int(n) for n in re.findall(r"\bp(\d+): pullRequest\(number: \1\)", args[3])]
            body = reply if reply is not None else {"data": {"repository": {
                f"p{n}": data(number=n) for n in asked}}}
            return 0, json.dumps(body), ""
        return T.GH("own-er/re.po", runner=runner)

    def test_query_shape_and_chunks(self):
        numbers = list(range(1, T.REVIEW_QUERY_CHUNK + 6))
        prs = self.gh(numbers).list_prs()
        graphql = [c for c in self.calls if c[:2] == ["api", "graphql"]]
        self.assertEqual(len(graphql), 2)
        first = graphql[0]
        self.assertEqual(first[2], "-f")
        self.assertTrue(first[3].startswith("query=query($owner: String!, $name: String!) "))
        self.assertEqual(first[4:], ["-f", "owner=own-er", "-f", "name=re.po"])
        self.assertIn("p1: pullRequest(number: 1)", first[3])
        self.assertNotIn("mutation", " ".join(" ".join(c) for c in graphql))
        self.assertEqual([p["reviewData"]["number"] for p in prs], numbers)

    def test_no_query_for_empty_or_oversized_lists(self):
        self.assertEqual(self.gh([]).list_prs(), [])
        self.assertEqual(len(self.calls), 1)
        prs = self.gh(list(range(1, T.PR_LIMIT + 2))).list_prs()
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn("reviewData", prs[0])

    def test_missing_pr_object_becomes_unknown(self):
        prs = self.gh([3], reply={"data": {"repository": {"p3": None}}}).list_prs()
        self.assertIsNone(prs[0]["reviewData"])

    def test_errors_and_bad_inputs_fail_closed(self):
        for reply in ({"errors": [{"message": "x"}], "data": {"repository": {}}}, {"data": None}, {}):
            with self.subTest(reply=reply), self.assertRaises(T.TriageError):
                self.gh([3], reply=reply).list_prs()
        with self.assertRaises(T.TriageError):
            self.gh(["3"]).list_prs()
        with self.assertRaises(T.TriageError):
            T.GH("not a repo", runner=lambda a, s: (0, "[]", "")).review_data([1])


if __name__ == "__main__":
    unittest.main()

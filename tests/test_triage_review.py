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
        self.assertEqual(labels(raw_pr(data([], [review(OLD)]))), {"needs-bot-review"})
        self.assertEqual(labels(raw_pr(data([thread(False)], [review(OLD)]))),
                         {"unresolved-review", "needs-bot-review"})

    def test_unknown_review_facts(self):
        self.assertEqual(labels(raw_pr(data(reviews=[review(OLD)], review_total=150))), {"unknown"})
        self.assertEqual(labels(raw_pr(None)), {"unknown"})
        self.assertEqual(labels(raw_pr(data([thread(False)], [review(HEAD)], thread_total=120))),
                         {"unresolved-review", "unknown"})

    def test_review_labels_join_other_labels(self):
        raw = dict(raw_pr(data([thread(False)], [review(OLD)])), reviewDecision="REVIEW_REQUIRED", isDraft=True)
        self.assertEqual(labels(raw), {"draft", "needs-review", "unresolved-review", "needs-bot-review"})

    def test_labels_follow_namespace(self):
        cfg = T.TriageConfig(label_namespace="kt/", required_checks=("CI",), review_bots=(CODEX,))
        pr = T.normalize_pr(raw_pr(data([thread(False)], [review(OLD)])), cfg)
        self.assertEqual(T.classify(pr, cfg), {"kt/unresolved-review", "kt/needs-bot-review"})
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
        self.assertIn(("add", 7, ("pr-triage/needs-bot-review", "pr-triage/unresolved-review")), gh.mutations)
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
        self.assertIn(("remove", 7, "pr-triage/needs-bot-review"), gh.mutations)
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



class ReviewFixes(unittest.TestCase):
    """Bounds and edge cases found in review: body size, pagination, gh failures, login case."""

    def test_worst_case_report_fits_github_body_limit(self):
        logins = tuple(f"{'b' * 34}{i}[bot]" for i in range(T.REVIEW_BOTS_MAX))
        cfg = T.TriageConfig(required_checks=("CI",), review_bots=logins)
        threads = [thread(False, ("|" * 300) + str(i), bot("x" * 39)) for i in range(T.REVIEW_THREADS_KEPT + 5)]
        prs = []
        for n in range(1, T.PR_LIMIT + 1):
            raw = raw_pr(data(threads, [review(OLD)], number=n), number=n)
            raw["title"] = "|" * 500
            prs.append(T.normalize_pr(raw, cfg))
        items = {p["number"]: {"priority": "보통", "next_action": "|" * 160} for p in prs}
        body = T.render({"digest": "0" * 64, "prs": prs}, items, "https://run/" + "9" * 30, "now", cfg)
        self.assertLessEqual(len(body), T.GITHUB_BODY_MAX)
        self.assertIn("길이 제한으로 생략했다", body)

    def test_oversized_body_is_refused_before_writes(self):
        gh = FakeGH([raw_pr(data([thread(False)], [review(OLD)]))], BOTS)
        snap = T.collect(gh, BOTS)
        with mock.patch.object(T, "GITHUB_BODY_MAX", 10), self.assertRaises(T.TriageError):
            T.publish(gh, snap, ai_for(snap), "run", "now", BOTS)
        self.assertEqual(gh.mutations, [])

    def test_login_case_does_not_matter(self):
        cfg = T.TriageConfig(required_checks=("CI",), review_bots=("ChatGPT-Codex-Connector[bot]",))
        facts = T.review_facts(data(reviews=[review(HEAD)]), HEAD, cfg)
        self.assertEqual(facts["head_reviewed"], {"ChatGPT-Codex-Connector[bot]": True})

    def test_partial_thread_list_is_marked(self):
        body = Report().render([raw_pr(data([thread(False)], [review(HEAD)], thread_total=900))])
        line = next(l for l in body.splitlines() if l.startswith("- #7"))
        self.assertIn("(일부 미확인)", line)

    def paged_gh(self, pages, last_has_next=False):
        """PR 7 whose threads come in `pages` pages of two resolved threads each."""
        calls = []

        def page(i):
            more = i < pages - 1 or last_has_next
            return {"totalCount": 2 * pages + (1 if last_has_next else 0),
                    "pageInfo": {"hasNextPage": more, "endCursor": f"c{i}"},
                    "nodes": [thread(True, f"p{i}a"), thread(True, f"p{i}b")]}

        def runner(args, stdin):
            calls.append(args)
            if args[:2] == ["pr", "list"]:
                return 0, json.dumps([{"number": 7, "headRefOid": HEAD}]), ""
            if "-F" in args:  # a later page; the cursor names the previous page
                previous = int(args[args.index("-f", 8) + 1].split("=c", 1)[1])
                return 0, json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": page(previous + 1)}}}}), ""
            node = dict(data(reviews=[review(HEAD)]), reviewThreads=page(0))
            return 0, json.dumps({"data": {"repository": {"p7": node}}}), ""
        return T.GH("o/r", runner=runner), calls

    def test_threads_beyond_one_page_are_followed(self):
        gh, calls = self.paged_gh(3)
        raw = gh.list_prs()[0]
        self.assertEqual(len(raw["reviewData"]["reviewThreads"]["nodes"]), 6)
        facts = T.review_facts(raw["reviewData"], HEAD, BOTS)
        self.assertTrue(facts["threads_complete"])
        later = [c for c in calls if "-F" in c]
        self.assertEqual(len(later), 2)
        self.assertEqual(later[0][-4:], ["-F", "number=7", "-f", "cursor=c0"])
        self.assertIn("after: $cursor", later[0][3])
        self.assertNotIn("mutation", T.THREAD_PAGE_QUERY)

    def test_page_limit_leaves_threads_incomplete(self):
        gh, calls = self.paged_gh(T.REVIEW_THREAD_PAGES, last_has_next=True)
        raw = gh.list_prs()[0]
        self.assertEqual(len([c for c in calls if "-F" in c]), T.REVIEW_THREAD_PAGES - 1)
        self.assertFalse(T.review_facts(raw["reviewData"], HEAD, BOTS)["threads_complete"])

    def test_gh_failure_becomes_triage_error(self):
        def runner(args, stdin):
            if args[:2] == ["pr", "list"]:
                return 0, json.dumps([{"number": 3}]), ""
            return 1, "", "GraphQL: API rate limit exceeded"
        with self.assertRaises(T.TriageError):
            T.GH("o/r", runner=runner).list_prs()

    def test_ready_description_is_unchanged(self):
        self.assertEqual(T.LABEL_SPECS["ready"][1], "Draft 아님, 충돌 없음, CI 통과, 리뷰 승인, 병합 가능 모두 확인됨")



MARKER = "codex-review-state:v1"
MARKED = T.TriageConfig(required_checks=("CI",), review_bots=(CODEX,), review_state_markers=(MARKER,))


def summary(commit, status="completed", findings=False, marker=MARKER):
    """The bot's summary comment as observed on a consumer PR (2026-11-18), with its review-state marker."""
    state = {"code_review": {"commit_sha": commit, "has_any_findings": findings,
                             "has_unresolved_findings": None if not findings else True, "status": status},
             "security_review": {"commit_sha": None, "has_any_findings": None, "has_unresolved_findings": None,
                                 "status": None}}
    return ("<!-- codex-pull-request-review-summary -->\n"
            f"<!-- {marker} {json.dumps(state, separators=(',', ':'))} -->\n\n"
            "<details>\n<summary><strong>Codex Review Summary</strong></summary>\n</details>\n")


def comment(body, author=None):
    return {"author": author if author is not None else bot("chatgpt-codex-connector"), "body": body}


def with_comments(d, comments, total=None):
    d["comments"] = {"totalCount": len(comments) if total is None else total, "nodes": comments}
    return d


class ReviewStateMarkers(unittest.TestCase):
    """Opt-in markers count only in a configured bot's own comment, and only for a completed head review."""

    def state(self, comments, cfg=MARKED, reviews=()):
        return T.review_facts(with_comments(data(reviews=list(reviews)), comments), HEAD, cfg)["head_reviewed"]

    def test_marker_on_head_counts_as_review(self):
        self.assertEqual(self.state([comment(summary(HEAD))]), {CODEX: True})
        self.assertEqual(labels(raw_pr(with_comments(data(), [comment(summary(HEAD))])), MARKED), {"ready"})

    def test_marker_on_older_commit_or_unfinished_is_pending(self):
        for body in (summary(OLD), summary(HEAD, status="in_progress"), summary(HEAD, status=None)):
            with self.subTest(body=body[60:140]):
                self.assertEqual(self.state([comment(body)]), {CODEX: False})
        self.assertEqual(labels(raw_pr(with_comments(data(), [comment(summary(OLD))])), MARKED),
                         {"needs-bot-review"})

    def test_other_authors_are_ignored(self):
        for author in (user("Maokami"), user("chatgpt-codex-connector"), bot("another-reviewer"), None):
            with self.subTest(author=author):
                c = comment(summary(HEAD), author if author is not None else {})
                if author is None:
                    c["author"] = None  # deleted account
                self.assertEqual(self.state([c]), {CODEX: False})

    def test_malformed_markers_fail_closed(self):
        good = summary(HEAD)
        bodies = [
            good.replace('"code_review":{', '"code_review":{,'),            # broken JSON
            good.replace(MARKER, "codex-review-state:v2"),                 # other marker version
            "x" * T.MARKER_SCAN_CHARS + good,                              # beyond the scanned start
            good.replace('"status":"completed"', '"status":"completed","pad":"' + "p" * T.MARKER_JSON_MAX + '"'),
            good.replace('"code_review"', '"other_review"'),               # wrong section
            good.replace(HEAD, HEAD.upper()),                              # not the exact head SHA
            None,
        ]
        for body in bodies:
            with self.subTest(body=str(body)[:40]):
                self.assertEqual(self.state([comment(body)]), {CODEX: False})

    def test_markers_ignored_unless_configured(self):
        self.assertEqual(self.state([comment(summary(HEAD))], cfg=BOTS), {CODEX: False})

    def test_review_still_counts_with_markers(self):
        self.assertEqual(self.state([], reviews=[review(HEAD)]), {CODEX: True})

    def test_marker_needs_same_head(self):
        d = with_comments(data(head=OLD), [comment(summary(HEAD))])
        self.assertEqual(T.review_facts(d, HEAD, MARKED)["head_reviewed"], {CODEX: None})

    def test_comment_bodies_do_not_reach_the_snapshot(self):
        gh = FakeGH([raw_pr(with_comments(data(), [comment(summary(HEAD) + "SECRET-LOOKING TEXT")]))], MARKED)
        snap = T.collect(gh, MARKED)
        self.assertNotIn("SECRET-LOOKING", json.dumps(snap))
        self.assertNotIn("codex-review-state", json.dumps(snap))

    def test_config(self):
        self.assertEqual(T.TriageConfig.from_mapping(MARKED.as_dict()), MARKED)
        self.assertNotIn("review_state_markers", BOTS.as_dict())
        self.assertNotEqual(MARKED.config_id(), BOTS.config_id())
        for kwargs in (dict(review_bots=(CODEX,), review_state_markers=("unknown:v1",)),
                       dict(review_bots=(CODEX,), review_state_markers=(MARKER, MARKER)),
                       dict(review_bots=(CODEX,), review_state_markers=[MARKER]),
                       dict(review_state_markers=(MARKER,))):          # markers without bots
            with self.assertRaises(T.ConfigError, msg=kwargs):
                T.TriageConfig(**kwargs)
        with self.assertRaises(T.ConfigError):
            T.TriageConfig.from_mapping({"version": 1, "review_bots": [CODEX], "review_state_markers": MARKER})

    def test_comment_query_only_when_configured(self):
        seen = []

        def runner(args, stdin):
            if args[:2] == ["pr", "list"]:
                return 0, json.dumps([{"number": 7}]), ""
            seen.append(args[3])
            return 0, json.dumps({"data": {"repository": {"p7": data()}}}), ""
        T.GH("o/r", runner=runner, cfg=BOTS).list_prs()
        T.GH("o/r", runner=runner, cfg=MARKED).list_prs()
        self.assertNotIn("comments(last:", seen[0])
        self.assertIn(T.COMMENT_FIELDS, seen[1])
        self.assertNotIn("mutation", T.COMMENT_FIELDS)

    def test_cli_flag(self):
        args = T.argparse.Namespace(config=None, label_namespace=None, report_title=None, required_checks=["CI"],
                                    marker_id=None, review_bots=[CODEX], review_state_markers=[MARKER])
        self.assertEqual(T.config_from_args(args), MARKED)


if __name__ == "__main__":
    unittest.main()

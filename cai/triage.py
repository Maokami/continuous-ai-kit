#!/usr/bin/env python3
"""PR status report and triage labels, moved from scripts/pr_triage.py in smarandache-wellin-lean
(sha256 33438382...).

Labels and states are derived deterministically from GitHub facts. Claude only proposes a priority and a
next action from the same facts; this script validates the proposal before adding it to the report. It
never merges or closes PRs and never modifies branches.

Repository-specific values come from TriageConfig: required check names, report title, managed label
namespace, and marker id. The default config DEFAULT equals the original SW settings, so with defaults the
snapshot digest, report markers, labels, idempotency, and stale handling match the original script.
collect and publish must use the same config; the snapshot's config_id checks this.

Subcommands:
  collect   Collect facts about open PRs into a snapshot and report whether they differ from the digest
            recorded in the report issue.
  validate  Validate Claude's structured output against the snapshot.
  publish   Collect again, refuse a stale snapshot, then update the managed labels and the single report
            issue. With --dry-run nothing is changed; the plan is printed.

Review facts: the GitHub wrapper also reads each open PR's review threads and latest reviews over read-only
GraphQL. Unresolved threads and configured review bots that have not reviewed the current head commit become
facts and labels. A bot also counts as having reviewed the head when an opted-in review-state marker in its own
PR comment names the head commit (bots that leave no review when they find nothing). Raw PRs without review data (inputs of the original script) keep the original behavior.

Config arguments (collect and publish; use one form only):
  --config FILE                  {"version": 1, "label_namespace", "report_title", "required_checks", "marker_id",
                                  "review_bots", "review_state_markers" (optional)}
  --label-namespace / --report-title / --required-check (repeatable) / --marker-id / --review-bot (repeatable)
  / --review-state-marker (repeatable)

Examples (read-only):
  python3 -I cai/triage.py collect --repo OWNER/REPO --out /tmp/snap.json
  python3 -I cai/triage.py publish --repo OWNER/REPO --snapshot /tmp/snap.json --ai /tmp/ai.json --dry-run
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Set, Tuple

DIGEST_VERSION = 1
CONFIG_VERSION = 1
PR_LIMIT = 100      # open-PR cap per run; query cap + 1 and stop if exceeded
REPORT_LIMIT = 20
BOT_LOGINS = {"app/github-actions", "github-actions[bot]", "github-actions"}
GITHUB_LABEL_MAX = 50
GITHUB_TITLE_MAX = 256
# Review facts. GraphQL pages hold at most 100 nodes; a longer connection makes the facts incomplete (unknown).
REVIEW_THREAD_LIMIT = 100   # review threads per GraphQL page
REVIEW_THREAD_PAGES = 5     # pages read per PR (500 threads, resolved ones included); more gives unknown
REVIEW_LIMIT = 100          # latest reviews per PR searched for the head commit
REVIEW_QUERY_CHUNK = 20     # PRs per GraphQL request
REVIEW_BOTS_MAX = 10
# Review-state markers a review bot writes into its own PR comment, as "<!-- NAME {json} -->". Only markers listed
# here can be configured; the value names the JSON section holding "commit_sha" and "status".
REVIEW_STATE_MARKERS: Dict[str, str] = {"codex-review-state:v1": "code_review"}
MARKER_COMMENT_LIMIT = 50   # latest PR comments searched for markers (only when markers are configured)
MARKER_SCAN_CHARS = 4096    # only the start of a comment is searched
MARKER_JSON_MAX = 2000      # longer marker JSON is ignored
REVIEW_PATH_MAX = 200       # characters of a thread path kept in the snapshot
REVIEW_THREADS_KEPT = 10    # unresolved threads kept per PR (snapshot size bound); the count stays exact
REPORT_THREAD_CHARS = 15000 # thread list budget; with the bounded table the body stays under GITHUB_BODY_MAX
REPORT_BOT_CELL = 120       # characters of the bot head-review cell
GITHUB_BODY_MAX = 65536
LOGIN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}(?:\[bot\])?")
REPO_RE = re.compile(r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")
# Read-only fields per PR. Bot logins come without the REST "[bot]" suffix; _actor adds it.
THREAD_NODE = "isResolved path comments(first: 1) { nodes { author { __typename login } } }"
REVIEW_FIELDS = (
    "number headRefOid "
    f"reviewThreads(first: {REVIEW_THREAD_LIMIT}) {{ totalCount pageInfo {{ hasNextPage endCursor }} "
    f"nodes {{ {THREAD_NODE} }} }} "
    f"reviews(last: {REVIEW_LIMIT}) {{ totalCount nodes {{ author {{ __typename login }} commit {{ oid }} }} }}")
# Added to REVIEW_FIELDS only when review_state_markers are configured. Bodies stay in the collector: only the
# parsed marker result reaches the snapshot.
COMMENT_FIELDS = (f"comments(last: {MARKER_COMMENT_LIMIT}) {{ totalCount nodes {{ author {{ __typename login }} "
                  "body } }")
# Later thread pages of one PR; the cursor is a GraphQL variable.
THREAD_PAGE_QUERY = (
    "query($owner: String!, $name: String!, $number: Int!, $cursor: String!) { "
    "repository(owner: $owner, name: $name) { pullRequest(number: $number) { "
    f"reviewThreads(first: {REVIEW_THREAD_LIMIT}, after: $cursor) {{ pageInfo {{ hasNextPage endCursor }} "
    f"nodes {{ {THREAD_NODE} }} }} }} }} }}")

# Managed label suffixes with (color, description); full names are namespace + suffix.
LABEL_SPECS: Dict[str, Tuple[str, str]] = {
    "ready": ("0e8a16", "Draft 아님, 충돌 없음, CI 통과, 리뷰 승인, 병합 가능 모두 확인됨"),
    "needs-review": ("fbca04", "리뷰 승인 대기"),
    "changes-requested": ("d93f0b", "리뷰가 변경을 요청함"),
    "unresolved-review": ("d93f0b", "해결되지 않은 리뷰 대화가 있음"),
    "needs-bot-review": ("fbca04", "설정된 리뷰 봇이 현재 head commit 을 아직 리뷰하지 않음"),
    "ci-failing": ("b60205", "실패한 검사가 있음"),
    "ci-pending": ("c5def5", "진행 중인 검사가 있음"),
    "conflict": ("b60205", "base 와 충돌"),
    "blocked": ("e99695", "나머지 확인은 통과했지만 병합 상태가 BLOCKED·BEHIND 등으로 미해결"),
    "draft": ("cfd3d7", "Draft PR"),
    "unknown": ("ededed", "상태 일부를 확인하지 못함 (누락·UNKNOWN)"),
    "report": ("5319e7", "PR 현황 보고서 이슈 (자동 갱신)"),
}
NAMESPACE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/")
MARKER_ID_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")  # keeps "--" out of HTML comments


class ConfigError(ValueError):
    """The configuration is malformed."""


@dataclass(frozen=True)
class TriageConfig:
    """Repository-specific values. The defaults are the existing smarandache-wellin-lean settings."""
    label_namespace: str = "pr-triage/"
    report_title: str = "PR 현황 보고서 (자동)"
    required_checks: Tuple[str, ...] = ("Lean build and audits",)  # required check names of the ruleset
    marker_id: str = "pr-triage"
    review_bots: Tuple[str, ...] = ()  # REST logins such as "name[bot]" that must review the head commit
    review_state_markers: Tuple[str, ...] = ()  # opt-in names from REVIEW_STATE_MARKERS
    _labels: Dict[str, Tuple[str, str]] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.label_namespace, str) or not NAMESPACE_RE.fullmatch(self.label_namespace):
            raise ConfigError("label_namespace 는 영숫자로 시작하고 '/' 로 끝나야 한다 (예: pr-triage/)")
        longest = max(len(self.label_namespace + suffix) for suffix in LABEL_SPECS)
        if longest > GITHUB_LABEL_MAX:
            raise ConfigError(f"라벨 이름이 GitHub 상한 {GITHUB_LABEL_MAX} 자를 넘는다 ({longest} 자)")
        title = self.report_title
        if (not isinstance(title, str) or not title.strip() or title != title.strip()
                or "\n" in title or "\r" in title or len(title) > GITHUB_TITLE_MAX):
            raise ConfigError("report_title 은 앞뒤 공백 없는 한 줄, 1~256 자여야 한다")
        checks = self.required_checks
        if (not isinstance(checks, tuple) or not checks or len(set(checks)) != len(checks)
                or any(not isinstance(c, str) or not c.strip() or c != c.strip() or "\n" in c for c in checks)):
            raise ConfigError("required_checks 는 서로 다른 한 줄 이름의 비지 않은 목록이어야 한다")
        if not isinstance(self.marker_id, str) or not MARKER_ID_RE.fullmatch(self.marker_id):
            raise ConfigError("marker_id 는 소문자·숫자와 단일 '-' 만 쓴다 (예: pr-triage)")
        bots = self.review_bots
        if (not isinstance(bots, tuple) or len(bots) > REVIEW_BOTS_MAX or len(set(bots)) != len(bots)
                or any(not isinstance(b, str) or not LOGIN_RE.fullmatch(b) for b in bots)):
            raise ConfigError(f"review_bots 는 서로 다른 GitHub login (예: name[bot]) {REVIEW_BOTS_MAX} 개 이하의 목록이어야 한다")
        markers = self.review_state_markers
        if (not isinstance(markers, tuple) or len(set(markers)) != len(markers)
                or any(m not in REVIEW_STATE_MARKERS for m in markers)):
            raise ConfigError(f"review_state_markers 는 {sorted(REVIEW_STATE_MARKERS)} 중에서 고른 서로 다른 이름이어야 한다")
        if markers and not bots:
            raise ConfigError("review_state_markers 는 review_bots 와 함께 쓴다 (설정된 봇의 코멘트만 읽는다)")
        object.__setattr__(self, "_labels", {self.label_namespace + suffix: spec
                                             for suffix, spec in LABEL_SPECS.items()})

    # ---- Derived names (equal to the original constants under the defaults)
    @property
    def report_label(self) -> str:
        return self.label_namespace + "report"

    @property
    def labels(self) -> Dict[str, Tuple[str, str]]:
        return dict(self._labels)

    @property
    def pr_labels(self) -> Set[str]:
        return {name for name in self._labels if name != self.report_label}

    def label(self, suffix: str) -> str:
        if suffix not in LABEL_SPECS:
            raise KeyError(suffix)
        return self.label_namespace + suffix

    @property
    def report_marker(self) -> str:
        return f"<!-- {self.marker_id}-report:v1 -->"

    @property
    def digest_re(self) -> "re.Pattern[str]":
        # MARKER_ID_RE allows only [a-z0-9-], so marker_id contains no regex metacharacters.
        return re.compile(r"<!-- " + self.marker_id + r"-digest: ([0-9a-f]{64}) -->")

    def digest_marker(self, digest: str) -> str:
        return f"<!-- {self.marker_id}-digest: {digest} -->"

    # ---- Serialization
    def as_dict(self) -> dict:
        out = {"version": CONFIG_VERSION, "label_namespace": self.label_namespace,
               "report_title": self.report_title, "required_checks": list(self.required_checks),
               "marker_id": self.marker_id}
        if self.review_bots:  # omitted when empty so existing configs keep their config_id
            out["review_bots"] = list(self.review_bots)
        if self.review_state_markers:
            out["review_state_markers"] = list(self.review_state_markers)
        return out

    def config_id(self) -> str:
        blob = json.dumps(self.as_dict(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    @classmethod
    def from_mapping(cls, data: object) -> "TriageConfig":
        """Load a versioned config object. Unknown keys and other versions are rejected; missing keys default."""
        if not isinstance(data, dict):
            raise ConfigError("설정은 JSON 객체여야 한다")
        allowed = {"version", "label_namespace", "report_title", "required_checks", "marker_id", "review_bots",
                   "review_state_markers"}
        unknown = set(data) - allowed
        if unknown:
            raise ConfigError(f"모르는 설정 키: {sorted(unknown)}")
        if data.get("version") != CONFIG_VERSION:
            raise ConfigError(f"설정 version 은 {CONFIG_VERSION} 이어야 한다")
        kwargs = {k: v for k, v in data.items() if k != "version"}
        for key in ("required_checks", "review_bots", "review_state_markers"):
            if key in kwargs:
                if not isinstance(kwargs[key], list):
                    raise ConfigError(f"{key} 는 배열이어야 한다")
                kwargs[key] = tuple(kwargs[key])
        return cls(**kwargs)


DEFAULT = TriageConfig()

# Original module constants (values of the default config), kept for existing callers and tests.
PREFIX = DEFAULT.label_namespace
REPORT_LABEL = DEFAULT.report_label
REPORT_TITLE = DEFAULT.report_title
REPORT_MARKER = DEFAULT.report_marker  # fixed marker of the bot-owned report
REQUIRED_CHECKS = DEFAULT.required_checks
LABELS: Dict[str, Tuple[str, str]] = DEFAULT.labels
PR_LABELS = DEFAULT.pr_labels
PRIORITIES = ("높음", "보통", "낮음")
DIGEST_RE = DEFAULT.digest_re

PASS_CONCLUSIONS = {"SUCCESS", "SKIPPED", "NEUTRAL"}
FAIL_CONCLUSIONS = {"FAILURE", "TIMED_OUT", "STARTUP_FAILURE", "ACTION_REQUIRED"}
PENDING_STATUSES = {"QUEUED", "IN_PROGRESS", "PENDING", "WAITING", "REQUESTED"}


class Stale(Exception):
    """Facts re-collected just before publishing differ from the snapshot."""


class TriageError(Exception):
    """The collection cannot be trusted to be complete (cap exceeded, several report issues, ...)."""


# ---------------------------------------------------------------- Normalization and classification

def check_state(check: dict) -> str:
    """Map one check to pass / fail / pending / unknown. CANCELLED, STALE, and missing values are unknown."""
    if check.get("__typename") == "StatusContext":
        state = (check.get("state") or "").upper()
        return {"SUCCESS": "pass", "FAILURE": "fail", "ERROR": "fail",
                "PENDING": "pending", "EXPECTED": "pending"}.get(state, "unknown")
    status = (check.get("status") or "").upper()
    if status != "COMPLETED":
        return "pending" if status in PENDING_STATUSES else "unknown"
    conclusion = (check.get("conclusion") or "").upper()
    if conclusion in PASS_CONCLUSIONS:
        return "pass"
    if conclusion in FAIL_CONCLUSIONS:
        return "fail"
    return "unknown"


def ci_state(checks: List[dict], cfg: TriageConfig = DEFAULT) -> str:
    """Any failure gives fail; anything in progress gives pending. Required checks must actually be SUCCESS
    (SKIPPED, NEUTRAL, or absence gives unknown). Other checks also pass when SKIPPED or NEUTRAL (for example a
    deployment job skipped on PRs)."""
    states = [c["state"] for c in checks]
    if "fail" in states:
        return "fail"
    if "pending" in states:
        return "pending"
    if "unknown" in states:
        return "unknown"
    for name in cfg.required_checks:
        required = [c for c in checks if c["name"] == name]
        if not required or any(c["conclusion"] != "SUCCESS" for c in required):
            return "unknown"
    return "pass"


def _opt_str(value) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _actor(author: object) -> Tuple[str, bool]:
    """(REST-style login, is a GitHub App bot). GraphQL omits the "[bot]" suffix that REST logins carry."""
    if not isinstance(author, dict):
        return "", False  # deleted account
    login = author.get("login") if isinstance(author.get("login"), str) else ""
    bot = author.get("__typename") == "Bot"
    if bot and login and not login.endswith("[bot]"):
        login += "[bot]"
    return login, bot


def _nodes(data: object, key: str) -> Tuple[List[dict], bool]:
    """(nodes, complete) of a GraphQL connection. Missing, malformed, or truncated connections are incomplete."""
    conn = data.get(key) if isinstance(data, dict) else None
    nodes = conn.get("nodes") if isinstance(conn, dict) else None
    total = conn.get("totalCount") if isinstance(conn, dict) else None
    if not isinstance(nodes, list) or type(total) is not int:
        return [], False
    kept = [n for n in nodes if isinstance(n, dict)]
    return kept, len(kept) == len(nodes) and total <= len(kept)


def marker_names_head(body: object, head_sha: str, markers: Tuple[str, ...]) -> bool:
    """Whether a configured review-state marker near the start of body records a completed review of head_sha.
    Anything else (no marker, oversized or malformed JSON, another commit, unfinished review) is False."""
    if not isinstance(body, str):
        return False
    start = body[:MARKER_SCAN_CHARS]
    for marker in markers:
        pattern = r"<!-- " + re.escape(marker) + r" (\{[^\n]{0,%d}?\}) -->" % MARKER_JSON_MAX
        for match in re.finditer(pattern, start):
            try:
                state = json.loads(match.group(1))
            except ValueError:
                continue
            section = state.get(REVIEW_STATE_MARKERS[marker]) if isinstance(state, dict) else None
            if (isinstance(section, dict) and section.get("status") == "completed"
                    and section.get("commit_sha") == head_sha):
                return True
    return False


def review_facts(data: object, head_sha: Optional[str], cfg: TriageConfig = DEFAULT) -> dict:
    """Unresolved review threads and, per configured review bot, whether it reviewed the head commit.

    head_reviewed values: True (a review by the bot has commit == head, or a configured review-state marker in
    the bot's own PR comment names head), False (complete reviews and the fetched comments show none),
    None (cannot tell: head unknown or moved since the PR list, or older reviews were not fetched).
    Thread and comment bodies never reach the result; only thread paths and first authors are kept."""
    threads, threads_complete = _nodes(data, "reviewThreads")
    unresolved = []
    for t in threads:
        if t.get("isResolved") is True:
            continue
        if t.get("isResolved") is not False:
            threads_complete = False  # counted as unresolved: surface rather than hide
        comments = t["comments"].get("nodes") if isinstance(t.get("comments"), dict) else None
        first = comments[0] if isinstance(comments, list) and comments and isinstance(comments[0], dict) else {}
        login, bot = _actor(first.get("author"))
        path = t.get("path") if isinstance(t.get("path"), str) else ""
        unresolved.append({"path": path[:REVIEW_PATH_MAX], "author": login, "bot": bot})
    reviews, reviews_complete = _nodes(data, "reviews")
    same_head = head_sha is not None and isinstance(data, dict) and data.get("headRefOid") == head_sha
    # GitHub logins are case-insensitive.
    on_head = {_actor(r.get("author"))[0].casefold() for r in reviews
               if isinstance(r.get("commit"), dict) and r["commit"].get("oid") == head_sha}
    # Markers count only in comments written by a configured bot; anyone else's copy is ignored.
    bots = {b.casefold() for b in cfg.review_bots}
    if cfg.review_state_markers and same_head:
        for c in _nodes(data, "comments")[0]:
            login = _actor(c.get("author"))[0].casefold()
            if login in bots and marker_names_head(c.get("body"), head_sha, cfg.review_state_markers):
                on_head.add(login)
    head_reviewed: Dict[str, Optional[bool]] = {}
    for bot in cfg.review_bots:
        if not same_head:
            head_reviewed[bot] = None
        elif bot.casefold() in on_head:
            head_reviewed[bot] = True
        else:
            head_reviewed[bot] = False if reviews_complete else None
    return {"threads_complete": threads_complete,
            "unresolved": len(unresolved),
            "unresolved_bot": sum(1 for t in unresolved if t["bot"]),
            "threads": unresolved[:REVIEW_THREADS_KEPT],
            "head_reviewed": head_reviewed}


def normalize_pr(raw: dict, cfg: TriageConfig = DEFAULT) -> dict:
    checks = sorted(
        ({"name": c.get("name") or c.get("context") or "?",
          "workflow": c.get("workflowName") or "",
          "conclusion": ((c.get("conclusion") if c.get("__typename") != "StatusContext" else c.get("state")) or "").upper(),
          "state": check_state(c)}
         for c in (raw.get("statusCheckRollup") or [])),
        key=lambda c: (c["workflow"], c["name"]))
    labels = sorted(l.get("name", "") for l in (raw.get("labels") or []))
    author = raw.get("author") or {}
    is_draft = raw.get("isDraft")
    head_sha = _opt_str(raw.get("headRefOid"))
    out = {
        "number": raw["number"],
        "title": raw.get("title") or "",
        "url": raw.get("url") or "",
        "head_sha": head_sha,
        "author": author.get("login") or "",
        "is_draft": is_draft if isinstance(is_draft, bool) else None,
        "mergeable": _opt_str(raw.get("mergeable")),
        "merge_state": _opt_str(raw.get("mergeStateStatus")),
        "review_decision": _opt_str(raw.get("reviewDecision")),
        "checks": checks,
        "ci": ci_state(checks, cfg),
        "labels": labels,
    }
    # GH.list_prs always attaches reviewData (None when GitHub returned no PR object). Raw PRs without the key
    # come from the original script's inputs and keep its facts, digest, labels, and report unchanged.
    if "reviewData" in raw:
        out["review"] = review_facts(raw["reviewData"], head_sha, cfg)
    return out


def classify(pr: dict, cfg: TriageConfig = DEFAULT) -> Set[str]:
    """Managed label set. Green CI without review approval is not ready. Missing or UNKNOWN facts give unknown.
    Unresolved review threads or a configured bot that has not reviewed the head commit also keep a PR from
    ready."""
    out: Set[str] = set()
    unknown = False
    review_gate = False
    review = pr.get("review")
    if review is not None:
        if review["unresolved"]:
            out.add(cfg.label("unresolved-review"))
            review_gate = True
        if not review["threads_complete"]:
            unknown = True
        states = list(review["head_reviewed"].values())
        if False in states:
            out.add(cfg.label("needs-bot-review"))
            review_gate = True
        if None in states:
            unknown = True
    if pr["is_draft"] is None:
        unknown = True
    elif pr["is_draft"]:
        out.add(cfg.label("draft"))
    if pr["mergeable"] == "CONFLICTING":
        out.add(cfg.label("conflict"))
    elif pr["mergeable"] != "MERGEABLE":
        unknown = True
    if pr["ci"] == "fail":
        out.add(cfg.label("ci-failing"))
    elif pr["ci"] == "pending":
        out.add(cfg.label("ci-pending"))
    elif pr["ci"] == "unknown":
        unknown = True
    decision = pr["review_decision"]
    if decision == "REVIEW_REQUIRED":
        out.add(cfg.label("needs-review"))
    elif decision == "CHANGES_REQUESTED":
        out.add(cfg.label("changes-requested"))
    elif decision != "APPROVED":
        unknown = True
    if pr["head_sha"] is None:
        unknown = True
    if (not unknown and not review_gate and pr["is_draft"] is False and pr["mergeable"] == "MERGEABLE"
            and pr["ci"] == "pass" and decision == "APPROVED"):
        if pr["merge_state"] in ("CLEAN", "HAS_HOOKS"):
            out.add(cfg.label("ready"))
        elif pr["merge_state"] in (None, "UNKNOWN"):
            unknown = True
        else:
            out.add(cfg.label("blocked"))  # BLOCKED, BEHIND, UNSTABLE, DIRTY, ...
    if unknown or not out:
        out.add(cfg.label("unknown"))  # never let the classification become empty
    return out


def managed(labels: List[str], cfg: TriageConfig = DEFAULT) -> Set[str]:
    return {l for l in labels if l in cfg.pr_labels}


def plan_labels(pr: dict, cfg: TriageConfig = DEFAULT) -> Tuple[List[str], List[str]]:
    """(add, remove). Only managed labels are touched; other labels are left alone."""
    desired = classify(pr, cfg)
    current = managed(pr["labels"], cfg)
    return sorted(desired - current), sorted(current - desired)


def facts_digest(prs: List[dict]) -> str:
    """Digest of the facts (excluding current managed labels). Timestamps and URLs are not included."""
    facts = []
    for p in sorted(prs, key=lambda p: p["number"]):
        fact = {k: p[k] for k in ("number", "title", "head_sha", "author", "is_draft", "mergeable",
                                  "merge_state", "review_decision", "checks", "ci")}
        if p.get("review") is not None:  # absent for original-script inputs, so their digest is unchanged
            fact["review"] = p["review"]
        facts.append(fact)
    blob = json.dumps({"v": DIGEST_VERSION, "prs": facts}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def find_report_issue(issues: List[dict], cfg: TriageConfig = DEFAULT) -> Optional[dict]:
    """The single open issue created by the bot with the report label, title, and fixed marker. Issues created
    by people are ignored. If several qualify, raise TriageError instead of guessing."""
    owned = [i for i in issues
             if (i.get("author") or {}).get("login") in BOT_LOGINS
             and any(l.get("name") == cfg.report_label for l in (i.get("labels") or []))
             and (i.get("state") or "OPEN").upper() == "OPEN"
             and i.get("title") == cfg.report_title
             and cfg.report_marker in (i.get("body") or "")]
    if len(owned) > 1:
        raise TriageError(f"봇 소유 보고서 이슈가 여럿이다: {sorted(i['number'] for i in owned)}. 하나만 남기고 닫아야 한다.")
    return owned[0] if owned else None


def parse_digest(body: Optional[str], cfg: TriageConfig = DEFAULT) -> Optional[str]:
    m = cfg.digest_re.search(body or "")
    return m.group(1) if m else None


# ---------------------------------------------------------------- Claude output validation and report

def _clean(text: str, limit: int) -> str:
    """Collapse to one line and neutralize tables, HTML, mentions, and digest markers."""
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("<!--", "").replace("-->", "").replace("<", "‹").replace(">", "›")
    text = text.replace("|", "\\|").replace("@", "@​").replace("`", "'")
    return text[:limit].rstrip()


def validate_ai(ai: object, snapshot: dict) -> Dict[int, dict]:
    """Check the digest, the PR number set (each exactly once), the priority enum, and non-empty next actions;
    raise ValueError otherwise."""
    if not isinstance(ai, dict):
        raise ValueError("구조화 출력이 객체가 아니다")
    if ai.get("digest") != snapshot["digest"]:
        raise ValueError("digest 가 스냅샷과 다르다")
    items = ai.get("items")
    if not isinstance(items, list):
        raise ValueError("items 가 배열이 아니다")
    expected = {p["number"] for p in snapshot["prs"]}
    out: Dict[int, dict] = {}
    for it in items:
        if not isinstance(it, dict) or not isinstance(it.get("number"), int):
            raise ValueError("항목 형식이 틀렸다")
        n = it["number"]
        if n in out:
            raise ValueError(f"PR #{n} 가 두 번 나온다")
        if n not in expected:
            raise ValueError(f"스냅샷에 없는 PR #{n}")
        if it.get("priority") not in PRIORITIES:
            raise ValueError(f"PR #{n} 의 우선순위가 허용값이 아니다")
        action = it.get("next_action")
        if not isinstance(action, str) or not action.strip():
            raise ValueError(f"PR #{n} 의 다음 조치가 비었다")
        out[n] = {"priority": it["priority"], "next_action": _clean(action, 160)}
    if set(out) != expected:
        raise ValueError(f"빠진 PR: {sorted(expected - set(out))}")
    return out


DRAFT_TEXT = {True: "예", False: "아니오", None: "미확인"}
MERGE_TEXT = {"MERGEABLE": "없음", "CONFLICTING": "있음"}
CI_TEXT = {"pass": "통과", "fail": "실패", "pending": "진행 중", "unknown": "미확인"}
REVIEW_TEXT = {"APPROVED": "승인", "REVIEW_REQUIRED": "승인 대기", "CHANGES_REQUESTED": "변경 요청"}
HEAD_REVIEW_TEXT = {True: "완료", False: "대기", None: "미확인"}


def _threads_text(review: Optional[dict]) -> str:
    if review is None:
        return "미확인"
    text = str(review["unresolved"])
    if review["unresolved_bot"]:
        text += f" (봇 {review['unresolved_bot']})"
    if not review["threads_complete"]:
        text += " · 일부 미확인"
    return text


def _head_review_text(review: Optional[dict]) -> str:
    if review is None:
        return "미확인"
    states = review["head_reviewed"]
    if not states:
        return "-"  # no review_bots configured
    return _clean(", ".join(f"{login} {HEAD_REVIEW_TEXT[state]}" for login, state in sorted(states.items())),
                  REPORT_BOT_CELL)


def _thread_lines(prs: List[dict]) -> List[str]:
    """One line per PR with unresolved threads: path and first author, never the comment text. The list stops at
    REPORT_THREAD_CHARS so the issue body stays within GitHub's limit."""
    lines: List[str] = []
    used = 0
    pending = [p for p in prs if p.get("review") and p["review"]["unresolved"]]
    for i, p in enumerate(pending):
        review = p["review"]
        shown = [f"{_clean(t['path'], 120) or '?'} ({_clean(t['author'], 60) or '미상'})"
                 for t in review["threads"]]
        rest = review["unresolved"] - len(shown)
        bots = f" (봇 {review['unresolved_bot']})" if review["unresolved_bot"] else ""
        partial = "" if review["threads_complete"] else " (일부 미확인)"
        line = (f"- #{p['number']}: 미해결 {review['unresolved']}건{bots}{partial} · " + ", ".join(shown)
                + (f" 외 {rest}건" if rest > 0 else ""))
        if used + len(line) + 1 > REPORT_THREAD_CHARS:
            lines.append(f"- 외 PR {len(pending) - i}개는 길이 제한으로 생략했다. 각 PR 화면에서 확인한다.")
            break
        lines.append(line)
        used += len(line) + 1
    return lines


def render(snapshot: dict, items: Dict[int, dict], run_url: str, now: str,
           cfg: TriageConfig = DEFAULT) -> str:
    lines = [
        "## PR 현황 보고서",
        "",
        cfg.report_marker,
        cfg.digest_marker(snapshot["digest"]),
        f"마지막 갱신 {now} · [실행]({run_url})",
        "",
        "라벨과 상태 열은 GitHub 의 사실에서 결정적으로 정했다. 우선순위와 다음 조치는 Claude 가 같은 사실로 쓴 제안이며,",
        "이 보고서는 PR 을 병합·종료하지 않는다. 사실이 바뀌지 않으면 갱신하지 않는다.",
        "",
    ]
    if not snapshot["prs"]:
        lines.append("열린 PR 이 없다.")
        return "\n".join(lines) + "\n"
    # Review columns appear only for snapshots with review facts, so original-script inputs render as before.
    with_review = any("review" in p for p in snapshot["prs"])
    review_head = " 미해결 대화 | 봇 head 리뷰 |" if with_review else ""
    lines += ["| PR | head | Draft | 충돌 | CI | 리뷰 |" + review_head + " 라벨 | 우선순위 | 다음 조치 |",
              "| --- " * (11 if with_review else 9) + "|"]
    order = {p: i for i, p in enumerate(PRIORITIES)}
    def rank(p: dict) -> Tuple[int, int]:
        return order.get(str(items.get(p["number"], {}).get("priority", "")), 9), p["number"]
    prs = sorted(snapshot["prs"], key=rank)
    for p in prs:
        it = items.get(p["number"], {"priority": "-", "next_action": "-"})
        labels = ", ".join(l.replace(cfg.label_namespace, "") for l in sorted(classify(p, cfg))) or "-"
        review_cells = (f"| {_threads_text(p.get('review'))} | {_head_review_text(p.get('review'))} "
                        if with_review else "")
        lines.append(
            f"| #{p['number']} {_clean(p['title'], 80)} | {(p['head_sha'] or '미확인')[:7]} "
            f"| {DRAFT_TEXT[p['is_draft']]} | {MERGE_TEXT.get(p['mergeable'] or '', '미확인')} "
            f"| {CI_TEXT[p['ci']]} | {REVIEW_TEXT.get(p['review_decision'] or '', '미확인')} "
            + review_cells +
            f"| {labels} | {it['priority']} | {it['next_action']} |")
    threads = _thread_lines(prs)
    if threads:
        lines += ["", "### 미해결 리뷰 대화", "",
                  "파일 경로와 첫 코멘트 작성자만 적는다. 병합 전에 반영하거나 이유를 답글로 남긴 뒤 대화를 해결 처리한다.",
                  ""] + threads
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- GitHub I/O

def _subprocess_runner(args: List[str], stdin: Optional[str]) -> Tuple[int, str, str]:
    res = subprocess.run(["gh"] + args, input=stdin, capture_output=True, text=True)
    return res.returncode, res.stdout, res.stderr


def label_path(name: str) -> str:
    """Encode a label name as one REST path segment so that the "/" in pr-triage/foo is not a separator."""
    return urllib.parse.quote(name, safe="")


class GH:
    """Thin gh CLI wrapper. With dry_run, non-GET calls are printed, not executed. runner is replaceable in tests."""

    def __init__(self, repo: str, dry_run: bool = False,
                 runner: Callable[[List[str], Optional[str]], Tuple[int, str, str]] = _subprocess_runner,
                 cfg: TriageConfig = DEFAULT):
        self.repo = repo
        self.dry_run = dry_run
        self.runner = runner
        self.cfg = cfg
        self.mutations: List[str] = []

    def _run(self, args: List[str], stdin: Optional[str] = None) -> str:
        rc, out, err = self.runner(args, stdin)
        if rc != 0:
            raise RuntimeError(f"gh {' '.join(args[:3])} 실패: {err.strip()[:300]}")
        return out

    def list_prs(self) -> List[dict]:
        """Open PRs, each with reviewData (its review threads and latest reviews, or None) attached."""
        prs = json.loads(self._run([
            "pr", "list", "-R", self.repo, "--state", "open", "--limit", str(PR_LIMIT + 1), "--json",
            "number,title,url,headRefOid,isDraft,mergeable,mergeStateStatus,reviewDecision,author,labels,statusCheckRollup"]))
        if len(prs) <= PR_LIMIT:  # collect refuses a longer list, so it is not worth querying
            data = self.review_data([p.get("number") for p in prs])
            for p in prs:
                p["reviewData"] = data.get(p["number"])
        return prs

    def review_data(self, numbers: List[object]) -> Dict[int, Optional[dict]]:
        """Read-only GraphQL query of review threads and reviews, REVIEW_QUERY_CHUNK PRs per request.
        Only validated PR numbers are placed in the query text; owner and name are variables."""
        match = REPO_RE.fullmatch(self.repo)
        if not match:
            raise TriageError(f"저장소 이름 형식이 틀렸다: {self.repo!r}")
        if any(type(n) is not int or n <= 0 for n in numbers):
            raise TriageError("PR 번호가 양의 정수가 아니다")
        unique = list(dict.fromkeys(numbers))  # a repeated alias would be a GraphQL error
        out: Dict[int, Optional[dict]] = {}
        for start in range(0, len(unique), REVIEW_QUERY_CHUNK):
            chunk = unique[start:start + REVIEW_QUERY_CHUNK]
            per_pr = REVIEW_FIELDS + (" " + COMMENT_FIELDS if self.cfg.review_state_markers else "")
            fields = " ".join(f"p{n}: pullRequest(number: {n}) {{ {per_pr} }}" for n in chunk)
            query = ("query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) { "
                     + fields + " } }")
            repo = self._graphql(query, match, {})
            for n in chunk:
                node = repo.get(f"p{n}")
                out[n] = node if isinstance(node, dict) and node.get("number") == n else None
                if out[n] is not None:
                    self._more_threads(n, out[n], match)
        return out

    def _graphql(self, query: str, match: "re.Match[str]", variables: Dict[str, str]) -> dict:
        args = ["api", "graphql", "-f", f"query={query}", "-f", f"owner={match.group(1)}",
                "-f", f"name={match.group(2)}"]
        for key, value in variables.items():
            args += ["-F" if key == "number" else "-f", f"{key}={value}"]
        try:
            reply = json.loads(self._run(args))
        except (RuntimeError, json.JSONDecodeError) as e:
            raise TriageError(f"리뷰 대화를 읽지 못했다. 일부만 읽은 사실로 게시하지 않는다: {str(e)[:200]}") from None
        data = reply.get("data") if isinstance(reply, dict) else None
        repo = data.get("repository") if isinstance(data, dict) else None
        if not isinstance(repo, dict) or reply.get("errors"):
            raise TriageError("리뷰 대화를 읽지 못했다. 일부만 읽은 사실로 게시하지 않는다.")
        return repo

    def _more_threads(self, number: int, node: dict, match: "re.Match[str]") -> None:
        """Append later review-thread pages (resolved threads count toward totalCount) up to
        REVIEW_THREAD_PAGES. A page that cannot be followed leaves the connection short, which is unknown."""
        conn = node.get("reviewThreads")
        for _ in range(REVIEW_THREAD_PAGES - 1):
            info = conn.get("pageInfo") if isinstance(conn, dict) else None
            cursor = info.get("endCursor") if isinstance(info, dict) else None
            if not (isinstance(info, dict) and info.get("hasNextPage") is True and isinstance(cursor, str)
                    and isinstance(conn.get("nodes"), list)):
                return
            repo = self._graphql(THREAD_PAGE_QUERY, match, {"number": str(number), "cursor": cursor})
            page = (repo.get("pullRequest") or {}).get("reviewThreads")
            if not isinstance(page, dict) or not isinstance(page.get("nodes"), list):
                return
            conn["nodes"] += page["nodes"]
            conn["pageInfo"] = page.get("pageInfo")

    def list_report_issues(self) -> List[dict]:
        return json.loads(self._run([
            "issue", "list", "-R", self.repo, "--state", "open", "--label", self.cfg.report_label,
            "--limit", str(REPORT_LIMIT + 1), "--json", "number,title,author,labels,body,state"]))

    def label_exists(self, name: str) -> bool:
        rc, _, _ = self.runner(["api", f"repos/{self.repo}/labels/{label_path(name)}"], None)
        return rc == 0

    def mutate(self, desc: str, args: List[str], stdin: Optional[str] = None) -> None:
        self.mutations.append(desc)
        if self.dry_run:
            print(f"[dry-run] {desc}")
            return
        self._run(args, stdin)

    def create_label(self, name: str) -> None:
        color, description = self.cfg.labels[name]
        self.mutate(f"라벨 생성 {name}", ["api", "-X", "POST", f"repos/{self.repo}/labels",
                                        "-f", f"name={name}", "-f", f"color={color}", "-f", f"description={description}"])

    def add_labels(self, number: int, names: List[str]) -> None:
        args = ["api", "-X", "POST", f"repos/{self.repo}/issues/{number}/labels"]
        for n in names:
            args += ["-f", f"labels[]={n}"]
        self.mutate(f"#{number} 라벨 추가 {names}", args)

    def remove_label(self, number: int, name: str) -> None:
        self.mutate(f"#{number} 라벨 제거 {name}",
                    ["api", "-X", "DELETE", f"repos/{self.repo}/issues/{number}/labels/{label_path(name)}"])

    def update_issue(self, number: int, body: str) -> None:
        self.mutate(f"보고서 이슈 #{number} 본문 갱신", ["api", "-X", "PATCH", f"repos/{self.repo}/issues/{number}",
                                                    "-F", "body=@-"], stdin=body)

    def create_issue(self, body: str) -> None:
        self.mutate("보고서 이슈 생성", ["issue", "create", "-R", self.repo, "--title", self.cfg.report_title,
                                    "--label", self.cfg.report_label, "--body-file", "-"], stdin=body)


def _same_config(gh, cfg: TriageConfig) -> None:
    """Refuse to mix a GitHub wrapper built with another config (test doubles without cfg pass)."""
    other = getattr(gh, "cfg", cfg)
    if other != cfg:
        raise ValueError("GitHub 래퍼와 triage 설정이 다르다")


def collect(gh, cfg: TriageConfig = DEFAULT) -> dict:
    _same_config(gh, cfg)
    raw_prs = gh.list_prs()
    if len(raw_prs) > PR_LIMIT:
        raise TriageError(f"열린 PR 이 {PR_LIMIT} 개를 넘는다. 잘린 목록을 전체라고 게시하지 않는다.")
    prs = [normalize_pr(raw, cfg) for raw in raw_prs]
    prs.sort(key=lambda p: p["number"])
    issues = gh.list_report_issues()
    if len(issues) > REPORT_LIMIT:
        raise TriageError(f"보고서 라벨 이슈가 {REPORT_LIMIT} 개를 넘는다. 정리가 필요하다.")
    report = find_report_issue(issues, cfg)
    digest = facts_digest(prs)
    report_digest = parse_digest(report.get("body"), cfg) if report else None
    return {
        "version": DIGEST_VERSION,
        "repo": gh.repo,
        "digest": digest,
        "prs": prs,
        "report_issue": {"number": report["number"], "digest": report_digest} if report else None,
        "facts_changed": report_digest != digest,
        "label_drift": any(plan_labels(p, cfg) != ([], []) for p in prs),
        "config_id": cfg.config_id(),
    }


def publish(gh, snapshot: dict, ai: Optional[object], run_url: str, now: str,
            cfg: TriageConfig = DEFAULT) -> str:
    """Publish. Returns published / labels-only / already-published; raises Stale for a stale snapshot."""
    # A snapshot without config_id comes from the original script, so it is treated as the default config.
    if snapshot.get("config_id", DEFAULT.config_id()) != cfg.config_id():
        raise ValueError("스냅샷을 만든 triage 설정과 게시 설정이 다르다")
    fresh = collect(gh, cfg)
    if fresh["digest"] != snapshot["digest"]:
        raise Stale("게시 직전 다시 모은 사실이 스냅샷과 다르다 (head·CI·리뷰 등이 바뀜). 낡은 상태로 쓰지 않는다.")
    report = fresh["report_issue"]
    need_body = not (report and report["digest"] == snapshot["digest"])
    items: Dict[int, dict] = {}
    if need_body:
        if fresh["prs"]:
            if ai is None:
                raise ValueError("사실이 바뀌었는데 검증할 Claude 출력이 없다")
            items = validate_ai(ai, snapshot)
    plans = [(p, plan_labels(p, cfg)) for p in fresh["prs"]]
    if not need_body and all(plan == ([], []) for _, plan in plans):
        return "already-published"
    body = render(snapshot, items, run_url, now, cfg) if need_body else ""
    if len(body) > GITHUB_BODY_MAX:  # checked before any write; the bounded render should never get here
        raise TriageError(f"보고서 본문이 GitHub 상한 {GITHUB_BODY_MAX} 자를 넘는다 ({len(body)} 자)")
    needed = {l for _, (add, _) in plans for l in add} | ({cfg.report_label} if need_body and not report else set())
    for name in sorted(needed):
        if not gh.label_exists(name):
            gh.create_label(name)
    for p, (add, remove) in plans:
        if add:
            gh.add_labels(p["number"], add)
        for name in remove:
            gh.remove_label(p["number"], name)
    if not need_body:
        return "labels-only"
    if report:
        gh.update_issue(report["number"], body)
    else:
        gh.create_issue(body)
    return "published"


# ---------------------------------------------------------------- CLI

def _add_config_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help="버전 있는 triage 설정 JSON 파일")
    parser.add_argument("--label-namespace")
    parser.add_argument("--report-title")
    parser.add_argument("--required-check", action="append", dest="required_checks")
    parser.add_argument("--marker-id")
    parser.add_argument("--review-bot", action="append", dest="review_bots")
    parser.add_argument("--review-state-marker", action="append", dest="review_state_markers")


def config_from_args(args: argparse.Namespace) -> TriageConfig:
    flags = {k: getattr(args, k)
             for k in ("label_namespace", "report_title", "required_checks", "marker_id", "review_bots",
                       "review_state_markers")
             if getattr(args, k) is not None}
    if args.config:
        if flags:
            raise ConfigError("--config 와 개별 설정 인자를 함께 쓸 수 없다")
        with open(args.config, encoding="utf-8") as f:
            return TriageConfig.from_mapping(json.load(f))
    for key in ("required_checks", "review_bots", "review_state_markers"):
        if key in flags:
            flags[key] = tuple(flags[key])
    return TriageConfig(**flags)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--repo", required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--github-output")
    _add_config_args(c)
    v = sub.add_parser("validate")
    v.add_argument("--snapshot", required=True)
    v.add_argument("--ai", required=True)
    p = sub.add_parser("publish")
    p.add_argument("--repo", required=True)
    p.add_argument("--snapshot", required=True)
    p.add_argument("--ai")
    p.add_argument("--run-url", default="")
    p.add_argument("--dry-run", action="store_true")
    _add_config_args(p)
    args = ap.parse_args(argv)

    cfg = DEFAULT
    if args.cmd in ("collect", "publish"):
        try:
            cfg = config_from_args(args)
        except (ConfigError, OSError, json.JSONDecodeError) as e:
            print(f"::error::triage 설정 오류: {e}")
            return 1

    if args.cmd == "collect":
        try:
            snap = collect(GH(args.repo, cfg=cfg), cfg)
        except TriageError as e:
            print(f"::error::{e}")
            return 1
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=1)
        print(f"PR {len(snap['prs'])} 개, digest {snap['digest'][:12]}, "
              f"사실 변화 {snap['facts_changed']}, 라벨 어긋남 {snap['label_drift']}")
        for pr in snap["prs"]:
            add, remove = plan_labels(pr, cfg)
            review = pr.get("review")
            threads = f" unresolved={review['unresolved']} bots={review['head_reviewed']}" if review else ""
            print(f"  #{pr['number']} head={(pr['head_sha'] or '?')[:7]} ci={pr['ci']} review={pr['review_decision']} "
                  f"mergeable={pr['mergeable']}{threads} -> {sorted(classify(pr, cfg))} (+{add} -{remove})")
        if args.github_output:
            with open(args.github_output, "a", encoding="utf-8") as f:
                f.write(f"facts_changed={'true' if snap['facts_changed'] else 'false'}\n")
                f.write(f"label_drift={'true' if snap['label_drift'] else 'false'}\n")
                f.write(f"pr_count={len(snap['prs'])}\n")
                f.write(f"digest={snap['digest']}\n")
        return 0

    with open(args.snapshot, encoding="utf-8") as f:
        snapshot = json.load(f)
    if args.cmd == "validate":
        with open(args.ai, encoding="utf-8") as f:
            raw = f.read()
        try:
            items = validate_ai(json.loads(raw) if raw.strip() else None, snapshot)
        except ValueError as e:
            print(f"::error::Claude 출력 검증 실패: {e}")
            return 1
        for n, it in sorted(items.items()):
            print(f"  #{n} [{it['priority']}] {it['next_action']}")
        return 0

    ai = None
    if args.ai:
        with open(args.ai, encoding="utf-8") as f:
            raw = f.read()
        ai = json.loads(raw) if raw.strip() else None
    gh = GH(args.repo, dry_run=args.dry_run, cfg=cfg)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    try:
        result = publish(gh, snapshot, ai, args.run_url, now, cfg)
    except Stale as e:
        print(f"::warning::{e}")
        return 3
    except (ValueError, TriageError) as e:
        print(f"::error::{e}")
        return 1
    print(f"결과: {result}, 변경 {len(gh.mutations)} 건")
    return 0


if __name__ == "__main__":
    sys.exit(main())

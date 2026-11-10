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

Config arguments (collect and publish; use one form only):
  --config FILE                  {"version": 1, "label_namespace", "report_title", "required_checks", "marker_id"}
  --label-namespace / --report-title / --required-check (repeatable) / --marker-id

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

# Managed label suffixes with (color, description); full names are namespace + suffix.
LABEL_SPECS: Dict[str, Tuple[str, str]] = {
    "ready": ("0e8a16", "Draft 아님, 충돌 없음, CI 통과, 리뷰 승인, 병합 가능 모두 확인됨"),
    "needs-review": ("fbca04", "리뷰 승인 대기"),
    "changes-requested": ("d93f0b", "리뷰가 변경을 요청함"),
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
        return {"version": CONFIG_VERSION, "label_namespace": self.label_namespace,
                "report_title": self.report_title, "required_checks": list(self.required_checks),
                "marker_id": self.marker_id}

    def config_id(self) -> str:
        blob = json.dumps(self.as_dict(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    @classmethod
    def from_mapping(cls, data: object) -> "TriageConfig":
        """Load a versioned config object. Unknown keys and other versions are rejected; missing keys default."""
        if not isinstance(data, dict):
            raise ConfigError("설정은 JSON 객체여야 한다")
        allowed = {"version", "label_namespace", "report_title", "required_checks", "marker_id"}
        unknown = set(data) - allowed
        if unknown:
            raise ConfigError(f"모르는 설정 키: {sorted(unknown)}")
        if data.get("version") != CONFIG_VERSION:
            raise ConfigError(f"설정 version 은 {CONFIG_VERSION} 이어야 한다")
        kwargs = {k: v for k, v in data.items() if k != "version"}
        if "required_checks" in kwargs:
            if not isinstance(kwargs["required_checks"], list):
                raise ConfigError("required_checks 는 배열이어야 한다")
            kwargs["required_checks"] = tuple(kwargs["required_checks"])
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
    return {
        "number": raw["number"],
        "title": raw.get("title") or "",
        "url": raw.get("url") or "",
        "head_sha": _opt_str(raw.get("headRefOid")),
        "author": author.get("login") or "",
        "is_draft": is_draft if isinstance(is_draft, bool) else None,
        "mergeable": _opt_str(raw.get("mergeable")),
        "merge_state": _opt_str(raw.get("mergeStateStatus")),
        "review_decision": _opt_str(raw.get("reviewDecision")),
        "checks": checks,
        "ci": ci_state(checks, cfg),
        "labels": labels,
    }


def classify(pr: dict, cfg: TriageConfig = DEFAULT) -> Set[str]:
    """Managed label set. Green CI without review approval is not ready. Missing or UNKNOWN facts give unknown."""
    out: Set[str] = set()
    unknown = False
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
    review = pr["review_decision"]
    if review == "REVIEW_REQUIRED":
        out.add(cfg.label("needs-review"))
    elif review == "CHANGES_REQUESTED":
        out.add(cfg.label("changes-requested"))
    elif review != "APPROVED":
        unknown = True
    if pr["head_sha"] is None:
        unknown = True
    if (not unknown and pr["is_draft"] is False and pr["mergeable"] == "MERGEABLE"
            and pr["ci"] == "pass" and review == "APPROVED"):
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
    facts = [{k: p[k] for k in ("number", "title", "head_sha", "author", "is_draft", "mergeable",
                                "merge_state", "review_decision", "checks", "ci")}
             for p in sorted(prs, key=lambda p: p["number"])]
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
    lines += ["| PR | head | Draft | 충돌 | CI | 리뷰 | 라벨 | 우선순위 | 다음 조치 |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    order = {p: i for i, p in enumerate(PRIORITIES)}
    def rank(p: dict) -> Tuple[int, int]:
        return order.get(str(items.get(p["number"], {}).get("priority", "")), 9), p["number"]
    prs = sorted(snapshot["prs"], key=rank)
    for p in prs:
        it = items.get(p["number"], {"priority": "-", "next_action": "-"})
        labels = ", ".join(l.replace(cfg.label_namespace, "") for l in sorted(classify(p, cfg))) or "-"
        lines.append(
            f"| #{p['number']} {_clean(p['title'], 80)} | {(p['head_sha'] or '미확인')[:7]} "
            f"| {DRAFT_TEXT[p['is_draft']]} | {MERGE_TEXT.get(p['mergeable'] or '', '미확인')} "
            f"| {CI_TEXT[p['ci']]} | {REVIEW_TEXT.get(p['review_decision'] or '', '미확인')} "
            f"| {labels} | {it['priority']} | {it['next_action']} |")
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
        return json.loads(self._run([
            "pr", "list", "-R", self.repo, "--state", "open", "--limit", str(PR_LIMIT + 1), "--json",
            "number,title,url,headRefOid,isDraft,mergeable,mergeStateStatus,reviewDecision,author,labels,statusCheckRollup"]))

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
    body = render(snapshot, items, run_url, now, cfg)
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


def config_from_args(args: argparse.Namespace) -> TriageConfig:
    flags = {k: getattr(args, k) for k in ("label_namespace", "report_title", "required_checks", "marker_id")
             if getattr(args, k) is not None}
    if args.config:
        if flags:
            raise ConfigError("--config 와 개별 설정 인자를 함께 쓸 수 없다")
        with open(args.config, encoding="utf-8") as f:
            return TriageConfig.from_mapping(json.load(f))
    if "required_checks" in flags:
        flags["required_checks"] = tuple(flags["required_checks"])
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
            print(f"  #{pr['number']} head={(pr['head_sha'] or '?')[:7]} ci={pr['ci']} review={pr['review_decision']} "
                  f"mergeable={pr['mergeable']} -> {sorted(classify(pr, cfg))} (+{add} -{remove})")
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

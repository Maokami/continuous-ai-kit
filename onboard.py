#!/usr/bin/env python3
"""Inspect a repository and preview or install reviewed, manually triggered AI lanes."""

import argparse
from collections import Counter
import fnmatch
import json
import math
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import install
from cai.config import path_name
from cai.triage import TriageConfig


def git_bytes(root, *args):
    return subprocess.check_output(["git", "-c", "core.fsmonitor=false", "-C", str(root), *args],
                                   stderr=subprocess.PIPE)


def git(root, *args):
    return git_bytes(root, *args).decode()


def repository(root):
    try:
        root = root.resolve(strict=True)
        top = Path(git(root, "rev-parse", "--show-toplevel").strip()).resolve()
        git(root, "rev-parse", "HEAD")
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("Pass an initialized, non-bare Git repository with at least one commit") from exc
    if top != root:
        raise ValueError("Pass the repository root, not a subdirectory")
    return root


def tracked(root):
    return git(root, "ls-files", "-z").split("\0")[:-1]


def inspect(root):
    root = repository(root)
    names = tracked(root)
    return {
        "head": git(root, "rev-parse", "HEAD").strip(),
        "branch": git(root, "branch", "--show-current").strip(),
        "tracked_files": len(names),
        "extensions": dict(Counter(Path(p).suffix for p in names).most_common(15)),
        "policy_candidates": [p for p in names if Path(p).name in {"AGENTS.md", "CLAUDE.md"}],
        "workflows": [p for p in names if p.startswith(".github/workflows/")],
        "existing_lane_configs": [p for p in names if p.startswith(".github/continuous-ai/")
                                  and p.endswith(".json")],
        "note": "Local inventory only. Verify remote default branch, required checks and existing schedules separately.",
    }


def input_bytes(root, name):
    path_name(name)
    path = root / name
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
        raise ValueError("Source/policy must be a regular non-symlink file: " + name)
    entry = git(root, "ls-tree", "HEAD", "--", name)
    if not entry.startswith(("100644 blob ", "100755 blob ")):
        raise ValueError("Source/policy must be committed in HEAD: " + name)
    pinned = git_bytes(root, "show", "HEAD:" + name)
    if len(pinned) > 250_000 or path.read_bytes() != pinned:
        raise ValueError("Source/policy is oversized or differs from HEAD: " + name)
    try:
        pinned.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Source/policy must be UTF-8 text: " + name) from exc
    return pinned


def scan_scope(root, globs, policies, batch_size):
    for pattern in globs:
        path_name(pattern, glob=True)
    names = sorted(p for p in tracked(root)
                   if any(fnmatch.fnmatchcase(p, pattern) for pattern in globs))
    if not names or len(names) > 1000:
        raise ValueError("Source selection is empty or exceeds 1000 files")
    if set(names) & set(policies):
        raise ValueError("Policy files must be outside source_globs; narrow the source selection")
    size = sum(len(input_bytes(root, p)) for p in names)
    if size > 2_000_000:
        raise ValueError("Source selection exceeds 2 MB; split the lane")
    for p in policies:
        input_bytes(root, p)
    return {"files": names, "bytes": size, "batches": math.ceil(len(names) / batch_size),
            "note": "One batch per dispatch. Source selection is not a filesystem access sandbox."}


def render_triage(revision, lane, branch, checks):
    install.identity(revision, lane, branch)
    if len(lane) > 31 or not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", lane):
        raise ValueError("Triage --lane needs at most 31 lowercase letters/digits separated by single hyphens")
    cfg = TriageConfig(label_namespace=lane + "/", marker_id=lane,
                       report_title=f"PR status report ({lane})", required_checks=tuple(checks))
    config_path = f".github/continuous-ai/{lane}.json"
    config = {"version": 1, "label_namespace": cfg.label_namespace, "marker_id": cfg.marker_id,
              "report_title": cfg.report_title, "required_checks": list(cfg.required_checks)}
    workflow = f'''name: Continuous AI ({lane})
on:
  schedule:
    # Recovery checks only; add a distinct cron for new maintenance work.
    - cron: '17 * * * *'
  workflow_dispatch:
    inputs:
      reset_quota:
        description: Reset an exhausted quota recovery budget
        type: boolean
        default: false
permissions: {{}}
concurrency:
  group: continuous-ai-{lane}
  cancel-in-progress: false
jobs:
  triage:
    if: >-
      github.ref == 'refs/heads/{branch}' &&
      (github.event_name != 'schedule' || vars.CAI_QUOTA_RECOVERY_DISABLED != 'true')
    permissions:
      contents: read
      issues: write
      pull-requests: write
      checks: read
      statuses: read
      actions: read
    uses: Maokami/continuous-ai-kit/.github/workflows/pr-triage.yml@{revision}
    with:
      config: {config_path}
      default_branch: {json.dumps(branch)}
      recovery_only: ${{{{ github.event_name == 'schedule' && github.event.schedule == '17 * * * *' }}}}
      reset_quota: ${{{{ inputs.reset_quota || false }}}}
    secrets:
      claude_oauth_token: ${{{{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}}}
'''
    return {config_path: json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            f".github/workflows/continuous-ai-{lane}.yml": workflow}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    view = commands.add_parser("inspect", help="Read local Git inventory; execute no repository code")
    view.add_argument("repository", type=Path)
    for kind in ("scan", "triage"):
        command = commands.add_parser(kind, help="Preview by default; --write creates local files")
        command.add_argument("repository", type=Path)
        command.add_argument("--ref", required=True, help="Reviewed full SHA of the public Kit")
        command.add_argument("--lane", default="improvements" if kind == "scan" else "pr-triage")
        command.add_argument("--default-branch", required=True)
        command.add_argument("--write", action="store_true")
        if kind == "scan":
            command.add_argument("--profile", choices=install.PROFILES, default="general")
            command.add_argument("--source-glob", action="append", required=True)
            command.add_argument("--policy-file", action="append", default=[])
            command.add_argument("--batch-size", type=int, default=4)
            command.add_argument("--max-turns", type=int, default=32)
            command.add_argument("--model", default="claude-sonnet-5-5")
        else:
            command.add_argument("--required-check", action="append", required=True,
                                 help="Exact required status/check context; repeat for every required check")
    args = parser.parse_args(argv)
    try:
        root = repository(args.repository)
        if args.command == "inspect":
            result = inspect(root)
        else:
            scope = None
            if args.command == "scan":
                files = install.render_scan(args.ref, args.lane, args.source_glob,
                                            "lean" if args.profile == "lean" else "text",
                                            args.default_branch, profile=args.profile,
                                            policies=args.policy_file, model=args.model,
                                            batch_size=args.batch_size, max_turns=args.max_turns)
                generated_inputs = [p for p in files if p.startswith(".github/continuous-ai/")]
                if any(fnmatch.fnmatchcase(p, pattern) for p in generated_inputs
                       for pattern in args.source_glob):
                    raise ValueError("Source selection would include generated config/prompt; narrow source_globs")
                scope = scan_scope(root, args.source_glob, args.policy_file, args.batch_size)
            else:
                files = render_triage(args.ref, args.lane, args.default_branch, args.required_check)
            install.check_destinations(root, files)
            written = install.write_files(root, files) if args.write else []
            result = {"mode": "written" if args.write else "preview", "scope": scope,
                      "files": files, "written": written,
                      "next": "Review the diff and adoption brief. Commit via normal repository workflow. "
                              "No secret, remote setting, or workflow run was changed. Generated callers include recovery-only schedules."}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        # Do not echo git stderr or source contents into the diagnostic.
        print("Onboarding failed: " + (str(exc) if isinstance(exc, ValueError)
                                      else type(exc).__name__), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

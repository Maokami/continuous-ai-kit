"""Bounded quota cooldowns shared by scan and triage; never wait on a runner."""

import hashlib
import json
import os
import re
import time

MAX_DEFERRALS = 20
MAX_WAIT = 14 * 24 * 3600
MAX_BACKOFF = 24 * 3600


def validate(state):
    if state == {}:
        return
    base = {"status", "run_id"}
    if (not isinstance(state, dict) or state.get("status") not in {"ready", "waiting", "exhausted"}
            or not isinstance(state.get("run_id"), str)
            or not re.fullmatch(r"[0-9]{1,30}", state["run_id"])):
        raise ValueError("Invalid quota recovery state")
    if state["status"] == "ready":
        if set(state) != base:
            raise ValueError("Invalid ready recovery state")
        return
    if (set(state) != base | {"deferrals", "first_limited_at", "next_attempt_at"}
            or type(state["deferrals"]) is not int or not 1 <= state["deferrals"] <= MAX_DEFERRALS
            or any(type(state[k]) is not int or not 0 < state[k] < 10**10
                   for k in ("first_limited_at", "next_attempt_at"))
            or not state["first_limited_at"] < state["next_attempt_at"] <= state["first_limited_at"] + MAX_WAIT):
        raise ValueError("Invalid quota cooldown")


def gate(state, *, now=None, recovery_only=False, run_id=None):
    validate(state)
    now = int(time.time()) if now is None else now
    if run_id is not None and int(run_id) <= int(state.get("run_id", "0")):
        return {"run": "false", "reason": "already_recorded"}
    if state.get("status") in {"waiting", "exhausted"}:
        if state["status"] == "exhausted" or now >= state["first_limited_at"] + MAX_WAIT:
            return {"run": "false", "reason": "quota_recovery_exhausted"}
        if now < state["next_attempt_at"]:
            return {"run": "false", "reason": "waiting_for_quota",
                    "next_attempt_at": str(state["next_attempt_at"])}
        return {"run": "true", "reason": "quota_retry_due"}
    return {"run": "false" if recovery_only else "true", "reason": "no_quota_wait"}


def record(state, category, run_id, *, now=None, reset_at=None, scope=""):
    """Only fixed categories and bounded timestamps cross the model/write boundary."""
    validate(state)
    if not re.fullmatch(r"[0-9]{1,30}", run_id):
        raise ValueError("Invalid run ID")
    if int(run_id) <= int(state.get("run_id", "0")):
        return state
    if category != "usage_limit":
        return {"status": "ready", "run_id": run_id}
    now = int(time.time()) if now is None else now
    waiting = state.get("status") in {"waiting", "exhausted"}
    count = min(MAX_DEFERRALS, state["deferrals"] + 1 if waiting else 1)
    first = state["first_limited_at"] if waiting else now
    delay = min(MAX_BACKOFF, 3600 * 2 ** (count - 1))
    # Stable jitter spreads independent repos without additional provider calls.
    jitter = int(hashlib.sha256((scope + run_id).encode()).hexdigest()[:8], 16) % 901
    due = now + delay + jitter
    if type(reset_at) is int and now < reset_at < now + MAX_WAIT:
        due = max(now + 300, reset_at + 300) + jitter
    due = min(due, first + MAX_WAIT)
    exhausted = count >= MAX_DEFERRALS or now >= first + MAX_WAIT
    return {"status": "exhausted" if exhausted else "waiting", "run_id": run_id,
            "deferrals": count, "first_limited_at": first, "next_attempt_at": due}


def emit(outputs):
    print(json.dumps(outputs, sort_keys=True))
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
            for key, value in outputs.items():
                stream.write(f"{key}={value}\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write("Quota recovery: `" + json.dumps(outputs, sort_keys=True) + "`\n")


def triage_state(api, marker_id):
    marker = "<!-- cai-quota:triage-" + marker_id + ":v1 -->"
    title = "[Continuous AI] " + marker_id + " quota recovery"
    matches = []
    for page in range(1, 11):
        issues = api.request(f"/repos/{api.repo}/issues?state=all&per_page=100&page={page}")
        matches.extend(i for i in issues if "pull_request" not in i
                       and i.get("user", {}).get("login") == "github-actions[bot]"
                       and i.get("title") == title and (i.get("body") or "").startswith(marker + "\n"))
        if len(issues) < 100:
            break
    else:
        raise ValueError("Quota issue history exceeds limit")
    if len(matches) > 1:
        raise ValueError("Duplicate quota recovery issues")
    state = json.loads(matches[0]["body"].split("\n", 1)[1]) if matches else {}
    validate(state)
    return (matches[0]["number"] if matches else None), state, title, marker


def triage_update(api, marker_id, operation, *, category="", reset_at=None):
    number, state, title, marker = triage_state(api, marker_id)
    run_id = os.environ["GITHUB_RUN_ID"]
    if operation == "check":
        return gate(state, recovery_only=os.environ.get("RECOVERY_ONLY") == "true", run_id=run_id)
    new = {} if operation == "reset" else record(
        state, category, run_id, reset_at=reset_at, scope=api.repo + marker_id)
    # Keep successful/no-op triage runs free of extra issue writes.
    if new != state and (number is not None or category == "usage_limit"):
        body = marker + "\n" + json.dumps(new, indent=2)
        if number is None:
            api.request(f"/repos/{api.repo}/issues", {"title": title, "body": body})
        else:
            api.request(f"/repos/{api.repo}/issues/{number}", {"body": body}, "PATCH")
    return {"recovery_status": new.get("status", "ready")}

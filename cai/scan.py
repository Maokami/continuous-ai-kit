"""Reserve, validate, and publish a bounded source scan using a bot-owned issue ledger."""

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from cai.github import GitHub
from cai import config
from cai.common import bounded_text, digest_json, neutralize, quote_at_line, reject_secrets

TITLE = "[Continuous AI] Lean 개선 조사 상태"
MARKER = "<!-- lean-improvement-state:v1 -->"
REPORT = "<!-- lean-improvement-report:"
IDS = "<!-- lean-improvement-ids:"
OUT = Path(".improvement")
PROMPT = Path(".github/prompts/lean-improvement.md")
SCHEMA = Path(__file__).resolve().parents[1] / "schema.json"
CFG = {**config.DEFAULTS, "adapter": "lean", "model": "sonnet"}
CONFIG_PATH = None
KINDS = {"duplicate", "assumptions", "dependency", "tactics", "documentation"}


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def configure(path):
    global CFG, CONFIG_PATH, PROMPT, TITLE, MARKER, REPORT, IDS, OUT
    CFG = config.load(path)
    CONFIG_PATH = path
    PROMPT = Path(CFG["prompt_file"])
    OUT = Path(".continuous-ai")
    if CFG["legacy"] != "kuratowski-v1":
        TITLE = "[Continuous AI] " + CFG["lane"] + " state"
        MARKER = "<!-- cai-state:" + CFG["lane"] + ":v1 -->"
        REPORT = "<!-- cai-report:" + CFG["lane"] + ":"
        IDS = "<!-- cai-ids:" + CFG["lane"] + ":"
    revision = os.environ.get("CAI_TOOL_SHA", "")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("A pinned package revision is required")
    package = Path(__file__).resolve().parents[1]
    actual = subprocess.check_output(["git", "-C", str(package), "rev-parse", "HEAD"], text=True).strip()
    if actual != revision:
        raise ValueError("Package checkout differs from the executing workflow")
    if CFG["default_branch"] != os.environ.get("CAI_DEFAULT_BRANCH", "main"):
        raise ValueError("Caller and configuration default branches differ")


def source():
    texts = config.source_files(CFG)
    policy = sorted(set([CONFIG_PATH, str(PROMPT), *CFG["policy_files"]]))
    materials = {p: config.tracked_bytes(p).decode() for p in policy}
    digest = digest_json({"version": 1, "tool_sha": os.environ["CAI_TOOL_SHA"],
                          "schema": SCHEMA.read_text(), "policy": materials, "source": texts})
    files = list(texts)
    size = CFG["batch_size"]
    batches = [files[i:i + size] for i in range(0, len(files), size)]
    return texts, batches, digest


def all_issues(api):
    result = []
    for page in range(1, 11):
        items = api.request(f"/repos/{api.repo}/issues?state=all&per_page=100&page={page}")
        result.extend(items)
        if len(items) < 100:
            return result
    raise ValueError("이슈 이력을 끝까지 읽지 못했습니다.")


def owned(issue):
    return ("pull_request" not in issue
            and issue.get("user", {}).get("login") == "github-actions[bot]")


def load_state(issues):
    matches = [i for i in issues if owned(i) and i.get("title") == TITLE
               and (i.get("body") or "").startswith(MARKER + "\n")]
    if len(matches) > 1:
        raise ValueError("상태 이슈가 중복되었습니다.")
    if not matches:
        return None, {"version": 1, "batches": {}, "seen": []}
    issue = matches[0]
    state = json.loads(issue["body"].split("\n", 1)[1])
    if (set(state) != {"version", "batches", "seen"} or state["version"] != 1
            or not isinstance(state["batches"], dict) or not isinstance(state["seen"], list)
            or len(state["seen"]) > 300
            or any(not isinstance(k, str) or not re.fullmatch(r"[0-9a-f]{64}", k)
                   for k in state["seen"])):
        raise ValueError("상태 이슈 형식이 잘못되었습니다.")
    for key, entry in state["batches"].items():
        if (not re.fullmatch(r"[0-9]{1,3}", key) or not isinstance(entry, dict)
                or set(entry) != {"digest", "attempts", "status", "run_id"}
                or not re.fullmatch(r"[0-9a-f]{64}", entry["digest"])
                or type(entry["attempts"]) is not int or not 1 <= entry["attempts"] <= 2
                or entry["status"] not in {"pending", "success", "error"}
                or not re.fullmatch(r"[0-9]+", entry["run_id"])):
            raise ValueError("조사 이력이 잘못되었습니다.")
    return issue["number"], state


def save_state(api, number, state):
    body = MARKER + "\n" + json.dumps(state, ensure_ascii=False, indent=2)
    if len(body.encode()) > 50000:
        raise ValueError("상태가 커졌습니다. 유지관리자의 이력 정리가 필요합니다.")
    if number is None:
        return api.request(f"/repos/{api.repo}/issues", {"title": TITLE, "body": body})["number"]
    api.request(f"/repos/{api.repo}/issues/{number}", {"body": body}, "PATCH")
    return number


def select_batch(batches, state, digest, requested, retry):
    if requested == "auto":
        if retry:
            raise ValueError("재시도는 batch 번호를 지정해야 합니다.")
        options = sorted(range(len(batches)), key=lambda i: (
            int(state["batches"].get(str(i), {}).get("run_id", "0")), i))
    elif re.fullmatch(r"[0-9]{1,3}", requested) and int(requested) < len(batches):
        options = [int(requested)]
    else:
        raise ValueError("등록되지 않은 batch입니다.")
    for index in options:
        old = state["batches"].get(str(index), {})
        if old.get("digest") != digest:
            return index, 1
        if retry and old.get("status") != "success" and old["attempts"] < 2:
            return index, old["attempts"] + 1
    return None


def prepare(api):
    requested = os.environ.get("REVIEW_BATCH", "auto")
    retry = os.environ.get("RETRY_FAILED", "false")
    if retry not in {"true", "false"}:
        raise ValueError("retry 입력이 잘못되었습니다.")
    _, batches, digest = source()
    # Validate caller inputs before writing state.
    select_batch(batches, {"batches": {}}, digest, requested, retry == "true")
    if api.request(f"/repos/{api.repo}/git/ref/heads/{CFG['default_branch']}")["object"]["sha"] != git("rev-parse", "HEAD"):
        return {"run": "false"}
    issues = all_issues(api)
    number, state = load_state(issues)
    selected = select_batch(batches, state, digest, requested, retry == "true")
    if selected is None:
        return {"run": "false"}
    index, attempts = selected
    plan = {"sha": git("rev-parse", "HEAD"), "batch": index, "digest": digest,
            "required_files": batches[index], "run_id": os.environ["GITHUB_RUN_ID"],
            "requested_model": CFG["model"], "policy": "read-only-v1"}
    state["batches"][str(index)] = {
        "digest": digest, "attempts": attempts, "status": "pending", "run_id": plan["run_id"]}
    save_state(api, number, state)  # Reserve before spending model tokens; retries are explicit and bounded.
    if OUT.is_symlink() or any((OUT / name).is_symlink() for name in ("plan.json", "review.json")):
        raise ValueError("Output paths must not be symlinks")
    OUT.mkdir(exist_ok=True)
    (OUT / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    return {"run": "true", "schema": json.dumps(json.loads(SCHEMA.read_text()), separators=(",", ":")),
            "lane": CFG["lane"], "model": CFG["model"], "max_turns": str(CFG["max_turns"]),
            "prompt_file": str(PROMPT)}


text = bounded_text
safe = neutralize


def validate(raw, plan, conclusion, secrets=()):
    if conclusion != "success" or len(raw.encode()) > 40000:
        raise ValueError("조사가 실패했거나 결과가 너무 큽니다.")
    data = json.loads(raw)
    reject_secrets(data, secrets)
    if (not isinstance(data, dict)
            or set(data) != {"sha", "batch", "reviewed_files", "summary", "candidates"}
            or data["sha"] != plan["sha"] or type(data["batch"]) is not int
            or data["batch"] != plan["batch"]):
        raise ValueError("조사 대상과 결과가 다릅니다.")
    sources, batches, digest = source()
    if (plan["sha"] != git("rev-parse", "HEAD") or plan["digest"] != digest
            or plan["required_files"] != batches[plan["batch"]]):
        raise ValueError("입력 소스가 바뀌었습니다.")
    files = data["reviewed_files"]
    if (not isinstance(files, list) or not all(isinstance(p, str) and p in sources for p in files)
            or len(files) != len(set(files)) or not set(plan["required_files"]).issubset(files)):
        raise ValueError("검토 파일이 누락되었거나 허용되지 않았습니다.")
    text(data["summary"], 2000)
    candidates = data["candidates"]
    if not isinstance(candidates, list) or len(candidates) > 3:
        raise ValueError("후보는 최대 3개입니다.")
    ids = set()
    for c in candidates:
        if not isinstance(c, dict) or set(c) != {
                "path", "declaration", "kind", "risk", "title", "reason", "proposal", "evidence"}:
            raise ValueError("후보 형식이 잘못되었습니다.")
        if (c["path"] not in plan["required_files"] or c["kind"] not in KINDS
                or c["risk"] not in {"proof-only", "manual"}):
            raise ValueError("후보 범위가 잘못되었습니다.")
        for name, limit in (("declaration", 160), ("title", 100), ("reason", 2000), ("proposal", 2000)):
            text(c[name], limit)
        declaration = c["declaration"].split(".")[-1]
        if CFG["adapter"] == "lean":
            # Match a declaration's leaf, including explicitly qualified source names.
            # This is a lexical check, not Lean namespace resolution or type checking.
            if not re.search(r"\b(?:theorem|lemma|def|abbrev|structure|inductive)\s+"
                             + r"(?:[\w']+\.)*" + re.escape(declaration)
                             + r"(?=\s|[({:\[])", sources[c["path"]]):
                raise ValueError("The Lean declaration does not occur in the source")
        elif c["declaration"] not in sources[c["path"]] or c["risk"] != "manual":
            raise ValueError("Text scans require an exact source anchor and manual review")
        evidence = c["evidence"]
        if not isinstance(evidence, list) or not 1 <= len(evidence) <= 3:
            raise ValueError("소스 근거가 필요합니다.")
        for e in evidence:
            if (not isinstance(e, dict) or set(e) != {"path", "line", "quote"}
                    or e["path"] not in files or type(e["line"]) is not int or e["line"] < 1):
                raise ValueError("근거 위치가 잘못되었습니다.")
            quote_at_line(sources, e["path"], e["line"], e["quote"])
        key = candidate_id(c)
        if key in ids:
            raise ValueError("중복 후보입니다.")
        ids.add(key)
    return data


def candidate_id(c):
    return hashlib.sha256(json.dumps([c["path"], c["declaration"], c["kind"]]).encode()).hexdigest()


def posted_ids(issues):
    result = set()
    for i in issues:
        body = i.get("body") or ""
        if not owned(i) or not body.startswith(REPORT):
            continue
        lines = body.splitlines()
        if len(lines) < 2 or not lines[1].startswith(IDS) or not lines[1].endswith(" -->"):
            raise ValueError("게시 후보 ID를 복구하지 못했습니다.")
        ids = json.loads(lines[1][len(IDS):-4])
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 3
                or any(not isinstance(k, str) or not re.fullmatch(r"[0-9a-f]{64}", k) for k in ids)):
            raise ValueError("게시 후보 ID 형식이 잘못되었습니다.")
        result.update(ids)
    return result


def publish(api):
    plan = json.loads((OUT / "plan.json").read_text())
    data = validate((OUT / "review.json").read_text(), plan, "success")
    issues = all_issues(api)
    number, state = load_state(issues)
    entry = state["batches"].get(str(plan["batch"]), {})
    if (number is None or plan["run_id"] != os.environ["GITHUB_RUN_ID"]
            or entry.get("run_id") != plan["run_id"] or entry.get("digest") != plan["digest"]
            or entry.get("status") != "pending"):
        raise ValueError("현재 실행이 예약한 조사가 아닙니다.")
    if api.request(f"/repos/{api.repo}/git/ref/heads/{CFG['default_branch']}")["object"]["sha"] != plan["sha"]:
        raise ValueError("main이 바뀌었습니다. 오래된 결과는 게시하지 않습니다.")
    # Reconcile acknowledged report IDs before deciding which candidates are new.
    state["seen"] = sorted(set(state["seen"]) | posted_ids(issues))
    fresh = [c for c in data["candidates"] if candidate_id(c) not in state["seen"]]
    if len(state["seen"]) + len(fresh) > 300:
        raise ValueError("후보 이력을 유지관리자가 정리해야 합니다.")
    marker = REPORT + str(plan["batch"]) + ":" + plan["digest"] + ":" + plan["run_id"] + " -->"
    if fresh:
        body = marker + "\n" + IDS + json.dumps([candidate_id(c) for c in fresh]) + " -->\n"
        body += "AI가 제안한 미검증 후보입니다. 코드 수정·PR 생성은 수행하지 않았습니다.\n\n"
        body += f"소스: `{plan['sha']}` · batch {plan['batch']} · run {plan['run_id']}\n\n"
        body += safe(data["summary"]) + "\n"
        for c in fresh:
            body += "\n### " + safe(c["title"]) + "\n\n"
            body += safe(f"{c['path']} · {c['declaration']} · {c['kind']} · {c['risk']}") + "\n\n"
            body += safe(c["reason"]) + "\n\n" + safe(c["proposal"]) + "\n\n"
            for e in c["evidence"]:
                body += f"- [{safe(e['path'])}:{e['line']}](https://github.com/{api.repo}/blob/{plan['sha']}/{e['path']}#L{e['line']})\n"
        api.request(f"/repos/{api.repo}/issues", {
            "title": f"[Continuous AI] {CFG['lane']} batch {plan['batch']} ({len(fresh)})", "body": body})
    state["seen"] += [candidate_id(c) for c in fresh if candidate_id(c) not in state["seen"]]
    entry["status"] = "success"
    save_state(api, number, state)
    print(f"candidates={len(data['candidates'])} new={len(fresh)}")


def error(api):
    number, state = load_state(all_issues(api))
    if number is not None:
        for entry in state["batches"].values():
            if entry["run_id"] == os.environ["GITHUB_RUN_ID"] and entry["status"] == "pending":
                entry["status"] = "error"
        save_state(api, number, state)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("prepare", "validate", "publish", "error"))
    parser.add_argument("--config", default=".github/continuous-ai/scan.json")
    args = parser.parse_args()
    configure(args.config)
    if args.operation == "validate":
        plan = json.loads((OUT / "plan.json").read_text())
        data = validate(os.environ["REVIEW_JSON"], plan, os.environ["REVIEW_CONCLUSION"],
                        (os.environ.get("GITHUB_TOKEN"), os.environ.get("REVIEW_OAUTH_TOKEN")))
        (OUT / "review.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    elif args.operation == "prepare":
        outputs = prepare(GitHub())
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            for key, value in outputs.items():
                f.write(f"{key}={value}\n")
    elif args.operation == "publish":
        publish(GitHub())
    elif args.operation == "error":
        error(GitHub())
    else:
        raise ValueError("사용법: lean_improvement.py prepare|validate|publish|error")


if __name__ == "__main__":
    main()

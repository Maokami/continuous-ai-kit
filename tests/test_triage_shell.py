"""Shell and structure tests for .github/workflows/pr-triage.yml. No network calls.

The publish step runs under `bash -e` as on GitHub runners; this carries over
smarandache-wellin-lean's tests/test_pr_triage_shell.py (stale exit 3 becomes success).
Run: python3 -I -m unittest discover -s tests -p 'test_triage*.py' -v
"""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "pr-triage.yml"
TEXT = WORKFLOW.read_text(encoding="utf-8")
spec = importlib.util.spec_from_file_location("triage", ROOT / "cai" / "triage.py")
assert spec is not None and spec.loader is not None
T = importlib.util.module_from_spec(spec)
spec.loader.exec_module(T)

# The structured-output schema of the original SW workflow, kept byte-for-byte.
SW_SCHEMA = ('{"type":"object","properties":{"digest":{"type":"string"},"items":{"type":"array","items":'
             '{"type":"object","properties":{"number":{"type":"integer"},"priority":{"type":"string",'
             '"enum":["높음","보통","낮음"]},"next_action":{"type":"string"}},'
             '"required":["number","priority","next_action"]}}},"required":["digest","items"]}')


def jobs(text=TEXT):
    """Map job id -> job block text (two-space indented keys under `jobs:`)."""
    body = text.split("\njobs:\n", 1)[1]
    parts = re.split(r"(?m)^  ([A-Za-z0-9_-]+):\n", body)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts), 2)}


def step_script(job, name):
    """The dedented `run: |` block of the step with the given name."""
    lines = jobs()[job].splitlines()
    start = next(i for i, l in enumerate(lines) if l.strip() == f"- name: {name}")
    run = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "run: |")
    indent = len(lines[run]) - len(lines[run].lstrip())
    block = []
    for line in lines[run + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        block.append(line)
    return textwrap.dedent("\n".join(block)) + "\n"


def bash(script, cwd, env):
    return subprocess.run(["bash", "-e", "-c", script], cwd=cwd, env=env, capture_output=True, text=True)


def fake_executable(directory, name, body):
    path = Path(directory) / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


class Structure(unittest.TestCase):
    def test_reusable_only_with_explicit_inputs_and_named_secret(self):
        on = TEXT.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertRegex(on, r"^  workflow_call:\n")
        self.assertEqual(len(re.findall(r"(?m)^  \S", on)), 1)  # no other trigger
        for name in ("config", "default_branch"):
            self.assertRegex(on, rf"(?s)      {name}:\n.*?required: true")
        self.assertRegex(on, r"secrets:\n      claude_oauth_token:\n        required: true")
        self.assertIn("\npermissions: {}\n", TEXT)
        self.assertNotIn("secrets: inherit", TEXT)

    def test_actions_pinned_by_full_sha(self):
        uses = re.findall(r"(?m)^\s*(?:- )?uses: (\S+)", TEXT)
        self.assertTrue(uses)
        for ref in uses:
            self.assertRegex(ref, r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$", ref)

    def test_job_permissions_and_secret_scope(self):
        js = jobs()
        self.assertEqual(set(js), {"collect", "summarize", "publish"})
        def permissions(block):
            lines = block.split("    permissions:\n", 1)[1].splitlines()
            entries = []
            for line in lines:
                if not line.startswith("      ") or line.startswith("       "):
                    break
                entries.append(line.strip().split(": ", 1))
            return dict(entries)
        perms = {name: permissions(block) for name, block in js.items()}
        self.assertEqual(perms["collect"], {"contents": "read", "pull-requests": "read", "issues": "read",
                                            "checks": "read", "statuses": "read", "actions": "read"})
        self.assertEqual(perms["summarize"], {"contents": "read"})
        self.assertEqual(perms["publish"], {"contents": "read", "pull-requests": "write", "issues": "write",
                                            "checks": "read", "statuses": "read", "actions": "read"})
        self.assertNotIn("id-token", TEXT)
        self.assertEqual([n for n, b in js.items() if "secrets." in b], ["summarize"])

    def test_self_checkout_and_revision_check_in_every_job(self):
        for name, block in jobs().items():
            self.assertIn("repository: ${{ job.workflow_repository }}", block, name)
            self.assertIn("ref: ${{ job.workflow_sha }}", block, name)
            self.assertIn("path: .continuous-ai-kit", block, name)
            self.assertIn("- name: Verify package revision", block, name)
            self.assertEqual(block.count("persist-credentials: false"), block.count("actions/checkout@"), name)
            self.assertIn("ref: ${{ github.sha }}", block, name)

    def test_artifacts_are_per_attempt(self):
        names = re.findall(r"(?m)name: (cai-.*)$", TEXT)
        self.assertEqual(len(names), 5)  # 2 uploads, 3 downloads
        for n in names:
            self.assertTrue(n.endswith("-${{ github.run_attempt }}"), n)

    def test_model_is_read_only_and_schema_is_unchanged(self):
        args = jobs()["summarize"].split("claude_args: >-\n", 1)[1]
        for flag in ("--tools Read\n", "--allowedTools Read\n", "--strict-mcp-config", "--restricted",
                     '--setting-sources user', "--disable-slash-commands", "'Read(./.git/**)'",
                     "'Read(./.continuous-ai-kit/.git/**)'", "'Read(//proc/**)'", "--max-turns 5",
                     "--model claude-sonnet-5-5"):
            self.assertIn(flag, args)
        self.assertIn(f"--json-schema '{SW_SCHEMA}'", args)

    def test_inputs_never_reach_run_blocks_directly(self):
        for match in re.finditer(r"(?m)^(\s*)run: \|\n((?:\1  .*\n|\n)*)", TEXT):
            self.assertNotIn("${{", match.group(2))

    def test_publish_condition_matches_original(self):
        cond = jobs()["publish"].split("if: >-\n", 1)[1].split("\n    runs-on", 1)[0]
        self.assertEqual(" ".join(cond.split()),
                         "always() && needs.collect.result == 'success' && "
                         "(needs.collect.outputs.facts_changed == 'true' || "
                         "needs.collect.outputs.label_drift == 'true') && "
                         "(needs.summarize.result == 'success' || needs.summarize.result == 'skipped')")
        self.assertIn("if: needs.collect.outputs.facts_changed == 'true' && needs.collect.outputs.pr_count != '0'",
                      jobs()["summarize"])


class PublishStep(unittest.TestCase):
    """The real publish step under `bash -e` with a fake python3 recording its arguments."""

    def run_publish(self, child_exit, with_ai):
        script = step_script("publish", "Publish report and labels")
        with tempfile.TemporaryDirectory() as tmp:
            bindir = Path(tmp) / "bin"
            bindir.mkdir()
            fake_executable(bindir, "python3", f'printf "%s\\n" "$@" > "{tmp}/args"\nexit {child_exit}\n')
            work = Path(tmp) / "work"
            (work / ".continuous-ai" / "triage").mkdir(parents=True)
            (work / ".continuous-ai" / "triage" / "snapshot.json").write_text("{}")
            if with_ai:
                (work / ".continuous-ai" / "triage" / "ai.json").write_text("{}")
            env = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ["PATH"],
                       GITHUB_REPOSITORY="owner/repo", RUN_URL="test-run", CAI_CONFIG="triage.json")
            result = bash(script, work, env)
            args = (Path(tmp) / "args").read_text().splitlines()
        return result, args

    def test_exit_codes_under_runner_errexit(self):
        for child_exit, expected in ((0, 0), (3, 0), (7, 7), (1, 1)):
            with self.subTest(child_exit=child_exit):
                result, _ = self.run_publish(child_exit, with_ai=True)
                self.assertEqual(result.returncode, expected, result.stderr)
                if child_exit == 3:
                    self.assertIn("낡은 스냅샷이라 게시하지 않았다", result.stdout)

    def test_arguments_with_and_without_ai_output(self):
        _, with_ai = self.run_publish(0, with_ai=True)
        self.assertEqual(with_ai, ["-I", "./.continuous-ai-kit/cai/triage.py", "publish", "--repo", "owner/repo",
                                   "--config", "triage.json", "--snapshot", ".continuous-ai/triage/snapshot.json",
                                   "--run-url", "test-run", "--ai", ".continuous-ai/triage/ai.json"])
        _, without = self.run_publish(0, with_ai=False)
        self.assertEqual(without, with_ai[:-2])


class RevisionStep(unittest.TestCase):
    def test_package_revision_must_match(self):
        script = step_script("collect", "Verify package revision")
        sha = "a" * 40
        with tempfile.TemporaryDirectory() as tmp:
            bindir = Path(tmp) / "bin"
            bindir.mkdir()
            fake_executable(bindir, "git", f'echo {sha}\n')
            env = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ["PATH"])
            self.assertEqual(bash(script, tmp, dict(env, CAI_TOOL_SHA=sha)).returncode, 0)
            self.assertNotEqual(bash(script, tmp, dict(env, CAI_TOOL_SHA="b" * 40)).returncode, 0)
            self.assertNotEqual(bash(script, tmp, dict(env, CAI_TOOL_SHA="main")).returncode, 0)
        self.assertEqual(script, step_script("summarize", "Verify package revision"))
        self.assertEqual(script, step_script("publish", "Verify package revision"))


class VerifyOutputStep(unittest.TestCase):
    """The real summarize verification step with the real cai/triage.py and a collected snapshot."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        kit = self.work / ".continuous-ai-kit" / "cai"
        kit.mkdir(parents=True)
        shutil.copyfile(ROOT / "cai" / "triage.py", kit / "triage.py")
        (self.work / ".continuous-ai" / "triage").mkdir(parents=True)

        class Fake:
            repo = "o/r"

            def list_prs(self):
                return [{"number": 2, "title": "PR 2", "url": "u", "headRefOid": "e4d", "isDraft": False,
                         "mergeable": "MERGEABLE", "mergeStateStatus": "BLOCKED",
                         "reviewDecision": "REVIEW_REQUIRED", "author": {"login": "u"}, "labels": [],
                         "statusCheckRollup": []}]

            def list_report_issues(self):
                return []
        self.snapshot = T.collect(Fake())
        (self.work / ".continuous-ai" / "triage" / "snapshot.json").write_text(json.dumps(self.snapshot))
        self.script = step_script("summarize", "Verify triage output")

    def run_step(self, out, oauth="oauth-secret-value"):
        env = dict(os.environ, OUT=out, REVIEW_GITHUB_TOKEN="ghs-test-token-value", REVIEW_OAUTH_TOKEN=oauth,
                   PYTHONDONTWRITEBYTECODE="1")
        return bash(self.script, self.work, env)

    def ai(self, **override):
        data = {"digest": self.snapshot["digest"],
                "items": [{"number": 2, "priority": "보통", "next_action": "확인"}]}
        data.update(override)
        return json.dumps(data, ensure_ascii=False)

    def test_valid_output_passes(self):
        result = self.run_step(self.ai())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.work / ".continuous-ai" / "triage" / "ai.json").exists())

    def test_invalid_output_fails(self):
        for out in (self.ai(items=[]), self.ai(digest="0" * 64), "", "not json"):
            with self.subTest(out=out[:20]):
                self.assertNotEqual(self.run_step(out).returncode, 0)

    def test_credential_echo_fails_even_when_escaped(self):
        secret = "oauth-secret-value"
        escaped = "".join("\\u%04x" % ord(c) for c in secret)  # JSON escapes in the raw output text
        out = self.ai(items=[{"number": 2, "priority": "보통", "next_action": "x PLACEHOLDER"}])
        out = out.replace("PLACEHOLDER", escaped)
        self.assertNotIn(secret, out)
        self.assertNotEqual(self.run_step(out, oauth=secret).returncode, 0)
        self.assertEqual(self.run_step(self.ai(), oauth=secret).returncode, 0)


if __name__ == "__main__":
    unittest.main()
